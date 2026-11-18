"""Timeline 时间轴事件表（v1.4 §7.3 / M2）。

与 ``emotion_records`` 共用同一 ``relative_seconds`` 原点（sessions.anchor_utc_at），
用于把量表过程事件与设备采集数据对齐到同一时间轴。

P0 约定：
- 事件级幂等：``UNIQUE(session_id, source, event_id)``；
- ``payload`` 是受控白名单，不接收 ``answer_value`` 等答案类字段；
- ``quality`` / ``quality_info`` 由服务器计算，客户端声明一律忽略。
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

#: SQLite 下 BIGINT 主键不会自增，统一降级为 INTEGER
_PK_TYPE = BigInteger().with_variant(Integer, "sqlite")


class TimelineEvent(Base):
    __tablename__ = "timeline_events"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "source",
            "event_id",
            name="uq_timeline_events_session_source_event",
        ),
        Index("ix_timeline_events_session_occurred", "session_id", "occurred_at"),
        Index(
            "ix_timeline_events_session_type_occurred",
            "session_id",
            "event_type",
            "occurred_at",
        ),
        Index(
            "ix_timeline_events_session_question_occurred",
            "session_id",
            "question_id",
            "occurred_at",
        ),
        Index("ix_timeline_events_session_quality", "session_id", "quality"),
        Index("ix_timeline_events_client_request", "client_request_id"),
    )

    id: Mapped[int] = mapped_column(_PK_TYPE, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), index=True
    )
    # 纯量表事件为空；设备事件必填
    device_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("devices.device_id"), nullable=True, index=True
    )
    # 关联数据流；data_streams 表尚未建立（P1），此处只保存标识，不加外键
    stream_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    # device / app / server
    source: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    relative_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    device_timestamp: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    monotonic_ms: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    clock_offset_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    time_uncertainty_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    server_received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    questionnaire_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    questionnaire_version: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    question_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    question_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    protocol_version: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # device / estimated / missing / legacy（§8.5）
    quality: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<TimelineEvent {self.event_id} type={self.event_type} "
            f"t={self.relative_seconds}>"
        )
