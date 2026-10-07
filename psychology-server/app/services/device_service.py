"""设备服务：注册 / 查询 / 心跳 / App 连接管理。"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import generate_device_token
from app.db.database import utcnow
from app.models.device import Device
from app.models.device_connection import DeviceConnection
from app.schemas.device import DeviceRegisterRequest

logger = get_logger(__name__)


def register_device(db: DBSession, req: DeviceRegisterRequest) -> tuple[Device, bool]:
    """注册设备。重复注册时返回已有设备（幂等），不重复插入。

    返回 (device, created)。
    """
    device = db.query(Device).filter(Device.device_id == req.device_id).first()
    if device is not None:
        logger.info("device %s already registered, return existing", req.device_id)
        return device, False

    now = utcnow()
    device = Device(
        device_id=req.device_id,
        device_name=req.device_name,
        device_type=req.device_type,
        manufacturer=req.manufacturer,
        model=req.model,
        firmware_version=req.firmware_version,
        status="offline",
        device_token=generate_device_token(),
        capabilities=req.capabilities,
        created_at=now,
        updated_at=now,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    logger.info("device %s registered", req.device_id)
    return device, True


def get_device(db: DBSession, device_id: str) -> Optional[Device]:
    return db.query(Device).filter(Device.device_id == device_id).first()


def get_connection(db: DBSession, connection_id: str) -> Optional[DeviceConnection]:
    return (
        db.query(DeviceConnection)
        .filter(DeviceConnection.connection_id == connection_id)
        .first()
    )


def effective_status(device: Device) -> str:
    """惰性判断在线状态：超过 N 秒无心跳视为 offline。"""
    settings = get_settings()
    if device.last_seen is None:
        return "offline"
    elapsed = (utcnow() - device.last_seen).total_seconds()
    online = elapsed <= settings.device_offline_after_seconds
    return "online" if online else "offline"


def heartbeat(
    db: DBSession, device: Device, firmware_version: Optional[str] = None
) -> Device:
    now = utcnow()
    device.last_seen = now
    device.status = "online"
    if firmware_version:
        device.firmware_version = firmware_version
    device.updated_at = now
    db.commit()
    db.refresh(device)
    logger.info("heartbeat from %s at %s", device.device_id, now.isoformat())
    return device


def is_online_since(last_seen: Optional[datetime], now: datetime) -> bool:
    if last_seen is None:
        return False
    return (now - last_seen).total_seconds() <= get_settings().device_offline_after_seconds


# ---------- 手机 App 连接 / 断开 ----------
def connect_device(
    db: DBSession,
    device: Device,
    app_client_id: str,
    user_id: Optional[str] = None,
) -> DeviceConnection:
    """手机连接设备并记录。同一手机对同一设备的活跃连接幂等复用，不重复建记录。"""
    active = (
        db.query(DeviceConnection)
        .filter(
            DeviceConnection.device_id == device.device_id,
            DeviceConnection.app_client_id == app_client_id,
            DeviceConnection.status == "connected",
        )
        .first()
    )
    if active is not None:
        logger.info(
            "app %s already connected to %s, reuse %s",
            app_client_id,
            device.device_id,
            active.connection_id,
        )
        return active

    now = utcnow()
    connection = DeviceConnection(
        connection_id=uuid.uuid4().hex,
        device_id=device.device_id,
        app_client_id=app_client_id,
        user_id=user_id,
        status="connected",
        connected_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    logger.info(
        "app %s connected to %s, device_status=%s, connection=%s",
        app_client_id,
        device.device_id,
        effective_status(device),
        connection.connection_id,
    )
    return connection


def disconnect_device(db: DBSession, connection_id: str) -> Optional[DeviceConnection]:
    """断开连接。记录不存在返回 None；已断开则幂等返回。"""
    connection = (
        db.query(DeviceConnection)
        .filter(DeviceConnection.connection_id == connection_id)
        .first()
    )
    if connection is None:
        return None
    if connection.status == "disconnected":
        return connection

    now = utcnow()
    connection.status = "disconnected"
    connection.disconnected_at = now
    connection.updated_at = now
    db.commit()
    db.refresh(connection)
    logger.info("connection %s disconnected at %s", connection_id, now.isoformat())
    return connection
