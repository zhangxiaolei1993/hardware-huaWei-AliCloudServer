"""阶段 7 验收：查询接口基础鉴权（P0-17 / AC-15）。

- GET /devices、GET /sessions/{id}、GET .../result 无 Key → 401，Key 正确 → 正常；
- /health 与设备上报链路（X-Device-Token）不受影响。
"""
from app.core.config import get_settings

QH = {"X-Api-Key": get_settings().query_api_key}

DEV = "atlas_query_auth"


def _online(client) -> dict:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": DEV,
            "device_name": "查询鉴权测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["device_token"]
    headers = {"X-Device-Id": DEV, "X-Device-Token": token}
    assert (
        client.post(f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=headers).status_code
        == 200
    )
    return headers


def test_health_and_device_reporting_unaffected(client):
    assert client.get("/api/v1/health").status_code == 200
    headers = _online(client)
    # 设备上报链路只认 X-Device-Token，不需要 X-Api-Key
    resp = client.post(
        "/api/v1/sessions", json={"device_id": DEV, "session_type": "emotion"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]
    resp = client.post(f"/api/v1/sessions/{sid}/status", json={"status": "running"})
    assert resp.status_code == 200


def test_device_list_requires_api_key(client):
    _online(client)
    assert client.get("/api/v1/devices").status_code == 401
    assert client.get("/api/v1/devices", headers={"X-Api-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/devices", headers=QH).status_code == 200


def test_session_query_requires_api_key(client):
    headers = _online(client)
    sid = client.post(
        "/api/v1/sessions", json={"device_id": DEV, "session_type": "emotion"}, headers=headers
    ).json()["session_id"]

    unauthorized = client.get(f"/api/v1/sessions/{sid}")
    assert unauthorized.status_code == 401
    assert "UNAUTHORIZED" in unauthorized.json()["message"]

    assert client.get(f"/api/v1/sessions/{sid}", headers=QH).status_code == 200


def test_result_and_events_query_require_api_key(client):
    headers = _online(client)
    sid = client.post(
        "/api/v1/sessions", json={"device_id": DEV, "session_type": "emotion"}, headers=headers
    ).json()["session_id"]

    assert client.get(f"/api/v1/emotion/sessions/{sid}/result").status_code == 401
    assert client.get(f"/api/v1/sessions/{sid}/events").status_code == 401
    # 带 Key 后进入业务逻辑（该会话暂无结果/事件 → 404 / 空列表）
    assert client.get(f"/api/v1/emotion/sessions/{sid}/result", headers=QH).status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}/events", headers=QH).status_code == 200
