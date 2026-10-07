"""会话服务：创建 / 查询 / 状态流转 / 僵尸会话回收。"""
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import utcnow
from app.models.device import Device
from app.models.session import SessionModel

logger = get_logger(__name__)

VALID_STATUSES = ("created", "running", "completed", "interrupted")
# 未完成、可能被回收的状态
UNFINISHED_STATUSES = ("created", "running")


def create_session(
    db: DBSession,
    device: Device,
    session_type: str = "emotion",
    user_id: Optional[str] = None,
) -> SessionModel:
    now = utcnow()
    session = SessionModel(
        session_id=uuid.uuid4().hex,
        device_id=device.device_id,
        user_id=user_id,
        session_type=session_type,
        status="created",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    logger.info(
        "session %s created by device %s (type=%s)",
        session.session_id,
        device.device_id,
        session_type,
    )
    return session


def get_session(db: DBSession, session_id: str) -> Optional[SessionModel]:
    return db.query(SessionModel).filter(SessionModel.session_id == session_id).first()


def get_active_session(
    db: DBSession, device_id: str, session_type: Optional[str] = None
) -> Optional[SessionModel]:
    """查询设备当前未完成的会话（created/running），返回最新一个。

    设备（Atlas）通过此接口拿到由手机端创建的 session_id；
    无未完成会话时返回 None。
    """
    query = db.query(SessionModel).filter(
        SessionModel.device_id == device_id,
        SessionModel.status.in_(UNFINISHED_STATUSES),
    )
    if session_type is not None:
        query = query.filter(SessionModel.session_type == session_type)
    return query.order_by(SessionModel.id.desc()).first()


def update_status(db: DBSession, session: SessionModel, new_status: str) -> SessionModel:
    """状态流转：created → running → completed。"""
    now = utcnow()
    if new_status == session.status:
        return session
    if new_status == "running":
        if session.status == "completed":
            raise ValueError("session already completed, cannot restart")
        if session.started_at is None:
            session.started_at = now
    elif new_status == "completed":
        if session.status == "completed":
            return session
        if session.started_at is None:
            session.started_at = now
        session.ended_at = now
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
