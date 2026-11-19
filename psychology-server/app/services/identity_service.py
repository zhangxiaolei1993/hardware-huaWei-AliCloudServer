"""设备身份保护（v1.4 §17.4 / D3 / P0-15 / M13）。

只依赖服务器 A 可观测的信息（注册载荷、token、源 IP、上传行为）：
- I1/I2/I3：注册快照比对 + 变更分级 + 注册仍成功 + 审计 + 告警头；
- I4：token 与 device_id 强绑定，同一 token 出现在另一 device_id → 401 + CRITICAL 审计；
- I5/I9：rotate-token / revoke / reinstate；
- I6/I7/I8：克隆、异常注册、异常认证的轻量检测（进程内滑动窗口，仅告警不阻断）；
- I10：device_audit_events 落库。

注意：审计 detail 只允许写 token 指纹，绝不写 token 明文。
"""
import hashlib
import ipaddress
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session as DBSession

from app.core.logging import get_logger
from app.db.database import utcnow
from app.models.device import Device
from app.models.device_audit_event import DeviceAuditEvent
from app.schemas.device import DeviceRegisterRequest

logger = get_logger(__name__)

SEVERITY_ORDER = {"INFO": 0, "WARN": 1, "HIGH": 2, "CRITICAL": 3}

# I6/I7/I8 阈值（§17.4）
CLONE_WINDOW_SECONDS = 600
CLONE_IP_THRESHOLD = 2
REGISTER_WINDOW_SECONDS = 300
REGISTER_BURST_THRESHOLD = 3
AUTH_FAIL_WINDOW_SECONDS = 60
AUTH_FAIL_THRESHOLD = 20

# 进程内滑动窗口：P0 单进程部署，重启即清空，只用于告警
_activity: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
_last_clone_alert: Dict[str, float] = {}
_register_events: List[Tuple[float, str]] = []
_last_register_burst_alert: Dict[str, float] = {}
_auth_failures: Dict[str, List[float]] = defaultdict(list)
_last_seen_ip: Dict[str, str] = {}


def token_fingerprint(token: str) -> str:
    """审计只保存指纹，便于比对但不泄露 token。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def record_audit(
    db: DBSession,
    *,
    device_id: str,
    event_type: str,
    severity: str,
    source_ip: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> DeviceAuditEvent:
    event = DeviceAuditEvent(
        device_id=device_id,
        event_type=event_type,
        severity=severity,
        source_ip=source_ip,
        detail=detail,
        created_at=utcnow(),
    )
    db.add(event)
    db.flush()
    logger.info(
        "device audit: device=%s type=%s severity=%s ip=%s",
        device_id,
        event_type,
        severity,
        source_ip,
    )
    return event


def _max_severity(severities: List[str]) -> str:
    if not severities:
        return "INFO"
    return max(severities, key=lambda s: SEVERITY_ORDER.get(s, 0))


def _version_key(value: Optional[str]) -> Tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value or ""))


def compare_registration(
    device: Device, req: DeviceRegisterRequest, source_ip: Optional[str] = None
) -> List[Dict[str, Any]]:
    """I2 R1–R5：逐字段比对注册快照，返回变更列表（含级别）。

    未提供的可选字段（None / 空 capabilities）不参与比对，避免误报。
    """
    changes: List[Dict[str, Any]] = []

    def add(rule: str, field: str, old: Any, new: Any, severity: str) -> None:
        if old != new:
            changes.append(
                {
                    "rule": rule,
                    "field": field,
                    "old": old,
                    "new": new,
                    "severity": severity,
                }
            )

    # R1 firmware_version 变化：回退 → WARN，否则 INFO
    if req.firmware_version is not None:
        old_fw = device.firmware_version
        new_fw = req.firmware_version
        if old_fw != new_fw:
            severity = "INFO"
            if old_fw and _version_key(new_fw) < _version_key(old_fw):
                severity = "WARN"
            add("R1", "firmware_version", old_fw, new_fw, severity)

    # R2 capabilities：新增 → INFO；减少 → WARN
    if req.capabilities:
        old_caps = sorted(device.capabilities or [])
        new_caps = sorted(req.capabilities)
        if old_caps != new_caps:
            removed = set(old_caps) - set(new_caps)
            add(
                "R2",
                "capabilities",
                old_caps,
                new_caps,
                "WARN" if removed else "INFO",
            )

    # R3 model / manufacturer 变化 → HIGH
    if req.model is not None:
        add("R3", "model", device.model, req.model, "HIGH")
    if req.manufacturer is not None:
        add("R3", "manufacturer", device.manufacturer, req.manufacturer, "HIGH")

    # R4 注册源 IP 与历史最近一次不同 → WARN（IP 不固定，仅提示）
    if source_ip:
        previous_ip = _last_seen_ip.get(device.device_id)
        if previous_ip and previous_ip != source_ip:
            add("R4", "source_ip", previous_ip, source_ip, "WARN")

    # R5 device_name 变化 → INFO
    add("R5", "device_name", device.device_name, req.device_name, "INFO")

    return changes


def apply_snapshot(device: Device, req: DeviceRegisterRequest) -> None:
    """变更已审计后，将基线滚动到最新注册值，避免每次启动重复告警。"""
    device.device_name = req.device_name
    if req.manufacturer is not None:
        device.manufacturer = req.manufacturer
    if req.model is not None:
        device.model = req.model
    if req.firmware_version is not None:
        device.firmware_version = req.firmware_version
    if req.capabilities:
        device.capabilities = req.capabilities


def note_registration_ip(device_id: str, source_ip: Optional[str]) -> None:
    if source_ip:
        _last_seen_ip[device_id] = source_ip


def note_registration(
    db: DBSession,
    device: Device,
    changes: List[Dict[str, Any]],
    source_ip: Optional[str],
) -> None:
    """I3：写审计、置 identity_changed_at；不阻断注册。"""
    now = utcnow()
    if changes:
        device.identity_changed_at = now
        # 同一载荷顺序稳定，便于复盘
        record_audit(
            db,
            device_id=device.device_id,
            event_type="register_changed",
            severity=_max_severity([c["severity"] for c in changes]),
            source_ip=source_ip,
            detail={
                "changes": changes,
                "identity_changed_at": now.isoformat(),
            },
        )
    if source_ip:
        _last_seen_ip[device.device_id] = source_ip


def record_registration_burst(
    db: DBSession, device: Device, source_ip: Optional[str]
) -> None:
    """I7：同一源 IP 5 分钟内新注册 ≥3 个 device_id → WARN（不阻断）。"""
    if not source_ip:
        return
    current = time.time()
    cutoff = current - REGISTER_WINDOW_SECONDS
    _register_events[:] = [e for e in _register_events if e[0] >= cutoff]
    _register_events.append((current, source_ip))
    count = sum(1 for _, ip in _register_events if ip == source_ip)
    if count < REGISTER_BURST_THRESHOLD:
        return
    if current - _last_register_burst_alert.get(source_ip, 0.0) < REGISTER_WINDOW_SECONDS:
        return
    _last_register_burst_alert[source_ip] = current
    record_audit(
        db,
        device_id=device.device_id,
        event_type="clone_suspected",
        severity="WARN",
        source_ip=source_ip,
        detail={
            "reason": "registration_burst",
            "registrations": count,
            "window_seconds": REGISTER_WINDOW_SECONDS,
        },
    )


def _is_loopback(source_ip: str) -> bool:
    """loopback（127.0.0.0/8、::1、IPv4-mapped）不代表真实位置差异，不参与克隆判定。"""
    try:
        address = ipaddress.ip_address(source_ip.strip())
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def observe_device_ip(
    db: DBSession,
    device_id: str,
    source_ip: Optional[str],
    *,
    channel: str,
) -> bool:
    """I6：10 分钟内同一 device_id 来自 ≥2 个不同 IP → possible clone。

    返回是否本次新写入审计；调用方负责 commit。
    loopback 地址（本机部署/本地代理）不计入 IP 多样性，避免误报。
    """
    if not source_ip:
        return False
    if _is_loopback(source_ip):
        return False
    current = time.time()
    window = _activity[device_id]
    window.append((current, source_ip))
    cutoff = current - CLONE_WINDOW_SECONDS
    window[:] = [item for item in window if item[0] >= cutoff]
    ips = sorted({ip for _, ip in window})
    if len(ips) < CLONE_IP_THRESHOLD:
        return False
    if current - _last_clone_alert.get(device_id, 0.0) < CLONE_WINDOW_SECONDS:
        return False
    _last_clone_alert[device_id] = current
    record_audit(
        db,
        device_id=device_id,
        event_type="clone_suspected",
        severity="WARN",
        source_ip=source_ip,
        detail={
            "reason": "multi_ip_within_window",
            "ips": ips,
            "channel": channel,
            "window_seconds": CLONE_WINDOW_SECONDS,
        },
    )
    return True


def record_auth_failure(
    db: DBSession, device_id: Optional[str], source_ip: Optional[str]
) -> bool:
    """I8：单设备 1 分钟内认证失败 ≥20 次 → WARN。返回是否本次写入审计。"""
    if not device_id:
        return False
    key = f"{device_id}|{source_ip or '-'}"
    current = time.time()
    bucket = _auth_failures[key]
    bucket.append(current)
    cutoff = current - AUTH_FAIL_WINDOW_SECONDS
    bucket[:] = [ts for ts in bucket if ts >= cutoff]
    if len(bucket) != AUTH_FAIL_THRESHOLD:
        return False
    record_audit(
        db,
        device_id=device_id,
        event_type="auth_failed_burst",
        severity="WARN",
        source_ip=source_ip,
        detail={
            "failures": len(bucket),
            "window_seconds": AUTH_FAIL_WINDOW_SECONDS,
        },
    )
    return True


def record_cross_device_token(
    db: DBSession,
    presented_device_id: str,
    token_owner_id: str,
    source_ip: Optional[str],
) -> None:
    """I4：同一 token 出现在另一 device_id 的请求中 → CRITICAL 审计。"""
    record_audit(
        db,
        device_id=presented_device_id,
        event_type="clone_suspected",
        severity="CRITICAL",
        source_ip=source_ip,
        detail={
            "reason": "token_used_on_other_device_id",
            "presented_device_id": presented_device_id,
            "token_owner_device_id": token_owner_id,
        },
    )


# ---------- token 轮换 / 吊销 / 恢复（I5 / I9） ----------
def rotate_token(db: DBSession, device: Device, source_ip: Optional[str] = None) -> str:
    """原子替换 token，返回新明文（仅此一次）。"""
    from app.core.security import generate_device_token

    now = utcnow()
    new_token = generate_device_token()
    device.device_token = new_token
    device.token_rotated_at = now
    device.updated_at = now
    record_audit(
        db,
        device_id=device.device_id,
        event_type="token_rotated",
        severity="WARN",
        source_ip=source_ip,
        detail={
            "action": "rotate",
            "token_fingerprint": token_fingerprint(new_token),
        },
    )
    db.commit()
    db.refresh(device)
    return new_token


def revoke_device(db: DBSession, device: Device, source_ip: Optional[str] = None) -> Device:
    now = utcnow()
    device.token_revoked_at = now
    device.updated_at = now
    record_audit(
        db,
        device_id=device.device_id,
        event_type="device_revoked",
        severity="HIGH",
        source_ip=source_ip,
        detail={"action": "revoke"},
    )
    db.commit()
    db.refresh(device)
    return device


def reinstate_device(
    db: DBSession, device: Device, source_ip: Optional[str] = None
) -> str:
    """恢复被吊销设备：生成新 token 并清除吊销标记。"""
    from app.core.security import generate_device_token

    now = utcnow()
    new_token = generate_device_token()
    device.device_token = new_token
    device.token_revoked_at = None
    device.token_rotated_at = now
    device.updated_at = now
    record_audit(
        db,
        device_id=device.device_id,
        event_type="token_rotated",
        severity="WARN",
        source_ip=source_ip,
        detail={
            "action": "reinstate",
            "token_fingerprint": token_fingerprint(new_token),
        },
    )
    db.commit()
    db.refresh(device)
    return new_token
