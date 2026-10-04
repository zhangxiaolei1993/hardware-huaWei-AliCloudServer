"""通用会话表：统一管理一次测评/采集过程。

session_type 当前只有 "emotion"；未来才会出现 heart_rate / eeg / multimodal。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id"), index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    session_type: Mapped[str] = mapped_column(String(32), default="emotion")
    # created / running / completed
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<Session {self.session_id} type={self.session_type} status={self.status}>"
