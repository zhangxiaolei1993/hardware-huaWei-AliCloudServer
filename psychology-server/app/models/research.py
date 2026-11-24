"""科研管理域数据表（第三阶段 0012_research_core）。

本模块只服务科研版云端：项目、实验组、被试、测评、逐题作答、AI 对话、
异步下载任务。现有 P0 表（sessions / external_result_snapshots）只新增可空列，
不改变原表约束。

账号与权限本轮不做：不建成员表，不做 owner/editor/viewer；所有接口沿用
`X-Api-Key`。项目级权限校验留空，后续接入主站凭证时再补。
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ResearchProject(Base):
    __tablename__ = "research_projects"
    __table_args__ = (Index("ix_research_projects_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    code: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResearchGroup(Base):
    __tablename__ = "research_groups"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_research_groups_project_name"),
        Index("ix_research_groups_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("research_projects.project_id"), nullable=False, index=True
    )
    # 用户自定义组名（如「实验组 A」「对照组」）；项目内唯一
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    group_type: Mapped[str] = mapped_column(
        String(16), default="experimental", server_default="experimental", nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResearchParticipant(Base):
    __tablename__ = "research_participants"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "participant_code",
            name="uq_research_participants_project_code",
        ),
        UniqueConstraint(
            "project_id",
            "source_local_id",
            name="uq_research_participants_project_local",
        ),
        Index(
            "ix_research_participants_project_status", "project_id", "status"
        ),
        Index(
            "ix_research_participants_project_group", "project_id", "group_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    participant_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("research_projects.project_id"), nullable=False, index=True
    )
    group_id: Mapped[Optional[str]] = mapped_column(
        String(40),
        ForeignKey("research_groups.group_id"),
        nullable=True,
        index=True,
    )
    participant_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 用户已拍板：姓名、电话同样上云
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    school: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    grade: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_local_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    client_request_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResearchAssessment(Base):
    __tablename__ = "research_assessments"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "questionnaire_code",
            name="uq_research_assessments_session_code",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_research_assessments_idempotency"
        ),
        UniqueConstraint(
            "current_snapshot_id", name="uq_research_assessments_current_snapshot"
        ),
        Index(
            "ix_research_assessments_project_participant",
            "project_id",
            "participant_id",
        ),
        Index(
            "ix_research_assessments_participant_code_reported",
            "participant_id",
            "questionnaire_code",
            "reported_at",
        ),
        Index(
            "ix_research_assessments_project_created", "project_id", "created_at"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    assessment_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("research_projects.project_id"), nullable=False, index=True
    )
    participant_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("research_participants.participant_id"),
        nullable=False,
        index=True,
    )
    group_id: Mapped[Optional[str]] = mapped_column(
        String(40), ForeignKey("research_groups.group_id"), nullable=True, index=True
    )
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), nullable=False, index=True
    )
    questionnaire_code: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    questionnaire_version: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True
    )
    scale_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    assessment_type: Mapped[str] = mapped_column(
        String(32), default="scale", server_default="scale", nullable=False
    )
    assessment_status: Mapped[str] = mapped_column(
        String(16), default="completed", server_default="completed", nullable=False
    )
    current_snapshot_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    result_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(
        String(16), default="live", server_default="live", nullable=False
    )
    source_local_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResearchAssessmentItem(Base):
    __tablename__ = "research_assessment_items"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "result_version",
            "question_id",
            name="uq_research_assessment_items_assessment_version_question",
        ),
        Index(
            "ix_research_assessment_items_assessment", "assessment_id", "result_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    assessment_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("research_assessments.assessment_id"),
        nullable=False,
        index=True,
    )
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    question_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    answer_value: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    response_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(
        String(16), default="live", server_default="live", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResearchConversation(Base):
    __tablename__ = "research_conversations"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_conversation_id",
            name="uq_research_conversations_source_external",
        ),
        Index("ix_research_conversations_project", "project_id"),
        Index("ix_research_conversations_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("research_projects.project_id"), nullable=False
    )
    participant_id: Mapped[Optional[str]] = mapped_column(
        String(40),
        ForeignKey("research_participants.participant_id"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("sessions.session_id"),
        nullable=False,
        index=True,
    )
    assessment_id: Mapped[Optional[str]] = mapped_column(
        String(40),
        ForeignKey("research_assessments.assessment_id"),
        nullable=True,
        index=True,
    )
    external_conversation_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(
        String(32), default="xin_tan_tan", server_default="xin_tan_tan", nullable=False
    )
    message_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResearchConversationMessage(Base):
    __tablename__ = "research_conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "seq",
            name="uq_research_conversation_messages_conv_seq",
        ),
        Index(
            "ix_research_conversation_messages_conversation",
            "conversation_id",
            "seq",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("research_conversations.conversation_id"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(16), default="text", server_default="text", nullable=False
    )
    attachment_ref: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResearchExportJob(Base):
    __tablename__ = "research_export_jobs"
    __table_args__ = (
        Index("ix_research_export_jobs_status_created", "status", "created_at"),
        Index("ix_research_export_jobs_user_created", "requested_by", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    export_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("research_projects.project_id"), nullable=False, index=True
    )
    # 本轮无账号体系：留空；仅记录来源 IP 作为最低限度运维线索
    requested_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    requested_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    filters_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued", nullable=False
    )
    progress_current: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    progress_total: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    counts_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True
    )
