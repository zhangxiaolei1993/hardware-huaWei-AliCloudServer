"""科研导出验收（Q-5/Q-6/Q-7 / AC-35）。

覆盖：
- manifest 返回锚点、数据流、条数与完整性校验，并声明派生时间 `estimated`
- 导出物含派生 `event_timestamp` 与 `quality_info.derived_time`
- **库内 `quality` 与 `quality_info` 不被修改**（AC-35 硬规则）
- 无 `X-Api-Key` → 401；未知 export_id → 404
"""
import uuid

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.models.emotion_record import EmotionRecord

QH = {"X-Api-Key": get_settings().query_api_key}

DEV = "atlas_export_dev"
STATE: dict = {}

TIMELINE = [
    {"relative_seconds": 0.0, "expression": "neutral", "confidence": 0.9},
    {"relative_seconds": 0.5, "expression": "happiness", "confidence": 0.8},
    {"relative_seconds": 1.0, "expression": "neutral", "confidence": 0.7},
]


def _headers() -> dict:
    return {"X-Device-Id": DEV, "X-Device-Token": STATE["token"]}


def _online(client) -> None:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": DEV,
            "device_name": "导出测试设备",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    STATE["token"] = resp.json()["device_token"]
    assert (
        client.post(f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=_headers()).status_code
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


def _upload(client, sid: str) -> None:
    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={"client_request_id": str(uuid.uuid4()), "timeline": TIMELINE},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text


def _db_quality(sid: str):
    db = SessionLocal()
    try:
        rows = (
            db.query(EmotionRecord)
            .filter(EmotionRecord.session_id == sid)
            .order_by(EmotionRecord.relative_seconds.asc())
            .all()
        )
        return [(r.quality, dict(r.quality_info or {})) for r in rows]
    finally:
        db.close()


def test_manifest_declares_estimated_derived_time(client):
    _online(client)
    sid = _session(client)
    _upload(client, sid)

    resp = client.get(f"/api/v1/sessions/{sid}/manifest", headers=QH)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["session_id"] == sid
    assert body["counts"]["emotion_records"] == 3
    assert body["streams"][0]["stream_id"] == "facial_expression"
    assert body["streams"][0]["record_count"] == 3
    assert body["derived_time"]["estimated"] is True
    assert body["derived_time"]["anchor_uncertainty_ms"] == get_settings().anchor_uncertainty_ms
    assert body["integrity"]["emotion_records_sha256"]
    assert body["protocol_version"] == "1.0"

    # 清单查询本身不得改库
    assert all(q == "legacy" and "derived_time" not in info for q, info in _db_quality(sid))


def test_manifest_requires_api_key(client):
    sid = _session(client)
    assert client.get(f"/api/v1/sessions/{sid}/manifest").status_code == 401


def test_json_export_has_derived_timestamp_without_touching_db(client):
    sid = _session(client)
    _upload(client, sid)
    before = _db_quality(sid)

    resp = client.post(
        f"/api/v1/sessions/{sid}/exports", json={"format": "json"}, headers=QH
    )
    assert resp.status_code == 201, resp.text
    meta = resp.json()
    assert meta["record_count"] == 3
    assert meta["status"] == "completed"
    assert meta["derived_time"]["estimated"] is True

    download = client.get(meta["download_url"], headers=QH)
    assert download.status_code == 200
    payload = download.json()

    records = payload["records"]
    assert len(records) == 3
    first = records[0]
    assert first["event_timestamp"].endswith("Z")
    assert first["event_timestamp_estimated"] is True
    assert first["quality"] == "legacy"
    derived = first["quality_info"]["derived_time"]
    assert derived["source"] == "server_anchor"
    assert derived["method"] == "anchor_plus_relative"
    assert derived["anchor_uncertainty_ms"] == get_settings().anchor_uncertainty_ms

    # AC-35：库内 quality 与 quality_info 完全未变
    assert _db_quality(sid) == before


def test_csv_export_and_time_window(client):
    sid = _session(client)
    _upload(client, sid)

    resp = client.post(
        f"/api/v1/sessions/{sid}/exports",
        json={"format": "csv", "from": 0.4, "to": 0.6},
        headers=QH,
    )
    assert resp.status_code == 201, resp.text
    meta = resp.json()
    assert meta["record_count"] == 1

    download = client.get(meta["download_url"], headers=QH)
    assert download.status_code == 200
    lines = download.text.strip().splitlines()
    assert lines[0].startswith("relative_seconds,event_timestamp,")
    assert len(lines) == 2
    assert "derived_time_source" not in lines[1] or "server_anchor" in lines[1]


def test_export_status_and_unknown_id(client):
    sid = _session(client)
    _upload(client, sid)
    meta = client.post(
        f"/api/v1/sessions/{sid}/exports", json={"format": "json"}, headers=QH
    ).json()

    status_resp = client.get(f"/api/v1/exports/{meta['export_id']}", headers=QH)
    assert status_resp.status_code == 200
    assert status_resp.json()["export_id"] == meta["export_id"]

    assert client.get("/api/v1/exports/exp_doesnotexist", headers=QH).status_code == 404
    assert client.get(f"/api/v1/exports/{meta['export_id']}").status_code == 401
