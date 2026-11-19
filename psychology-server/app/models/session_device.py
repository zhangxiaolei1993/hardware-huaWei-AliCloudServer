"""会话-设备绑定表（v1.4 §11.3 / M5）。

一个会话可绑定多台设备（``primary`` / ``auxiliary``）；会话创建时自动绑定
主设备，其他设备通过 ``POST /sessions/{sid}/devices`` 绑定。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class SessionDevice(Base):
    __tablename__ = "session_devices"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "device_id", name="uq_session_devices_session_device"
        ),
        Index("ix_session_devices_device_status", "device_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id"), nullable=False, index=True
    )
    # primary / auxiliary
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="primary")
    # bound / unbound：会话级绑定状态，与会话生命周期绑定；
    # 不是设备连接状态（后者见 device_connections.connected/disconnected）。
    # 会话完成或手机 disconnect 都不改变本列，否则会阻断迟到上传。
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="bound")
    bound_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    unbound_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<SessionDevice session={self.session_id} device={self.device_id} "
            f"role={self.role}>"
        )
