"""手机连接设备功能测试。"""
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import SessionLocal, utcnow
from app.models.device import Device
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.session import SessionModel
from app.models.session_device import SessionDevice

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
    assert "CONNECTION_DEVICE_MISMATCH" in resp.json()["message"]


def test_disconnect_by_connection_id_is_idempotent(client: TestClient):
    conn = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": "phone-app-client-0003"},
    ).json()
    cid = conn["connection_id"]

    first = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect", json={"connection_id": cid}
    )
    second = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect", json={"connection_id": cid}
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["connection_id"] == second.json()["connection_id"] == cid
    assert second.json()["status"] == "disconnected"


def test_disconnect_by_app_client_id(client: TestClient):
    """只带 app_client_id（Flutter 实际入参）也能关闭当前活跃连接。"""
    app = "phone-app-client-only-01"
    conn = client.post(
        f"/api/v1/devices/{DEVICE}/connect",
        json={"app_client_id": app, "user_id": "user-001"},
    ).json()

    resp = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect",
        json={"app_client_id": app, "user_id": "user-001"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["connection_id"] == conn["connection_id"]
    assert data["status"] == "disconnected"
    assert data["disconnected_at"] is not None


def test_disconnect_by_app_client_id_is_idempotent(client: TestClient):
    app = "phone-app-client-only-02"
    conn = client.post(
        f"/api/v1/devices/{DEVICE}/connect", json={"app_client_id": app}
    ).json()

    first = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect", json={"app_client_id": app}
    )
    second = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect", json={"app_client_id": app}
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["connection_id"] == conn["connection_id"]
    assert second.json()["connection_id"] == conn["connection_id"]


def test_disconnect_no_matching_connection_404(client: TestClient):
    resp = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect",
        json={"app_client_id": "phone-app-client-absent"},
    )
    assert resp.status_code == 404
    assert "NO_ACTIVE_CONNECTION" in resp.json()["message"]


def test_disconnect_without_identifier_400(client: TestClient):
    resp = client.post(f"/api/v1/devices/{DEVICE}/disconnect", json={})
    assert resp.status_code == 400
    assert "MISSING_CONNECTION_IDENTIFIER" in resp.json()["message"]


def test_disconnect_without_body_400(client: TestClient):
    """完全不带 body 也不应是 422；按契约返回 400。"""
    resp = client.post(f"/api/v1/devices/{DEVICE}/disconnect")
    assert resp.status_code == 400
    assert "MISSING_CONNECTION_IDENTIFIER" in resp.json()["message"]


def test_disconnect_invalid_app_client_id_422(client: TestClient):
    """app_client_id 长度不足由 Schema 拒绝（非法参数）。"""
    resp = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect",
        json={"app_client_id": "short"},
    )
    assert resp.status_code == 422


def test_disconnect_offline_device(client: TestClient):
    """设备异常离线（心跳超时）时，仍可按 app_client_id 关闭连接记录。"""
    _register_and_token(client)
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.device_id == DEVICE).one()
        device.last_seen = utcnow() - timedelta(hours=1)
        db.commit()
    finally:
        db.close()
    app = "phone-app-offline-001"
    conn = client.post(
        f"/api/v1/devices/{DEVICE}/connect", json={"app_client_id": app}
    ).json()
    assert conn["connected"] is False

    resp = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect", json={"app_client_id": app}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "disconnected"
    assert resp.json()["disconnected_at"] is not None


def test_disconnect_after_session_completed_keeps_data(client: TestClient):
    """会话完成后断开：只关连接，不误删会话/绑定/表情数据。"""
    token = _register_and_token(client)
    headers = {"X-Device-Id": DEVICE, "X-Device-Token": token}
    client.post(f"/api/v1/devices/{DEVICE}/heartbeat", json={}, headers=headers)

    app = "phone-app-completed-01"
    conn = client.post(
        f"/api/v1/devices/{DEVICE}/connect", json={"app_client_id": app}
    ).json()

    created = client.post(
        "/api/v1/sessions",
        json={"device_id": DEVICE, "session_type": "emotion"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]

    upload = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": str(uuid.uuid4()),
            "session_meta": {"video_frames": 10, "face_detected_frames": 9},
            "timeline": [
                {"relative_seconds": 0.0, "expression": "neutral", "confidence": 0.9},
                {"relative_seconds": 0.5, "expression": "happiness", "confidence": 0.8},
            ],
        },
        headers=headers,
    )
    assert upload.status_code == 200, upload.text

    done = client.post(f"/api/v1/sessions/{sid}/status", json={"status": "completed"})
    assert done.status_code == 200, done.text

    db = SessionLocal()
    try:
        before = {
            "sessions": db.query(SessionModel).count(),
            "binds": db.query(SessionDevice).count(),
            "records": db.query(EmotionRecord).count(),
            "results": db.query(EmotionResult).count(),
        }
    finally:
        db.close()

    resp = client.post(
        f"/api/v1/devices/{DEVICE}/disconnect", json={"app_client_id": app}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connection_id"] == conn["connection_id"]

    db = SessionLocal()
    try:
        after = {
            "sessions": db.query(SessionModel).count(),
            "binds": db.query(SessionDevice).count(),
            "records": db.query(EmotionRecord).count(),
            "results": db.query(EmotionResult).count(),
        }
        binding = (
            db.query(SessionDevice)
            .filter(
                SessionDevice.session_id == sid,
                SessionDevice.device_id == DEVICE,
            )
            .one()
        )
        # 会话级绑定保持 bound：迟到上传仍依赖它，不应随 disconnect 解绑
        assert binding.status == "bound"
        assert binding.unbound_at is None
    finally:
        db.close()

    assert before == after

    detail = client.get(
        f"/api/v1/sessions/{sid}",
        headers={"X-Api-Key": get_settings().query_api_key},
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"

