"""表情识别结果表：保存服务器重新计算的最终统计结果。"""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class EmotionResult(Base):
    __tablename__ = "emotion_results"
    # 防重复上传：session_id + client_request_id 唯一
    __table_args__ = (
        UniqueConstraint(
            "session_id", "client_request_id", name="uq_emotion_result_session_request"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), unique=True, index=True
    )
    # 防重复上传：session_id + client_request_id 幂等
    client_request_id: Mapped[str] = mapped_column(String(128), index=True)
    valid_expression_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    dominant_expression: Mapped[str] = mapped_column(String(32))
    average_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    face_coverage_percent: Mapped[float] = mapped_column(Float, default=0.0)
    result_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<EmotionResult session={self.session_id} dominant={self.dominant_expression}>"
