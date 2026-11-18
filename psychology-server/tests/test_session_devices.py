"""阶段 4 验收：会话-设备多对多绑定（P0-6 / M5、AC-13）。"""
from app.db.database import SessionLocal
from app.models.session_device import SessionDevice
from app.models.upload_batch import UploadBatch

DEV_A = "atlas_bind_a"
DEV_B = "atlas_bind_b"
DEV_C = "atlas_bind_c"
STATE: dict = {}


def _register(client, dev: str) -> dict:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": dev,
            "device_name": f"绑定测试-{dev}",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    headers = {"X-Device-Id": dev, "X-Device-Token": resp.json()["device_token"]}
    client.post(f"/api/v1/devices/{dev}/heartbeat", json={}, headers=headers)
    return headers


def test_01_session_auto_binds_primary_device(client):
    headers_a = _register(client, DEV_A)
    _register(client, DEV_B)
    _register(client, DEV_C)
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV_A, "session_type": "emotion"},
        headers=headers_a,
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    STATE["sid"] = sid
    STATE["headers_a"] = headers_a

    db = SessionLocal()
    try:
        binding = (
            db.query(SessionDevice)
            .filter(
                SessionDevice.session_id == sid,
                SessionDevice.device_id == DEV_A,
            )
            .one()
        )
        assert binding.role == "primary"
        assert binding.status == "bound"
    finally:
        db.close()


def test_02_bind_auxiliary_device_idempotent(client):
    sid = STATE["sid"]
    resp = client.post(
        f"/api/v1/sessions/{sid}/devices",
        json={"device_id": DEV_B, "role": "auxiliary"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is True
    assert resp.json()["role"] == "auxiliary"
    assert resp.json()["status"] == "bound"

    again = client.post(
        f"/api/v1/sessions/{sid}/devices",
        json={"device_id": DEV_B, "role": "auxiliary"},
    )
    assert again.status_code == 200
    assert again.json()["created"] is False


def test_03_bind_unknown_device_404(client):
    resp = client.post(
        f"/api/v1/sessions/{STATE['sid']}/devices",
        json={"device_id": "no_such_device"},
    )
    assert resp.status_code == 404


def test_04_bind_unknown_session_404(client):
    resp = client.post(
        "/api/v1/sessions/no-such-session/devices",
        json={"device_id": DEV_B},
    )
    assert resp.status_code == 404


def test_05_invalid_role_422(client):
    resp = client.post(
        f"/api/v1/sessions/{STATE['sid']}/devices",
        json={"device_id": DEV_B, "role": "owner"},
    )
    assert resp.status_code == 422


def test_06_auxiliary_device_can_upload(client):
    """AC-13：同一会话下辅助设备也能上传，批次按 device_id 可区分。"""
    sid = STATE["sid"]
    token_b = client.post(
        "/api/v1/devices/register",
        json={"device_id": DEV_B, "device_name": "x", "device_type": "edge_ai_device"},
    ).json()["device_token"]
    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": "aux-batch-0002",
            "session_meta": {"video_frames": 100, "face_detected_frames": 90},
            "timeline": [
                {"relative_seconds": 1.0, "expression": "neutral", "confidence": 0.9},
                {"relative_seconds": 1.5, "expression": "happiness", "confidence": 0.9},
            ],
        },
        headers={"X-Device-Id": DEV_B, "X-Device-Token": token_b},
    )
    assert resp.status_code == 200, resp.text

    db = SessionLocal()
    try:
        batch = (
            db.query(UploadBatch)
            .filter(
                UploadBatch.session_id == sid,
                UploadBatch.client_request_id == "aux-batch-0002",
            )
            .one()
        )
        assert batch.device_id == DEV_B
    finally:
        db.close()


def test_07_unbound_device_rejected(client):
    token_c = client.post(
        "/api/v1/devices/register",
        json={"device_id": DEV_C, "device_name": "x", "device_type": "edge_ai_device"},
    ).json()["device_token"]
    resp = client.post(
        f"/api/v1/emotion/sessions/{STATE['sid']}/data",
        json={
            "client_request_id": "unbound-batch-0001",
            "session_meta": {},
            "timeline": [
                {"relative_seconds": 0.0, "expression": "neutral", "confidence": 0.9}
            ],
        },
        headers={"X-Device-Id": DEV_C, "X-Device-Token": token_c},
    )
    assert resp.status_code == 403
    assert "DEVICE_NOT_BOUND" in resp.json()["message"]
