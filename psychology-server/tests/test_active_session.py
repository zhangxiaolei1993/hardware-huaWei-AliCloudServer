"""免认证查询设备当前活跃会话测试（各用例使用独立设备）。"""
from fastapi.testclient import TestClient


def _prepare(client: TestClient, dev: str) -> tuple[str, str]:
    r = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": dev,
            "device_name": "活跃会话测试",
            "device_type": "edge_ai_device",
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["device_token"]
    client.post(
        f"/api/v1/devices/{dev}/heartbeat",
        json={},
        headers={"X-Device-Id": dev, "X-Device-Token": token},
    )
    cr = client.post(
        f"/api/v1/devices/{dev}/connect",
        json={"app_client_id": f"app-{dev}"},
    )
    assert cr.json()["connected"] is True
    return token, cr.json()["connection_id"]


def test_no_active_session_returns_null(client: TestClient):
    dev = "atlas_active_none"
    r = client.get(f"/api/v1/devices/{dev}/active-session")
    assert r.status_code == 200
    data = r.json()
    assert data["device_id"] == dev
    assert data["session_id"] is None
    assert data["status"] is None


def test_active_session_after_created(client: TestClient):
    dev = "atlas_active_created"
    _, connection_id = _prepare(client, dev)
    cr = client.post(
        "/api/v1/sessions",
        json={"device_id": dev, "session_type": "emotion", "connection_id": connection_id},
    )
    assert cr.status_code == 201
    expected_id = cr.json()["session_id"]

    r = client.get(f"/api/v1/devices/{dev}/active-session")
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == expected_id
    assert data["status"] in ("created", "running")
    assert data["session_type"] == "emotion"


def test_no_active_session_after_completed(client: TestClient):
    dev = "atlas_active_completed"
    token, connection_id = _prepare(client, dev)
    cr = client.post(
        "/api/v1/sessions",
        json={"device_id": dev, "connection_id": connection_id},
    )
    session_id = cr.json()["session_id"]

    dr = client.post(
        f"/api/v1/sessions/{session_id}/status",
        json={"status": "completed"},
        headers={"X-Device-Id": dev, "X-Device-Token": token},
    )
    assert dr.status_code == 200

    r = client.get(f"/api/v1/devices/{dev}/active-session")
    assert r.status_code == 200
    assert r.json()["session_id"] is None
