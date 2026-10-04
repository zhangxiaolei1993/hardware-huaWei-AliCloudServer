"""设备实时表情识别状态表（与设备 1:1）。

Atlas 在采集过程中约每 3 秒上报一次当前状态，服务器对同一设备只保留最新一行（upsert）。
与心跳相互独立：心跳(20s)决定 online/offline，本表只表示"当下识别到的表情"。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class DeviceEmotionStatus(Base):
    __tablename__ = "device_emotion_status"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id"), unique=True, index=True
    )
    face_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    expression_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    current_expression: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DeviceEmotionStatus device={self.device_id} "
            f"expression={self.current_expression} updated={self.updated_at}>"
        )
