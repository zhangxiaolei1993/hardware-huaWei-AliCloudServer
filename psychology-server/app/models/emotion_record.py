"""表情识别原始 timeline 数据：必须保留原始时间序列，不只存统计结果。

M6（v1.4 §11.4）新增 7 个时间/质量字段；旧固件不传，全部可空，
``quality`` 由服务器推导（客户端声明一律忽略）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class EmotionRecord(Base):
    __tablename__ = "emotion_records"
    __table_args__ = (
        Index("ix_emotion_records_session_quality", "session_id", "quality"),
        Index(
            "ix_emotion_records_session_stream_time",
            "session_id",
            "stream_id",
            "relative_seconds",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), index=True
    )
    device_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    stream_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    relative_seconds: Mapped[float] = mapped_column(Float)
    expression: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # ---- M6：时间与质量字段（全部由服务器写入）----
    device_timestamp: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    monotonic_ms: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    clock_offset_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    frame_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quality: Mapped[str] = mapped_column(
        String(16), nullable=False, default="legacy", server_default="legacy"
    )
    quality_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    protocol_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<EmotionRecord session={self.session_id} "
            f"t={self.relative_seconds} expr={self.expression}>"
        )
