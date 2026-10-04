"""设备实时表情状态功能测试。"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db.database import utcnow
from app.models.device_emotion_status import DeviceEmotionStatus

DEVICE = "atlas_status_dev"
OTHER = "atlas_status_other"


def _register(client: TestClient, device_id: str = DEVICE) -> str:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": device_id,
            "device_name": "实时状态测试设备",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["device_token"]


def _auth(token: str, device_id: str = DEVICE) -> dict:
    return {"X-Device-Id": device_id, "X-Device-Token": token}


def test_put_status_requires_auth(client: TestClient):
    resp = client.put(
        f"/api/v1/emotion/devices/{DEVICE}/status",
        json={"face_detected": True, "expression_detected": False},
    )
    assert resp.status_code == 401


def test_put_and_get_status(client: TestClient):
    token = _register(client)
    resp = client.put(
        f"/api/v1/emotion/devices/{DEVICE}/status",
        json={
            "face_detected": True,
            "expression_detected": True,
            "current_expression": "neutral",
            "confidence": 0.82,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["current_expression"] == "neutral"
    assert data["confidence"] == 0.82
    assert data["stale"] is False
    assert data["updated_at"] is not None

    got = client.get(f"/api/v1/emotion/devices/{DEVICE}/status")
    assert got.status_code == 200
    assert got.json()["current_expression"] == "neutral"


def test_repeated_put_keeps_single_row(client: TestClient, db_session):
    token = _register(client)
    for expr in ("neutral", "happiness", "surprise"):
        client.put(
            f"/api/v1/emotion/devices/{DEVICE}/status",
            json={
                "face_detected": True,
                "expression_detected": True,
                "current_expression": expr,
                "confidence": 0.9,
            },
            headers=_auth(token),
        )
    rows = (
        db_session.query(DeviceEmotionStatus)
        .filter(DeviceEmotionStatus.device_id == DEVICE)
        .count()
    )
    assert rows == 1
    latest = client.get(f"/api/v1/emotion/devices/{DEVICE}/status").json()
    assert latest["current_expression"] == "surprise"


def test_get_status_never_reported_404(client: TestClient):
    _register(client, OTHER)
    resp = client.get(f"/api/v1/emotion/devices/{OTHER}/status")
    assert resp.status_code == 404


def test_expression_true_but_missing_value_422(client: TestClient):
    token = _register(client)
    resp = client.put(
        f"/api/v1/emotion/devices/{DEVICE}/status",
        json={
            "face_detected": True,
            "expression_detected": True,
            "current_expression": None,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_invalid_expression_422(client: TestClient):
    token = _register(client)
    resp = client.put(
        f"/api/v1/emotion/devices/{DEVICE}/status",
        json={
            "face_detected": True,
            "expression_detected": True,
            "current_expression": "bored",
            "confidence": 0.5,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_invalid_confidence_422(client: TestClient):
    token = _register(client)
    resp = client.put(
        f"/api/v1/emotion/devices/{DEVICE}/status",
        json={
            "face_detected": True,
            "expression_detected": True,
            "current_expression": "neutral",
            "confidence": 1.5,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_expression_false_clears_value(client: TestClient):
    token = _register(client)
    resp = client.put(
        f"/api/v1/emotion/devices/{DEVICE}/status",
        json={
            "face_detected": False,
            "expression_detected": False,
            "current_expression": None,
            "confidence": None,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["expression_detected"] is False
    assert data["current_expression"] is None
    assert data["confidence"] is None


def test_token_device_mismatch_403(client: TestClient):
    token = _register(client, DEVICE)
    resp = client.put(
        f"/api/v1/emotion/devices/{OTHER}/status",
        json={"face_detected": True, "expression_detected": False},
        headers=_auth(token, OTHER),
    )
    assert resp.status_code in (403, 401)


def test_stale_flag_after_15_seconds(client: TestClient, db_session):
    row = (
        db_session.query(DeviceEmotionStatus)
        .filter(DeviceEmotionStatus.device_id == DEVICE)
        .first()
    )
    assert row is not None
    row.updated_at = utcnow() - timedelta(seconds=15)
    db_session.commit()

    resp = client.get(f"/api/v1/emotion/devices/{DEVICE}/status")
    assert resp.status_code == 200
    assert resp.json()["stale"] is True
