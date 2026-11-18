"""修复验证：clone_suspected 只比较设备身份请求的来源 IP。

业务请求（手机端建会话、状态流转）即使来自不同 IP 也不得告警；
同一 device_id 的设备身份请求（心跳/上传等）来自不同 IP 时仍必须告警。
"""
from app.db.database import SessionLocal
from app.models.device_audit_event import DeviceAuditEvent

DEV = "atlas_clone_scope_dev"
DEV2 = "atlas_clone_scope_dev2"
STATE: dict = {}


def _register(client, device_id: str = DEV) -> str:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": device_id,
            "device_name": "克隆检测范围测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    STATE[device_id] = resp.json()["device_token"]
    return STATE[device_id]


def _headers(device_id: str, ip: str) -> dict:
    return {
        "X-Device-Id": device_id,
        "X-Device-Token": STATE[device_id],
        "X-Forwarded-For": ip,
    }


def _clone_events(device_id: str) -> list:
    """只取多 IP 克隆事件（排除注册突发等同类型审计）。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(DeviceAuditEvent)
            .filter(
                DeviceAuditEvent.device_id == device_id,
                DeviceAuditEvent.event_type == "clone_suspected",
            )
            .order_by(DeviceAuditEvent.id.asc())
            .all()
        )
        return [
            e
            for e in rows
            if (e.detail or {}).get("reason") == "multi_ip_within_window"
        ]
    finally:
        db.close()


def test_business_requests_do_not_trigger_clone_warning(client):
    _register(client)
    # 设备心跳：设备侧 IP A
    assert (
        client.post(
            f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=_headers(DEV, "192.0.2.10")
        ).status_code
        == 200
    )
    assert _clone_events(DEV) == []

    # 手机端建会话（真机联调中为 127.0.0.1）：业务请求，不参与比较
    created = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "session_type": "emotion"},
        headers=_headers(DEV, "127.0.0.1"),
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    assert _clone_events(DEV) == []

    # 手机端状态流转（携带设备凭证）：同样不参与比较
    status = client.post(
        f"/api/v1/sessions/{sid}/status",
        json={"status": "running"},
        headers=_headers(DEV, "127.0.0.2"),
    )
    assert status.status_code == 200, status.text
    assert _clone_events(DEV) == []

    # 真实克隆能力保留：设备身份请求来自第二个 IP → WARN 审计
    assert (
        client.post(
            f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=_headers(DEV, "192.0.2.11")
        ).status_code
        == 200
    )
    events = _clone_events(DEV)
    assert len(events) == 1
    assert events[0].severity == "WARN"
    assert events[0].detail["reason"] == "multi_ip_within_window"
    assert events[0].detail["channel"] == "authenticated"
    assert "127.0.0.1" not in events[0].detail["ips"]
    assert "127.0.0.2" not in events[0].detail["ips"]
    assert set(events[0].detail["ips"]) == {"192.0.2.10", "192.0.2.11"}

    # 同一设备 IP 继续请求不会重复告警（去重窗口）
    assert (
        client.post(
            f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=_headers(DEV, "192.0.2.12")
        ).status_code
        == 200
    )
    assert len(_clone_events(DEV)) == 1


def test_single_device_ip_never_warns(client):
    _register(client, DEV2)
    for ip in ("192.0.2.20", "192.0.2.20"):
        assert (
            client.post(
                f"/api/v1/devices/{DEV2}/heartbeat", json={}, headers=_headers(DEV2, ip)
            ).status_code
            == 200
        )
    assert _clone_events(DEV2) == []
