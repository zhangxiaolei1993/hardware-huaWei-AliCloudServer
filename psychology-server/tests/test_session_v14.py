"""v1.4 P0-1 / P0-2 / M1 验收测试。

覆盖：旧客户端兼容、N1 保存与一对多、创建幂等、类型白名单、
来源系统规则、锚点与补传截止、能力门控、aborted 状态、旧流程回归。
"""
from typing import Dict

from sqlalchemy.orm import Session as DBSession

DEVICE_ID = "atlas_v14"
STATE: Dict[str, str] = {}


def _query_headers() -> dict:
    from app.core.config import get_settings

    return {"X-Api-Key": get_settings().query_api_key}


def _headers(token: str) -> dict:
    return {"X-Device-Id": DEVICE_ID, "X-Device-Token": token}


def _ensure_device_online(client) -> dict:
    if "token" not in STATE:
        resp = client.post(
            "/api/v1/devices/register",
            json={
                "device_id": DEVICE_ID,
                "device_name": "v1.4 验收设备",
                "device_type": "edge_ai_device",
                "manufacturer": "Huawei",
                "model": "Atlas 200I DK A2",
                "capabilities": ["emotion"],
            },
        )
        assert resp.status_code == 200, resp.text
        STATE["token"] = resp.json()["device_token"]
    resp = client.post(
        f"/api/v1/devices/{DEVICE_ID}/heartbeat", json={}, headers=_headers(STATE["token"])
    )
    assert resp.status_code == 200, resp.text
    return _headers(STATE["token"])


def _create(client, **payload):
    body = {"device_id": DEVICE_ID, "session_type": "emotion"}
    body.update(payload)
    return client.post("/api/v1/sessions", json=body, headers=_ensure_device_online(client))


# ---------- 1. 旧客户端不传新字段仍可创建 ----------
def test_legacy_client_without_new_fields(client):
    resp = _create(client)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["created"] is True
    assert data["source_system"] == "standalone"
    assert data["external_session_id"] is None
    assert data["status"] == "created"
    assert data["anchor"]["source"] == "server"
    assert data["anchor"]["uncertainty_ms"] == 5000
    assert data["late_upload_deadline"] is not None
    assert data["related_session_ids"] == []

    detail = client.get(
        f"/api/v1/sessions/{data['session_id']}", headers=_query_headers()
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["source_system"] == "standalone"
    assert body["external_session_id"] is None
    assert body["anchor"]["utc_at"] is not None


# ---------- 2. 新客户端携带 N1 ----------
def test_new_client_saves_external_session_id(client):
    resp = _create(
        client,
        source_system="xin_tan_tan",
        external_session_id="1742",
        client_request_id="req-n1-001",
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["source_system"] == "xin_tan_tan"
    assert data["external_session_id"] == "1742"
    STATE["s1"] = data["session_id"]

    detail = client.get(
        f"/api/v1/sessions/{data['session_id']}", headers=_query_headers()
    ).json()
    assert detail["external_session_id"] == "1742"


# ---------- 3. 同一 N1 关联多个 S1 ----------
def test_same_external_id_allows_multiple_sessions(client):
    resp = _create(
        client,
        source_system="xin_tan_tan",
        external_session_id="1742",
        client_request_id="req-n1-002",
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["session_id"] != STATE["s1"]
    assert STATE["s1"] in data["related_session_ids"]
    STATE["s2"] = data["session_id"]

    old = client.get(
        f"/api/v1/sessions/{STATE['s1']}", headers=_query_headers()
    ).json()
    assert old["external_session_id"] == data["external_session_id"] == "1742"


# ---------- 4. 相同 source_system + client_request_id 幂等 ----------
def test_create_idempotent_by_client_request_id(client, db_session: DBSession):
    payload = dict(
        source_system="xin_tan_tan",
        external_session_id="1742",
        client_request_id="req-n1-002",
    )
    resp = _create(client, **payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] is False
    assert data["session_id"] == STATE["s2"]

    from app.models.session import SessionModel

    count = (
        db_session.query(SessionModel)
        .filter(
            SessionModel.source_system == "xin_tan_tan",
            SessionModel.client_request_id == "req-n1-002",
        )
        .count()
    )
    assert count == 1

    # 同一 client_request_id 被另一设备复用时冲突
    resp = client.post(
        "/api/v1/devices/register",
        json={"device_id": "atlas_v14_b", "device_name": "第二台", "device_type": "edge_ai_device"},
    )
    token_b = resp.json()["device_token"]
    client.post(
        "/api/v1/devices/atlas_v14_b/heartbeat",
        json={},
        headers={"X-Device-Id": "atlas_v14_b", "X-Device-Token": token_b},
    )
    other = client.post(
        "/api/v1/sessions",
        json={
            "device_id": "atlas_v14_b",
            "session_type": "emotion",
            "source_system": "xin_tan_tan",
            "external_session_id": "9999",
            "client_request_id": "req-n1-002",
        },
        headers={"X-Device-Id": "atlas_v14_b", "X-Device-Token": token_b},
    )
    assert other.status_code == 409
    assert "CLIENT_REQUEST_ID_CONFLICT" in other.json()["message"]


# ---------- 5. emotion 原有功能正常 ----------
def test_emotion_flow_still_works(client):
    resp = _create(client)
    assert resp.status_code == 201
    sid = resp.json()["session_id"]

    resp = client.post(
        f"/api/v1/sessions/{sid}/status", json={"status": "running"}
    )
    assert resp.status_code == 200

    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": "req-v14-emotion-1",
            "session_meta": {"video_frames": 100, "face_detected_frames": 80},
            "timeline": [
                {"relative_seconds": 0.5, "expression": "neutral", "confidence": 0.8},
                {"relative_seconds": 1.0, "expression": "happiness", "confidence": 0.9},
            ],
        },
        headers=_headers(STATE["token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["valid_expression_seconds"] == 0.5

    result = client.get(
        f"/api/v1/emotion/sessions/{sid}/result", headers=_query_headers()
    )
    assert result.status_code == 200
    assert result.json()["result"]["face_coverage_percent"] == 80.0


# ---------- 6. multimodal / assessment / custom 规则 ----------
def test_multimodal_and_assessment_supported(client):
    for session_type in ("multimodal", "assessment", "physiological", "speech"):
        resp = _create(client, session_type=session_type)
        assert resp.status_code == 201, (session_type, resp.text)
        assert resp.json()["session_type"] == session_type


def test_custom_source_system_forbidden(client):
    resp = _create(client, source_system="custom")
    assert resp.status_code == 403
    assert "SOURCE_SYSTEM_NOT_ALLOWED" in resp.json()["message"]


# ---------- 7. 非法类型 / 规则拒绝 ----------
def test_invalid_session_type_rejected(client):
    resp = _create(client, session_type="heart_rate")
    assert resp.status_code == 422


def test_invalid_source_system_rejected(client):
    resp = _create(client, source_system="legacy_app")
    assert resp.status_code == 422


def test_standalone_rejects_external_session_id(client):
    resp = _create(client, external_session_id="1742")
    assert resp.status_code == 422
    assert "INVALID_EXTERNAL_SESSION_ID" in resp.json()["message"]


def test_xin_tan_tan_requires_external_session_id(client):
    resp = _create(client, source_system="xin_tan_tan")
    assert resp.status_code == 422
    assert "EXTERNAL_SESSION_ID_REQUIRED" in resp.json()["message"]


def test_fallback_external_session_id_rejected(client):
    resp = _create(
        client,
        source_system="xin_tan_tan",
        external_session_id="unknown",
        client_request_id="req-fallback",
    )
    assert resp.status_code == 422


# ---------- 能力门控（§6.7 G1） ----------
def test_active_session_gating_by_capability(client):
    # 设备 capabilities=["emotion"]，multimodal 会话不下发
    resp = _create(client, session_type="multimodal")
    assert resp.status_code == 201
    multi_sid = resp.json()["session_id"]

    active = client.get(f"/api/v1/devices/{DEVICE_ID}/active-session")
    assert active.status_code == 200
    assert active.json()["session_id"] is None
    assert active.json()["poll_interval_seconds"] == 3
    assert active.json()["server_time"] is not None

    client.post(f"/api/v1/sessions/{multi_sid}/status", json={"status": "completed"})

    resp = _create(client, session_type="emotion")
    emotion_sid = resp.json()["session_id"]
    active = client.get(f"/api/v1/devices/{DEVICE_ID}/active-session").json()
    assert active["session_id"] == emotion_sid
    assert active["session_type"] == "emotion"
    assert active["anchor"]["source"] == "server"
    client.post(f"/api/v1/sessions/{emotion_sid}/status", json={"status": "completed"})


# ---------- aborted 状态 ----------
def test_aborted_status(client):
    sid = _create(client).json()["session_id"]
    resp = client.post(f"/api/v1/sessions/{sid}/status", json={"status": "aborted"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "aborted"
    assert data["ended_reason"] == "user cancelled"
    assert data["ended_at"] is not None
