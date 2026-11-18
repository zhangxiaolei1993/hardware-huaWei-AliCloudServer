"""列出所有设备接口测试（P0-17 起需 X-Api-Key）。"""
from fastapi.testclient import TestClient

from app.core.config import get_settings

_H = {"X-Api-Key": get_settings().query_api_key}


def test_list_devices_returns_registered(client: TestClient):
    dev = "atlas_list_dev"
    before = client.get("/api/v1/devices", headers=_H)
    assert before.status_code == 200
    existing = {d["device_id"] for d in before.json()}

    r = client.post(
        "/api/v1/devices/register",
        json={"device_id": dev, "device_name": "列表测试设备", "device_type": "edge_ai_device"},
    )
    assert r.status_code == 200

    after = client.get("/api/v1/devices", headers=_H)
    assert after.status_code == 200
    items = after.json()
    assert isinstance(items, list)
    assert len(items) == len(existing) + 1

    target = next(d for d in items if d["device_id"] == dev)
    assert target["device_name"] == "列表测试设备"
    assert target["status"] in ("online", "offline")
    # 列表项不应暴露设备 token
    assert "device_token" not in target


def test_list_devices_requires_api_key(client: TestClient):
    assert client.get("/api/v1/devices").status_code == 401
    assert (
        client.get("/api/v1/devices", headers={"X-Api-Key": "wrong"}).status_code == 401
    )
