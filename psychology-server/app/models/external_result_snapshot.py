"""外部测评结果快照（v1.4 §11.3 / M3）。

A 只保存 B 端返回的**聚合白名单**结果，绝不生成或改写业务评分。

- 幂等：``UNIQUE(idempotency_key)`` + ``UNIQUE(session_id, questionnaire_code, result_version)``；
- ``trust_level`` 由服务器计算，一期恒为 ``client_relayed``；
- ``dimensions`` 固定 ``raw_map_v1``，``radar_norm`` 固定 ``radar_norm_v1``，两者分别存储。
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
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


class ExternalResultSnapshot(Base):
    __tablename__ = "external_result_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_result_snapshots_idempotency_key"
        ),
        UniqueConstraint(
            "session_id",
            "questionnaire_code",
            "result_version",
            name="uq_result_snapshots_session_code_version",
        ),
        CheckConstraint(
            "trust_level IN ('client_relayed','server_signed','server_verified')",
            name="ck_result_snapshots_trust_level",
        ),
        Index(
            "ix_result_snapshots_session_code",
            "session_id",
            "questionnaire_code",
        ),
        # 0012 科研域：逻辑关联到 assessment / participant，可空
        Index("ix_external_result_snapshots_assessment", "assessment_id"),
        Index("ix_external_result_snapshots_participant", "participant_id"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), nullable=False, index=True
    )
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    source_result_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # 0012 科研域：所属测评与被试（逻辑关联，历史快照保持 NULL）
    assessment_id: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, index=True
    )
    participant_id: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, index=True
    )
    questionnaire_code: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    # B 现状不返回量表版本，必须可空
    questionnaire_version: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    scale_algorithm_version: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    # 由客户端维护（B 不提供版本号）
    result_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_snapshot_id: Mapped[Optional[str]] = mapped_column(
        String(40),
        ForeignKey("external_result_snapshots.snapshot_id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    total_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    scoring_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    state_update_status: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    scale_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    scale_question_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    dimensions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    radar_norm: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    level_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    recommendation_code: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    ai_profile_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trust_level: Mapped[str] = mapped_column(
        String(24), nullable=False, default="client_relayed"
    )
    signature: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    signature_algorithm: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    source_payload_sha256: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ExternalResultSnapshot {self.snapshot_id} "
            f"session={self.session_id} code={self.questionnaire_code} "
            f"v{self.result_version}>"
        )
