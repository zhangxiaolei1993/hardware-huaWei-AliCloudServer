"""手机 App 与设备的连接记录表。

由手机端调用 connect 时写入，用于记录"哪台手机连了哪台设备"。
当前只做记录；未来可在此基础上做鉴权与"一台设备同时只服务一个用户"的占用控制。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class DeviceConnection(Base):
    __tablename__ = "device_connections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id"), index=True
    )
    # 手机端生成的安装/实例唯一标识
    app_client_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # connected / disconnected
    status: Mapped[str] = mapped_column(String(16), default="connected", index=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DeviceConnection {self.connection_id} device={self.device_id} "
            f"app={self.app_client_id} status={self.status}>"
        )
