"""P0-9 / M7 验收：受试者脱敏标识。

- 传 external_user_id → subject_ref = HMAC(SALT, source:external_user_id)[:16]；
- 不传但传 user_id → 回退计算并标记 deprecated_identity_used；
- 两者都不传 → subject_ref 为 None。
"""
from app.core.config import get_settings
from app.db.database import SessionLocal
from app.models.emotion_record import EmotionRecord
from app.services.session_service import subject_ref_for

DEV = "atlas_subject_ref"
STATE: dict = {}

QH = {"X-Api-Key": get_settings().query_api_key}


def _online(client) -> dict:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": DEV,
            "device_name": "脱敏标识测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    STATE["token"] = resp.json()["device_token"]
    headers = {"X-Device-Id": DEV, "X-Device-Token": STATE["token"]}
    assert (
        client.post(f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=headers).status_code
        == 200
    )
    return headers


def _create(client, headers, **extra):
    payload = {"device_id": DEV, "session_type": "emotion"}
    payload.update(extra)
    return client.post("/api/v1/sessions", json=payload, headers=headers)


def test_external_user_id_yields_hmac_subject_ref(client):
    headers = _online(client)
    resp = _create(
        client,
        headers,
        source_system="xin_tan_tan",
        external_session_id="N1-1001",
        external_user_id="user-42",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    expected = subject_ref_for("xin_tan_tan", "user-42")
    assert body["subject_ref"] == expected
    assert len(body["subject_ref"]) == 16
    assert body["deprecated_identity_used"] is False

    detail = client.get(f"/api/v1/sessions/{body['session_id']}", headers=QH).json()
    assert detail["subject_ref"] == expected
    assert detail["deprecated_identity_used"] is False


def test_user_id_fallback_is_marked(client):
    headers = _online(client)
    resp = _create(client, headers, user_id="legacy-user")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["deprecated_identity_used"] is True
    assert body["subject_ref"] == subject_ref_for("standalone", "legacy-user")


def test_no_identity_no_subject_ref(client):
    headers = _online(client)
    resp = _create(client, headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["subject_ref"] is None
    assert resp.json()["deprecated_identity_used"] is False


def test_subject_ref_is_stable_and_source_scoped(client):
    assert subject_ref_for("xin_tan_tan", "u1") == subject_ref_for("xin_tan_tan", "u1")
    assert subject_ref_for("xin_tan_tan", "u1") != subject_ref_for("standalone", "u1")


def test_emotion_records_store_device_and_stream(client):
    """M6 补齐：新记录写入 device_id 与 stream_id。"""
    headers = _online(client)
    sid = _create(client, headers).json()["session_id"]
    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": "subject-ref-batch-1",
            "timeline": [
                {"relative_seconds": 0.0, "expression": "neutral", "confidence": 0.9}
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    db = SessionLocal()
    try:
        record = (
            db.query(EmotionRecord).filter(EmotionRecord.session_id == sid).one()
        )
        assert record.device_id == DEV
        assert record.stream_id == "facial_expression"
    finally:
        db.close()
