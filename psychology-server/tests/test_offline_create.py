"""设备离线时禁止创建会话的测试。"""
from fastapi.testclient import TestClient


def _register(client: TestClient, dev: str) -> str:
    r = client.post(
        "/api/v1/devices/register",
        json={"device_id": dev, "device_name": "离线校验", "device_type": "edge_ai_device"},
    )
    assert r.status_code == 200, r.text
    return r.json()["device_token"]


def _connection_id(client: TestClient, dev: str, app_id: str) -> str:
    # 设备离线时 connect 仍创建连接记录（connected=false）
    r = client.post(
        f"/api/v1/devices/{dev}/connect",
        json={"app_client_id": app_id},
    )
    assert r.status_code == 200
    return r.json()["connection_id"]


def test_offline_device_rejected_via_connection_id(client: TestClient):
    dev = "atlas_offline_reject"
    _register(client, dev)  # 不发心跳 → offline
    cid = _connection_id(client, dev, "app-offline-1")

    r = client.post(
        "/api/v1/sessions",
        json={"device_id": dev, "connection_id": cid},
    )
    assert r.status_code == 409
    assert "offline" in r.json()["message"]


def test_offline_device_rejected_via_device_token(client: TestClient):
    dev = "atlas_offline_token"
    token = _register(client, dev)  # 注册后不心跳 → offline

    r = client.post(
        "/api/v1/sessions",
        json={"device_id": dev},
        headers={"X-Device-Id": dev, "X-Device-Token": token},
    )
    assert r.status_code == 409


def test_online_device_allows_creation(client: TestClient):
    dev = "atlas_online_allow"
    token = _register(client, dev)
    # 发心跳上线
    hb = client.post(
        f"/api/v1/devices/{dev}/heartbeat",
        json={},
        headers={"X-Device-Id": dev, "X-Device-Token": token},
    )
    assert hb.status_code == 200

    cid = _connection_id(client, dev, "app-offline-2")
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": dev, "connection_id": cid},
    )
    assert r.status_code == 201, r.text
