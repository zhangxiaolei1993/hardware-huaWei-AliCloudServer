"""会话服务：创建 / 查询 / 状态流转 / 僵尸会话回收。"""
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import utcnow
from app.models.device import Device
from app.models.research import ResearchParticipant
from app.models.session import SessionModel
from app.models.session_device import SessionDevice

logger = get_logger(__name__)

VALID_STATUSES = ("created", "running", "completed", "interrupted", "aborted")
# 未完成、可能被回收的状态
UNFINISHED_STATUSES = ("created", "running")

# 设备能力 → 允许下发的会话类型（§6.7 G1）
_CAPABILITY_SESSION_TYPES = {"emotion": "emotion"}


def find_by_client_request_id(
    db: DBSession, source_system: str, client_request_id: str
) -> Optional[SessionModel]:
    """按幂等键查询已创建的会话。"""
    return (
        db.query(SessionModel)
        .filter(
            SessionModel.source_system == source_system,
            SessionModel.client_request_id == client_request_id,
        )
        .first()
    )


def related_session_ids(
    db: DBSession, session: SessionModel, exclude_session_id: Optional[str] = None
) -> List[str]:
    """同一来源系统下、同一外部会话编号 N1 的其他 S1 列表。"""
    if not session.external_session_id:
        return []
    query = db.query(SessionModel).filter(
        SessionModel.source_system == session.source_system,
        SessionModel.external_session_id == session.external_session_id,
    )
    if exclude_session_id:
        query = query.filter(SessionModel.session_id != exclude_session_id)
    return [s.session_id for s in query.order_by(SessionModel.id.asc()).all()]


def subject_ref_for(source_system: str, identity: str) -> str:
    """P0-9 / M7：不可逆脱敏关联标识（不是加密，无机密性）。

    ``HMAC_SHA256(RESEARCH_SALT, source_system + ':' + identity)`` 前 16 hex。
    """
    salt = get_settings().research_salt.encode("utf-8")
    message = f"{source_system}:{identity}".encode("utf-8")
    return hmac.new(salt, message, hashlib.sha256).hexdigest()[:16]


def create_session(
    db: DBSession,
    device: Device,
    session_type: str = "emotion",
    user_id: Optional[str] = None,
    source_system: str = "standalone",
    external_session_id: Optional[str] = None,
    client_request_id: Optional[str] = None,
    external_user_id: Optional[str] = None,
    participant_id: Optional[str] = None,
) -> Tuple[SessionModel, bool, List[str]]:
    """创建会话；返回 (会话, 是否新建, 同 N1 的其他 session_id)。

    幂等：同一 source_system + client_request_id 已存在时返回原会话，不新建。
    """
    if client_request_id:
        existing = find_by_client_request_id(db, source_system, client_request_id)
        if existing is not None:
            _ensure_primary_binding(db, existing)
            logger.info(
                "session %s reused by idempotent request (source=%s)",
                existing.session_id,
                source_system,
            )
            return (
                existing,
                False,
                related_session_ids(db, existing, existing.session_id),
            )

    settings = get_settings()
    now = utcnow()
    # P0-9：优先 external_user_id；缺失时回退 user_id 并标记 deprecated
    identity_value = external_user_id or user_id
    deprecated_identity_used = bool(not external_user_id and user_id)
    subject_ref = (
        subject_ref_for(source_system, identity_value) if identity_value else None
    )
    if deprecated_identity_used:
        logger.warning(
            "session identity fallback to user_id (deprecated_identity_used=true)"
        )
    research_participant: Optional[ResearchParticipant] = None
    if participant_id:
        research_participant = (
            db.query(ResearchParticipant)
            .filter(ResearchParticipant.participant_id == participant_id)
            .first()
        )
    session = SessionModel(
        session_id=uuid.uuid4().hex,
        device_id=device.device_id,
        user_id=user_id,
        external_user_id=external_user_id,
        subject_ref=subject_ref,
        deprecated_identity_used=deprecated_identity_used,
        participant_id=participant_id,
        project_id=(
            research_participant.project_id if research_participant else None
        ),
        group_id=research_participant.group_id if research_participant else None,
        data_origin="live",
        session_type=session_type,
        source_system=source_system,
        external_session_id=external_session_id,
        client_request_id=client_request_id,
        anchor_utc_at=now,
        anchor_source="server",
        anchor_uncertainty_ms=settings.anchor_uncertainty_ms,
        late_upload_deadline=now
        + timedelta(seconds=settings.session_late_upload_window_seconds),
        status="created",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        # 并发重放：唯一约束兜底，回查并返回已存在会话
        db.rollback()
        existing = find_by_client_request_id(
            db, source_system, client_request_id or ""
        )
        if existing is None:
            raise
        return existing, False, related_session_ids(db, existing, existing.session_id)

    db.refresh(session)
    _ensure_primary_binding(db, session)
    logger.info(
        "session %s created by device %s (type=%s, source=%s, external=%s)",
        session.session_id,
        device.device_id,
        session_type,
        source_system,
        external_session_id,
    )
    return (
        session,
        True,
        related_session_ids(db, session, session.session_id),
    )


def get_session(db: DBSession, session_id: str) -> Optional[SessionModel]:
    return db.query(SessionModel).filter(SessionModel.session_id == session_id).first()


def _ensure_primary_binding(db: DBSession, session: SessionModel) -> None:
    """确保会话的主设备已写入 session_devices（历史会话补齐）。"""
    exists = (
        db.query(SessionDevice)
        .filter(
            SessionDevice.session_id == session.session_id,
            SessionDevice.device_id == session.device_id,
        )
        .first()
    )
    if exists is not None:
        return
    now = utcnow()
    db.add(
        SessionDevice(
            session_id=session.session_id,
            device_id=session.device_id,
            role="primary",
            status="bound",
            bound_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def bind_device(
    db: DBSession, session: SessionModel, device: Device, role: str = "auxiliary"
) -> Tuple[SessionDevice, bool]:
    """绑定设备到会话；已 bound 的重复绑定幂等返回 (binding, False)。"""
    binding = (
        db.query(SessionDevice)
        .filter(
            SessionDevice.session_id == session.session_id,
            SessionDevice.device_id == device.device_id,
        )
        .first()
    )
    now = utcnow()
    if binding is None:
        binding = SessionDevice(
            session_id=session.session_id,
            device_id=device.device_id,
            role=role,
            status="bound",
            bound_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(binding)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            binding = (
                db.query(SessionDevice)
                .filter(
                    SessionDevice.session_id == session.session_id,
                    SessionDevice.device_id == device.device_id,
                )
                .one()
            )
            return binding, False
        db.refresh(binding)
        return binding, True

    if binding.status == "bound":
        return binding, False

    # 重新绑定（P0 只支持 bound 状态流转，不删除历史行）
    binding.status = "bound"
    binding.role = role
    binding.bound_at = now
    binding.unbound_at = None
    binding.updated_at = now
    db.commit()
    db.refresh(binding)
    return binding, True


def device_bound_to_session(
    db: DBSession, session: SessionModel, device_id: str
) -> bool:
    """判断设备是否属于该会话（主设备或已绑定辅助设备）。"""
    if session.device_id == device_id:
        return True
    return (
        db.query(SessionDevice)
        .filter(
            SessionDevice.session_id == session.session_id,
            SessionDevice.device_id == device_id,
            SessionDevice.status == "bound",
        )
        .first()
        is not None
    )


def allowed_session_types(device: Device) -> Optional[List[str]]:
    """按设备能力计算允许下发的会话类型（§6.7 G1）。

    返回 None 表示不做门控（旧设备未声明可映射能力）。
    """
    capabilities = device.capabilities or []
    if not isinstance(capabilities, list):
        return None
    allowed = [
        _CAPABILITY_SESSION_TYPES[c] for c in capabilities if c in _CAPABILITY_SESSION_TYPES
    ]
    return allowed or None


def get_active_session(
    db: DBSession,
    device_id: str,
    session_type: Optional[str] = None,
    allowed_types: Optional[List[str]] = None,
) -> Optional[SessionModel]:
    """查询设备当前未完成的会话（created/running），返回最新一个。

    设备（Atlas）通过此接口拿到由手机端创建的 session_id；
    无未完成会话时返回 None。allowed_types 用于能力门控（§6.7 G1）。
    """
    query = db.query(SessionModel).filter(
        SessionModel.device_id == device_id,
        SessionModel.status.in_(UNFINISHED_STATUSES),
    )
    if session_type is not None:
        query = query.filter(SessionModel.session_type == session_type)
    session = query.order_by(SessionModel.id.desc()).first()
    if session is None:
        return None
    # 能力门控（§6.7 G1）：最新未完成会话与设备能力不匹配时不下发，
    # 不回退到更早的兼容会话，避免设备跑错会话。
    if allowed_types is not None and session.session_type not in allowed_types:
        return None
    return session


def update_status(db: DBSession, session: SessionModel, new_status: str) -> SessionModel:
    """状态流转：created → running → completed / aborted。

    兼容客户端：输入 ``interrupted`` 一律落库为 ``aborted``，
    ``ended_reason`` 记录 ``client_interrupted``（P1 修复）。
    """
    now = utcnow()
    client_interrupted = new_status == "interrupted"
    if client_interrupted:
        new_status = "aborted"
    if new_status == session.status:
        return session
    if new_status == "running":
        if session.status in ("completed", "aborted"):
            raise ValueError("session already ended, cannot restart")
        if session.started_at is None:
            session.started_at = now
    elif new_status == "completed":
        if session.status == "completed":
            return session
        if session.started_at is None:
            session.started_at = now
        session.ended_at = now
        session.ended_reason = "completed"
    elif new_status == "aborted":
        if session.status in ("completed", "aborted"):
            return session
        if session.started_at is None:
            session.started_at = now
        session.ended_at = now
        session.ended_reason = (
            "client_interrupted" if client_interrupted else "user cancelled"
        )
    else:
        raise ValueError(f"invalid target status: {new_status}")

    session.status = new_status
    session.updated_at = now
    db.commit()
    db.refresh(session)
    logger.info("session %s status -> %s", session.session_id, new_status)
    return session


def mark_interrupted(
    db: DBSession,
    session: SessionModel,
    reason: str = "device unreachable",
    now: Optional[datetime] = None,
) -> SessionModel:
    """将未完成会话标记为 interrupted（云端兜底，不删除数据）。"""
    current = now or utcnow()
    if session.status not in UNFINISHED_STATUSES:
        return session
    session.status = "interrupted"
    if session.started_at is None:
        session.started_at = current
    session.ended_at = current
    session.ended_reason = reason
    session.updated_at = current
    db.commit()
    db.refresh(session)
    logger.info("session %s interrupted: %s", session.session_id, reason)
    return session


def _is_device_unreachable(
    device: Optional[Device], now: datetime, threshold_seconds: int
) -> bool:
    if device is None or device.last_seen is None:
        return True
    return (now - device.last_seen).total_seconds() > threshold_seconds


def reap_stale_sessions(db: DBSession, now: Optional[datetime] = None) -> List[str]:
    """回收僵尸会话：未完成（created/running）且设备心跳消失超过阈值的会话置 interrupted。

    返回被中断的 session_id 列表。
    """
    current = now or utcnow()
    threshold = get_settings().session_interrupt_after_seconds

    stale = (
        db.query(SessionModel)
        .filter(SessionModel.status.in_(UNFINISHED_STATUSES))
        .all()
    )
    # 只查询涉及的设备，避免全表扫描
    device_ids = {s.device_id for s in stale}
    devices = {
        d.device_id: d
        for d in db.query(Device).filter(Device.device_id.in_(device_ids)).all()
    }

    interrupted: List[str] = []
    for session in stale:
        if _is_device_unreachable(
            devices.get(session.device_id), current, threshold
        ):
            mark_interrupted(db, session, reason="device heartbeat lost", now=current)
            interrupted.append(session.session_id)
    return interrupted
