"""设备认证：token 生成与校验。"""
import hmac
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.models.device import Device


def generate_device_token() -> str:
    return secrets.token_hex(32)


def verify_token(stored: str, provided: str) -> bool:
    return hmac.compare_digest(stored, provided)


def get_current_device(
    x_device_id: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
    db: DBSession = Depends(get_db),
) -> Device:
    """设备认证依赖：请求头携带 X-Device-Id / X-Device-Token。"""
    if not x_device_id or not x_device_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Device-Id or X-Device-Token header",
        )
    device = db.query(Device).filter(Device.device_id == x_device_id).first()
    if device is None or not verify_token(device.device_token, x_device_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid device credentials",
        )
    return device
