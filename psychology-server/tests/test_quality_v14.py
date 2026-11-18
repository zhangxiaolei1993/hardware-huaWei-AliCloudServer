"""阶段 5 验收：记录质量模型（P0-7 / M6）与分级兼容接收（P0-14）。

对应 AC-16 ~ AC-21：
- AC-16 旧固件（无 stream_id / 无协议头）→ 200 且 quality=legacy
- AC-17 新客户端缺 device_timestamp / monotonic_ms → quality=missing
- AC-18 server_received_at 不参与质量判定
- AC-20 默认流补全带 stream_defaulted 标记与 X-Compat-Warning 头
- AC-21 客户端伪造 quality / quality_info 一律忽略
"""
import uuid

from app.db.database import SessionLocal
from app.models.emotion_record import EmotionRecord

DEV = "atlas_quality_dev"
STATE: dict = {}

V1_TIMELINE = [
    {"relative_seconds": 0.0, "expression": "neutral", "confidence": 0.9},
    {"relative_seconds": 0.5, "expression": "happiness", "confidence": 0.8},
]


def _headers() -> dict:
    return {"X-Device-Id": DEV, "X-Device-Token": STATE["token"]}


def _online(client) -> None:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": DEV,
            "device_name": "质量模型测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    STATE["token"] = resp.json()["device_token"]
    assert (
        client.post(
            f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=_headers()
        ).status_code
        == 200
    )


def _session(client) -> str:
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "session_type": "emotion"},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _upload(client, sid: str, body: dict, headers: dict | None = None):
    payload = {"client_request_id": str(uuid.uuid4()), "timeline": V1_TIMELINE}
    payload.update(body)
    return client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json=payload,
        headers=headers or _headers(),
    )


def _records(sid: str):
    db = SessionLocal()
    try:
        return (
            db.query(EmotionRecord)
            .filter(EmotionRecord.session_id == sid)
            .order_by(EmotionRecord.relative_seconds.asc())
            .all()
        )
    finally:
        db.close()


def test_01_legacy_firmware_is_accepted_as_legacy(client):
    """AC-16 / AC-20：旧固件三字段请求 → 200、quality=legacy、兼容告警头。"""
    _online(client)
    sid = _session(client)
    resp = _upload(client, sid, {})
    assert resp.status_code == 200, resp.text
    assert resp.json()["duplicate"] is False

    # 分级兼容响应头（AC-19：服务器接受版本列表固定）
    assert resp.headers["X-Protocol-Accepted"] == "1.0,2.0"
    assert resp.headers["X-Protocol-Version"] == "1.0"
    warning = resp.headers["X-Compat-Warning"]
    assert "stream_defaulted" in warning
    assert "protocol_inferred" in warning

    records = _records(sid)
    assert len(records) == 2
    assert all(r.quality == "legacy" for r in records)
    assert all(r.protocol_version == "1.0" for r in records)
    info = records[0].quality_info
    assert info["stream_id"] == "facial_expression"
    assert info["stream_source"] == "server_default"
    assert info["clock_sync_status"] == "unverified"
    assert "stream_defaulted" in info["flags"]


def test_02_client_supplied_quality_is_ignored(client):
    """AC-21：客户端伪造 quality/quality_info 一律忽略，仍按服务器推导。"""
    sid = _session(client)
    resp = _upload(
        client,
        sid,
        {
            "quality": "device",
            "quality_info": {"flags": ["fake"], "clock_sync_status": "synced"},
        },
    )
    assert resp.status_code == 200, resp.text
    records = _records(sid)
    assert all(r.quality == "legacy" for r in records)
    assert "fake" not in records[0].quality_info["flags"]


def test_03_v2_missing_device_timestamp_is_missing(client):
    """AC-17：声明 2.0 + stream_id 但无时间字段 → quality=missing。"""
    sid = _session(client)
    resp = _upload(
        client,
        sid,
        {"stream_id": "facial_expression", "protocol_version": "2.0"},
        headers={**_headers(), "X-Protocol-Version": "2.0"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Protocol-Accepted"] == "1.0,2.0"
    assert resp.headers["X-Protocol-Version"] == "2.0"
    assert "stream_defaulted" not in resp.headers.get("X-Compat-Warning", "")

    records = _records(sid)
    assert all(r.quality == "missing" for r in records)
    assert all(r.protocol_version == "2.0" for r in records)


def test_04_v2_with_monotonic_is_estimated_not_device(client):
    """P0 不产出 device：有 monotonic_ms 但时钟未校验 → estimated。"""
    sid = _session(client)
    resp = _upload(
        client,
        sid,
        {
            "stream_id": "facial_expression",
            "protocol_version": "2.0",
            "monotonic_ms": 120000,
        },
        headers={**_headers(), "X-Protocol-Version": "2.0"},
    )
    assert resp.status_code == 200, resp.text
    records = _records(sid)
    assert all(r.quality == "estimated" for r in records)
    assert all(r.monotonic_ms == 120000 for r in records)


def test_05_server_received_at_not_used_for_quality(client):
    """AC-18：server_received_at 只记录，不参与质量判定。"""
    sid = _session(client)
    resp = _upload(client, sid, {})
    assert resp.status_code == 200, resp.text
    records = _records(sid)
    assert all("server_received_at" in r.quality_info for r in records)
    # 接收时间全新，但质量依旧是 legacy，而非 device
    assert all(r.quality == "legacy" for r in records)


def test_06_pre_anchor_and_out_of_range_flags(client):
    """锚点前 -5~0 秒标记 pre_anchor；超上界标记 out_of_range。"""
    sid = _session(client)
    timeline = [
        {"relative_seconds": -1.0, "expression": "neutral", "confidence": 0.9},
        {"relative_seconds": 9_999_999.0, "expression": "neutral", "confidence": 0.9},
    ]
    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={"client_request_id": str(uuid.uuid4()), "timeline": timeline},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    records = _records(sid)
    assert "pre_anchor" in records[0].quality_info["flags"]
    assert "out_of_range" in records[1].quality_info["flags"]


def test_07_v1_upload_path_unchanged(client):
    """v1 上传路径冻结：仍是 /api/v1/emotion/sessions/{id}/data。"""
    sid = _session(client)
    resp = _upload(client, sid, {})
    assert resp.status_code == 200
    assert set(resp.json().keys()) >= {"session_id", "session_status", "duplicate", "result"}
