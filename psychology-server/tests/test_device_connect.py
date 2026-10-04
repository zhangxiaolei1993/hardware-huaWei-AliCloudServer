"""手机连接设备功能测试。"""
from fastapi.testclient import TestClient

DEVICE = "atlas_conn_dev"
APP_CLIENT = "phone-app-client-0001"


def _register_and_token(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": DEVICE,
            "device_name": "连接测试设备",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["device_token"]


def test_connect_unknown_device_404(client: TestClient):
    resp = client.post(
        "/api/v1/devices/no_such_device/connect",
        json={"app_client_id": APP_CLIENT},
    )
    assert resp.status_code == 404


def test_connect_offline_device(client: TestClient):
    _register_and_token(client)
    resp = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": APP_CLIENT},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["connected"] is False
    assert data["device_status"] == "offline"
    assert len(data["connection_id"]) >= 16


def test_connect_online_device(client: TestClient):
    token = _register_and_token(client)
    # 先让设备上线
    hb = client.post(
        f"/api/v1/devices/{DEVICE}/heartbeat",
        json={},
        headers={"X-Device-Id": DEVICE, "X-Device-Token": token},
    )
    assert hb.status_code == 200

    # 之前离线连接已产生 connected 记录，断开它以干净验证
    # （直接再次连接应复用同一记录，状态随设备在线而变）
    resp = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": APP_CLIENT},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["device_status"] == "online"
    assert data["device_name"] == "连接测试设备"


def test_reconnect_is_idempotent(client: TestClient):
    resp1 = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": APP_CLIENT},
    )
    resp2 = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": APP_CLIENT},
    )
    assert resp1.json()["connection_id"] == resp2.json()["connection_id"]


def test_disconnect(client: TestClient):
    conn = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": APP_CLIENT},
    ).json()
    cid = conn["connection_id"]

    resp = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect",
        json={"connection_id": cid},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "disconnected"
    assert data["disconnected_at"] is not None


def test_disconnect_unknown_connection_404(client: TestClient):
    resp = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect",
        json={"connection_id": "no-such-connection"},
    )
    assert resp.status_code == 404


def test_disconnect_wrong_device_403(client: TestClient):
    # 连接属于 DEVICE，却用另一个 device_id 路径去断 → 403
    conn = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": "third-phone-client"},
    ).json()
    resp = client.post(
        "/api/v1/devices/other_device/disconnect",
        json={"connection_id": conn["connection_id"]},
    )
    assert resp.status_code == 403
