"""会话服务：创建 / 查询 / 状态流转。"""
import uuid
from typing import Optional

from sqlalchemy.orm import Session as DBSession

from app.core.logging import get_logger
from app.db.database import utcnow
from app.models.device import Device
from app.models.session import SessionModel

logger = get_logger(__name__)

VALID_STATUSES = ("created", "running", "completed")


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
        SessionModel.status.in_(("created", "running")),
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
