"""表情识别原始 timeline 数据：必须保留原始时间序列，不只存统计结果。"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class EmotionRecord(Base):
    __tablename__ = "emotion_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), index=True
    )
    relative_seconds: Mapped[float] = mapped_column(Float)
    expression: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<EmotionRecord session={self.session_id} "
            f"t={self.relative_seconds} expr={self.expression}>"
        )
