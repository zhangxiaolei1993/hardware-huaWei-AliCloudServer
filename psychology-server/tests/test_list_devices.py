"""列出所有设备接口测试。"""
from fastapi.testclient import TestClient


def test_list_devices_returns_registered(client: TestClient):
    dev = "atlas_list_dev"
    before = client.get("/api/v1/devices")
    assert before.status_code == 200
    existing = {d["device_id"] for d in before.json()}

    r = client.post(
        "/api/v1/devices/register",
        json={"device_id": dev, "device_name": "列表测试设备", "device_type": "edge_ai_device"},
    )
    assert r.status_code == 200

    after = client.get("/api/v1/devices")
    assert after.status_code == 200
    items = after.json()
    assert isinstance(items, list)
    assert len(items) == len(existing) + 1

    target = next(d for d in items if d["device_id"] == dev)
    assert target["device_name"] == "列表测试设备"
    assert target["status"] in ("online", "offline")
    # 列表项不应暴露设备 token
    assert "device_token" not in target


def test_list_devices_no_auth_required(client: TestClient):
    # 无任何请求头即可访问
    r = client.get("/api/v1/devices")
    assert r.status_code == 200
