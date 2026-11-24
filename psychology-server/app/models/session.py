"""通用会话表：统一管理一次测评/采集过程。

v1.4 扩展（M1）：
- source_system：会话来源（xin_tan_tan / standalone / custom），默认 standalone
- external_session_id：外部系统会话编号 N1（只读、原样保存、不设唯一约束）
- client_request_id：创建幂等键，与 source_system 组成唯一约束
- 锚点字段：设备采集时刻与服务器时间对齐用
- late_upload_deadline：断网补传截止时间（ended_at 后默认 24h）
- ended_reason：结束原因（完成 / 中断 / 用户取消）
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class SessionModel(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # 创建幂等：同一来源 + 同一客户端请求 ID 只创建一次（NULL 允许重复）
        UniqueConstraint(
            "source_system", "client_request_id", name="uq_sessions_source_request"
        ),
        Index("ix_sessions_device_status", "device_id", "status"),
        Index("ix_sessions_status_ended", "status", "ended_at"),
        Index("ix_sessions_subject_created", "subject_ref", "created_at"),
        # 0012 科研域：逻辑关联，不建外键（sessions 为高频 P0 表）
        Index("ix_sessions_participant", "participant_id"),
        Index("ix_sessions_project_created", "project_id", "created_at"),
        Index("ix_sessions_participant_started", "participant_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id"), index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # M7（§17.3）：外部系统受试者 ID（来自服务器 B），原样保存、不对外出口
    external_user_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    # M7：脱敏受试者标识 = HMAC_SHA256(RESEARCH_SALT, source:external_user_id)[:16]
    subject_ref: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    # 是否因缺少 external_user_id 回退使用 user_id 计算（P0-9 标记）
    deprecated_identity_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    # ---- 0012 科研域：被试 / 项目 / 组别（逻辑关联，可空，旧会话保持 NULL）----
    participant_id: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, index=True
    )
    project_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # 施测时组别；被试后续换组不回写
    group_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # live（实时采集）/ legacy_import（无 S1 的离线历史合成会话）
    data_origin: Mapped[str] = mapped_column(
        String(16), default="live", server_default="live", nullable=False
    )
    # emotion / multimodal / assessment / physiological / speech / custom
    session_type: Mapped[str] = mapped_column(String(32), default="emotion")
    # 会话来源；默认 standalone（兼容旧客户端）
    source_system: Mapped[str] = mapped_column(
        String(32), default="standalone", server_default="standalone"
    )
    # 外部系统会话编号 N1：不设唯一约束，同一 N1 可关联多个 S1
    external_session_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    # 创建幂等键：同一 source_system 下唯一
    client_request_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    # 锚点（§8.2）：服务器统一锚定，设备本地单调时钟仅作匹配提示
    anchor_utc_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    anchor_monotonic_ms: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    anchor_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    anchor_uncertainty_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    # 断网补传截止时间
    late_upload_deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    # completed / device unreachable / user cancelled 等
    ended_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # created / running / completed / interrupted / aborted
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Session {self.session_id} type={self.session_type} "
            f"source={self.source_system} status={self.status}>"
        )
