"""阶段 6 验收：设备身份保护（P0-15 / M13）。

对应 AC-22（rotate-token 旧 token 立即失效）、
AC-23（重新注册变更 → 审计 + 告警头，注册仍成功）、
AC-24（revoke 后注册 403 / 上传 401；reinstate 后新 token 可用）。
"""
from app.core.config import get_settings
from app.db.database import SessionLocal
from app.models.device_audit_event import DeviceAuditEvent


def _app_headers() -> dict:
    return {"X-App-Key": get_settings().app_key}


def _register(client, device_id: str, **overrides):
    payload = {
        "device_id": device_id,
        "device_name": "身份保护测试机",
        "device_type": "edge_ai_device",
        "manufacturer": "Huawei",
        "model": "Atlas 200I DK A2",
        "capabilities": ["emotion"],
    }
    payload.update(overrides)
    return client.post("/api/v1/devices/register", json=payload)


def _heartbeat(client, device_id: str, token: str):
    return client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        json={},
        headers={"X-Device-Id": device_id, "X-Device-Token": token},
    )


def _audits(device_id: str, event_type: str | None = None):
    db = SessionLocal()
    try:
        query = db.query(DeviceAuditEvent).filter(
            DeviceAuditEvent.device_id == device_id
        )
        if event_type:
            query = query.filter(DeviceAuditEvent.event_type == event_type)
        return query.order_by(DeviceAuditEvent.id.asc()).all()
    finally:
        db.close()


def test_01_reregister_with_changed_model_is_audited(client):
    """AC-23：同 device_id 换 model 重新注册 → 仍成功、返回原 token、HIGH 审计 + 告警头。"""
    first = _register(client, "identity_dev_01")
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    assert "X-Identity-Warning" not in first.headers
    token = first.json()["device_token"]

    second = _register(client, "identity_dev_01", model="Atlas 300I")
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["device_token"] == token  # 幂等，返回原 token
    assert second.headers.get("X-Identity-Warning") == "changed"

    events = _audits("identity_dev_01", "register_changed")
    assert len(events) == 1
    assert events[0].severity == "HIGH"
    rules = {c["rule"] for c in events[0].detail["changes"]}
    assert "R3" in rules

    # 基线已滚动：完全相同载荷再注册不重复告警
    third = _register(client, "identity_dev_01", model="Atlas 300I")
    assert third.status_code == 200
    assert "X-Identity-Warning" not in third.headers


def test_02_rotate_token_invalidates_old_immediately(client):
    """AC-22：轮换后旧 token 立即 401，新 token 正常，审计有 token_rotated。"""
    resp = _register(client, "identity_dev_02")
    old_token = resp.json()["device_token"]
    assert _heartbeat(client, "identity_dev_02", old_token).status_code == 200

    # 缺少 X-App-Key 不允许轮换
    assert (
        client.post("/api/v1/devices/identity_dev_02/rotate-token").status_code == 401
    )

    rotated = client.post(
        "/api/v1/devices/identity_dev_02/rotate-token", headers=_app_headers()
    )
    assert rotated.status_code == 200, rotated.text
    new_token = rotated.json()["device_token"]
    assert new_token and new_token != old_token

    assert _heartbeat(client, "identity_dev_02", old_token).status_code == 401
    assert _heartbeat(client, "identity_dev_02", new_token).status_code == 200

    events = _audits("identity_dev_02", "token_rotated")
    assert events and events[0].detail["action"] == "rotate"
    # 审计不得包含 token 明文
    assert old_token not in str(events[0].detail)
    assert new_token not in str(events[0].detail)


def test_03_revoke_then_reinstate(client):
    """AC-24：revoke 后 token 401、注册 403；reinstate 后新 token 可用。"""
    resp = _register(client, "identity_dev_03")
    old_token = resp.json()["device_token"]

    revoked = client.post(
        "/api/v1/devices/identity_dev_03/revoke", headers=_app_headers()
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    assert _heartbeat(client, "identity_dev_03", old_token).status_code == 401

    blocked = _register(client, "identity_dev_03")
    assert blocked.status_code == 403
    assert "DEVICE_REVOKED" in blocked.json()["message"]

    reinstated = client.post(
        "/api/v1/devices/identity_dev_03/reinstate", headers=_app_headers()
    )
    assert reinstated.status_code == 200, reinstated.text
    new_token = reinstated.json()["device_token"]
    assert new_token != old_token

    assert _heartbeat(client, "identity_dev_03", new_token).status_code == 200
    assert _heartbeat(client, "identity_dev_03", old_token).status_code == 401

    assert _audits("identity_dev_03", "device_revoked")
    assert _register(client, "identity_dev_03").status_code == 200


def test_04_token_used_on_other_device_is_critical(client):
    """I4：同一 token 出现在另一 device_id 的请求中 → 401 + CRITICAL 审计。"""
    _register(client, "identity_dev_04")
    resp = _register(client, "identity_dev_05")
    token_05 = resp.json()["device_token"]

    assert _heartbeat(client, "identity_dev_04", token_05).status_code == 401

    events = _audits("identity_dev_04", "clone_suspected")
    assert events
    assert events[0].severity == "CRITICAL"
    assert events[0].detail["reason"] == "token_used_on_other_device_id"
    assert events[0].detail["token_owner_device_id"] == "identity_dev_05"


def test_05_registration_burst_is_warned_not_blocked(client):
    """I7：同 IP 5 分钟内新注册 ≥3 → WARN 审计，但注册不被拒绝。"""
    for index in range(3):
        resp = _register(client, f"identity_burst_{index}")
        assert resp.status_code == 200, resp.text

    db = SessionLocal()
    try:
        events = (
            db.query(DeviceAuditEvent)
            .filter(
                DeviceAuditEvent.event_type == "clone_suspected",
                DeviceAuditEvent.severity == "WARN",
            )
            .all()
        )
    finally:
        db.close()
    assert any(e.detail.get("reason") == "registration_burst" for e in events)
