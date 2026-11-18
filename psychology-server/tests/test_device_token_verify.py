"""设备详情认证（§12.2 D-4）：设备启动自检必须能凭 401/403 判定 token 失效。

覆盖：
- 有效设备 token → 200（设备启动自检路径）
- token 错误 / 已轮换 / 已吊销 → 401（设备据此重新注册）
- token 与路径 device_id 不一致 → 403
- 无任何凭证 → 401
- X-App-Key / X-Api-Key 合法调用 → 200，错误 key → 401
"""
from fastapi.testclient import TestClient

from app.core.config import get_settings


def _app_headers() -> dict:
    return {"X-App-Key": get_settings().app_key}


def _api_headers() -> dict:
    return {"X-Api-Key": get_settings().query_api_key}


def _device_headers(device_id: str, token: str) -> dict:
    return {"X-Device-Id": device_id, "X-Device-Token": token}


def _register(client: TestClient, device_id: str) -> str:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": device_id,
            "device_name": "token 校验测试机",
            "device_type": "edge_ai_device",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["device_token"]


def test_valid_device_token_returns_200(client: TestClient):
    dev = "verify_dev_ok"
    token = _register(client, dev)

    resp = client.get(f"/api/v1/devices/{dev}", headers=_device_headers(dev, token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["device_id"] == dev
    # 设备详情永不返回 token
    assert "device_token" not in body


def test_invalid_token_returns_401(client: TestClient):
    dev = "verify_dev_bad"
    _register(client, dev)

    resp = client.get(
        f"/api/v1/devices/{dev}", headers=_device_headers(dev, "not-a-valid-token")
    )

    assert resp.status_code == 401


def test_rotated_old_token_returns_401_and_new_token_works(client: TestClient):
    dev = "verify_dev_rotate"
    old_token = _register(client, dev)

    rotated = client.post(
        f"/api/v1/devices/{dev}/rotate-token", headers=_app_headers()
    )
    assert rotated.status_code == 200, rotated.text
    new_token = rotated.json()["device_token"]
    assert new_token != old_token

    # 旧 token：设备启动自检必须拿到 401 才会重新注册
    assert (
        client.get(
            f"/api/v1/devices/{dev}", headers=_device_headers(dev, old_token)
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/v1/devices/{dev}", headers=_device_headers(dev, new_token)
        ).status_code
        == 200
    )


def test_revoked_token_returns_401(client: TestClient):
    dev = "verify_dev_revoked"
    token = _register(client, dev)

    revoked = client.post(f"/api/v1/devices/{dev}/revoke", headers=_app_headers())
    assert revoked.status_code == 200, revoked.text

    resp = client.get(f"/api/v1/devices/{dev}", headers=_device_headers(dev, token))

    assert resp.status_code == 401


def test_token_device_id_mismatch_returns_403(client: TestClient):
    dev_a = "verify_dev_match_a"
    dev_b = "verify_dev_match_b"
    _register(client, dev_a)
    token_b = _register(client, dev_b)

    # 合法 token，但属于另一台设备
    resp = client.get(
        f"/api/v1/devices/{dev_a}", headers=_device_headers(dev_b, token_b)
    )

    assert resp.status_code == 403


def test_missing_credentials_returns_401(client: TestClient):
    dev = "verify_dev_anon"
    _register(client, dev)

    assert client.get(f"/api/v1/devices/{dev}").status_code == 401
    # 只带一半设备头同样 401
    assert (
        client.get(
            f"/api/v1/devices/{dev}", headers={"X-Device-Id": dev}
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/v1/devices/{dev}", headers={"X-Api-Key": "wrong-key"}
        ).status_code
        == 401
    )


def test_app_key_and_api_key_can_read(client: TestClient):
    dev = "verify_dev_keys"
    _register(client, dev)

    assert client.get(f"/api/v1/devices/{dev}", headers=_app_headers()).status_code == 200
    assert client.get(f"/api/v1/devices/{dev}", headers=_api_headers()).status_code == 200


def test_api_key_can_read_unknown_device_returns_404(client: TestClient):
    resp = client.get("/api/v1/devices/verify_dev_not_exists", headers=_api_headers())
    assert resp.status_code == 404
