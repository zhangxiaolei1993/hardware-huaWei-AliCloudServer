"""会话状态免认证 & 僵尸会话回收测试。"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db.database import utcnow
from app.models.device import Device
from app.models.session import SessionModel
from app.services import session_service


def _online_device(client: TestClient, dev: str) -> str:
    r = client.post(
        "/api/v1/devices/register",
        json={"device_id": dev, "device_name": "回收测试", "device_type": "edge_ai_device"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["device_token"]
    hb = client.post(
        f"/api/v1/devices/{dev}/heartbeat",
        json={},
        headers={"X-Device-Id": dev, "X-Device-Token": token},
    )
    assert hb.status_code == 200
    return token


def _create(client: TestClient, dev: str, token: str) -> str:
    r = client.post(
        "/api/v1/sessions",
        json={"device_id": dev},
        headers={"X-Device-Id": dev, "X-Device-Token": token},
    )
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


# ---------- 状态接口免认证 ----------
def test_status_endpoint_no_auth(client: TestClient):
    dev = "atlas_reap_noauth"
    token = _online_device(client, dev)
    sid = _create(client, dev, token)

    # 手机端不带任何认证头：running
    r = client.post(
        f"/api/v1/sessions/{sid}/status",
        json={"status": "running"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    # completed 同样免认证
    r = client.post(
        f"/api/v1/sessions/{sid}/status",
        json={"status": "completed"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_status_endpoint_device_token_still_works(client: TestClient):
    dev = "atlas_reap_token"
    token = _online_device(client, dev)
    sid = _create(client, dev, token)
    r = client.post(
        f"/api/v1/sessions/{sid}/status",
        json={"status": "completed"},
        headers={"X-Device-Id": dev, "X-Device-Token": token},
    )
    assert r.status_code == 200


def test_status_endpoint_mismatched_device_403(client: TestClient):
    dev = "atlas_reap_owner"
    token = _online_device(client, dev)
    sid = _create(client, dev, token)

    other = "atlas_reap_stranger"
    other_token = _online_device(client, other)
    r = client.post(
        f"/api/v1/sessions/{sid}/status",
        json={"status": "completed"},
        headers={"X-Device-Id": other, "X-Device-Token": other_token},
    )
    assert r.status_code == 403


# ---------- 僵尸会话回收 ----------
def test_reap_interrupts_session_with_old_heartbeat(client: TestClient, db_session):
    dev = "atlas_reap_old"
    token = _online_device(client, dev)
    sid = _create(client, dev, token)

    # 模拟设备心跳消失 200 秒
    device = db_session.query(Device).filter(Device.device_id == dev).first()
    device.last_seen = utcnow() - timedelta(seconds=200)
    db_session.commit()

    interrupted = session_service.reap_stale_sessions(db_session)
    assert sid in interrupted

    session = db_session.query(SessionModel).filter(SessionModel.session_id == sid).first()
    assert session.status == "interrupted"
    assert session.ended_at is not None


def test_reap_keeps_session_with_recent_heartbeat(client: TestClient, db_session):
    dev = "atlas_reap_recent"
    token = _online_device(client, dev)  # last_seen 就在当前
    sid = _create(client, dev, token)

    interrupted = session_service.reap_stale_sessions(db_session)
    assert sid not in interrupted
    session = db_session.query(SessionModel).filter(SessionModel.session_id == sid).first()
    assert session.status in ("created", "running")


def test_reap_interrupts_never_seen_device(client: TestClient, db_session):
    # last_seen=None 的场景只可能来自历史遗留数据（现接口要求在线才给建会话）
    dev = "atlas_reap_never"
    client.post(
        "/api/v1/devices/register",
        json={"device_id": dev, "device_name": "从未心跳", "device_type": "edge_ai_device"},
    )
    device = db_session.query(Device).filter(Device.device_id == dev).first()
    session = session_service.create_session(db_session, device)

    interrupted = session_service.reap_stale_sessions(db_session)
    assert session.session_id in interrupted


def test_reap_does_not_touch_completed(client: TestClient, db_session):
    dev = "atlas_reap_done"
    token = _online_device(client, dev)
    sid = _create(client, dev, token)
    client.post(
        f"/api/v1/sessions/{sid}/status",
        json={"status": "completed"},
    )

    # 设备心跳变旧，已完成会话不受影响
    device = db_session.query(Device).filter(Device.device_id == dev).first()
    device.last_seen = utcnow() - timedelta(seconds=600)
    db_session.commit()

    interrupted = session_service.reap_stale_sessions(db_session)
    assert sid not in interrupted


def test_active_session_excludes_interrupted(client: TestClient, db_session):
    dev = "atlas_reap_hidden"
    token = _online_device(client, dev)
    sid = _create(client, dev, token)

    device = db_session.query(Device).filter(Device.device_id == dev).first()
    device.last_seen = utcnow() - timedelta(seconds=300)
    db_session.commit()
    session_service.reap_stale_sessions(db_session)

    r = client.get(f"/api/v1/devices/{dev}/active-session")
    assert r.status_code == 200
    assert r.json()["session_id"] is None

    # 直接查询仍可看到 interrupted 状态（数据保留）
    r = client.get(f"/api/v1/sessions/{sid}")
    assert r.json()["status"] == "interrupted"
