"""表情结果版本表（P1-D）：只追加的结果版本历史。

设计要点：
- ``emotion_results`` 仍保存每会话当前值（``UNIQUE(session_id)`` 不变），本表只追加；
- 同一会话内 ``version`` 单调递增，``UNIQUE(session_id, version)`` 防止并发重复写入；
- ``source_sha256`` + ``source_record_count`` 可追溯到生成该版本时的原始记录集合，
  不保存原始记录副本；
- 版本内容随行保存（``result_json``），旧版本不依赖当前 ``emotion_results``；
- 状态只有 ``generated``（当前）与 ``superseded``（被更新版本取代）。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class EmotionResultVersion(Base):
    __tablename__ = "emotion_result_versions"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "version",
            name="uq_emotion_result_versions_session_version",
        ),
        Index(
            "ix_emotion_result_versions_session_sha256",
            "session_id",
            "source_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), nullable=False, index=True
    )
    result_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("emotion_results.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 生成该版本的算法版本，算法调整时递增以区分口径
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # generated（当前版本）/ superseded（已被更新版本取代）
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # first_import（首次导入）/ recompute（追加上传后重算）
    # / manual_recompute（手动重算）/ backfill（历史回填）
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False)
    # 生成时参与的原始记录条数与摘要，用于回溯当时的源数据
    source_record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # 该版本的完整计算结果（与当时 emotion_results.result_json 一致）
    result_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<EmotionResultVersion session={self.session_id} "
            f"v{self.version} status={self.status}>"
        )
