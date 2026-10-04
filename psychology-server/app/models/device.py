"""通用设备表：任何设备（Atlas / 未来的心率、EEG 设备）都登记在这里。"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(128))
    device_type: Mapped[str] = mapped_column(String(64))
    manufacturer: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    firmware_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="offline")
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    device_token: Mapped[str] = mapped_column(String(128), unique=True)
    capabilities: Mapped[Any] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=None, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=None, nullable=False)

    def __repr__(self) -> str:
        return f"<Device {self.device_id} status={self.status}>"
