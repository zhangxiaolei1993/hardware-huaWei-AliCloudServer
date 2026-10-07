"""手机端使用 connection_id 创建会话的测试。"""
from fastapi.testclient import TestClient

DEV = "atlas_sess_conn"
OTHER = "atlas_sess_conn_other"


def _register_and_online(client: TestClient, device_id: str) -> str:
    r = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": device_id,
            "device_name": "会话连接测试",
            "device_type": "edge_ai_device",
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["device_token"]
    hb = client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        json={},
        headers={"X-Device-Id": device_id, "X-Device-Token": token},
    )
    assert hb.status_code == 200, hb.text
    return token


def _connect(client: TestClient, device_id: str, app_id: str = "app-phone-1") -> str:
    r = client.post(
        f"/api/v1/devices/{device_id}/connect",
        json={"app_client_id": app_id},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["connected"] is True
    return data["connection_id"]


def test_create_session_with_connection_id(client: TestClient):
    _register_and_online(client, DEV)
    connection_id = _connect(client, DEV)
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "session_type": "emotion", "connection_id": connection_id},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "created"
    assert len(data["session_id"]) >= 16


def test_create_session_unknown_connection_404(client: TestClient):
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "connection_id": "not_exist_conn"},
    )
    assert r.status_code == 404


def test_create_session_disconnected_connection_409(client: TestClient):
    connection_id = _connect(client, DEV, app_id="app-phone-2")
    dr = client.post(
        f"/api/v1/devices/{DEV}/disconnect",
        json={"connection_id": connection_id},
    )
    assert dr.status_code == 200
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "connection_id": connection_id},
    )
    assert r.status_code == 409


def test_connection_id_device_mismatch_403(client: TestClient):
    _register_and_online(client, OTHER)
    connection_id = _connect(client, OTHER, app_id="app-phone-3")
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "connection_id": connection_id},
    )
    assert r.status_code == 403


def test_create_session_without_any_auth_401(client: TestClient):
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV},
    )
    assert r.status_code == 401


def test_device_token_path_still_works(client: TestClient):
    token = _register_and_online(client, DEV)
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "session_type": "emotion"},
        headers={"X-Device-Id": DEV, "X-Device-Token": token},
    )
    assert r.status_code == 201, r.text
