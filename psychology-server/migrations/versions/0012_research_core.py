"""科研版云端：项目 / 实验组 / 被试 / 测评 / 逐题作答 / 对话 / 下载任务

Revision ID: 0012_research_core
Revises: 0011_p1d_emotion_result_versions
Create Date: 2026-11-23

Additive only：
- 新建 8 张科研域表（不含账号成员表；本轮不做账号与权限）；
- ``sessions`` 增加 4 个可空列（``data_origin`` 非空但有 server_default）；
- ``external_result_snapshots`` 增加 2 个可空列。

不在迁移中做业务回填；历史数据由 ``POST /api/v1/research/import/legacy``
（运维用途）显式导入。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_research_core"
down_revision: Union[str, None] = "0011_p1d_emotion_result_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. 现有 P0 表：只加可空列（sessions.data_origin 有 server_default）
    # ------------------------------------------------------------------
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("participant_id", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("project_id", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("group_id", sa.String(40), nullable=True))
        batch_op.add_column(
            sa.Column(
                "data_origin",
                sa.String(16),
                nullable=False,
                server_default="live",
            )
        )
        batch_op.create_index("ix_sessions_participant", ["participant_id"])
        batch_op.create_index("ix_sessions_participant_id", ["participant_id"])
        batch_op.create_index(
            "ix_sessions_project_created", ["project_id", "created_at"]
        )
        batch_op.create_index(
            "ix_sessions_participant_started", ["participant_id", "started_at"]
        )

    with op.batch_alter_table("external_result_snapshots", schema=None) as batch_op:
        batch_op.add_column(sa.Column("assessment_id", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("participant_id", sa.String(40), nullable=True))
        batch_op.create_index(
            "ix_external_result_snapshots_assessment_id", ["assessment_id"]
        )
        batch_op.create_index(
            "ix_external_result_snapshots_participant_id", ["participant_id"]
        )
        batch_op.create_index(
            "ix_external_result_snapshots_assessment", ["assessment_id"]
        )
        batch_op.create_index(
            "ix_external_result_snapshots_participant", ["participant_id"]
        )

    # ------------------------------------------------------------------
    # 2. 科研域新表
    # ------------------------------------------------------------------
    op.create_table(
        "research_projects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("code", sa.String(32), nullable=True),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("code", name="uq_research_projects_code"),
    )
    op.create_index(
        "ix_research_projects_project_id",
        "research_projects",
        ["project_id"],
        unique=True,
    )
    op.create_index("ix_research_projects_name", "research_projects", ["name"])
    op.create_index("ix_research_projects_status", "research_projects", ["status"])

    op.create_table(
        "research_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column(
            "group_type",
            sa.String(16),
            nullable=False,
            server_default="experimental",
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.project_id"],
            name="fk_research_groups_project",
        ),
        sa.UniqueConstraint(
            "project_id", "name", name="uq_research_groups_project_name"
        ),
    )
    op.create_index(
        "ix_research_groups_group_id",
        "research_groups",
        ["group_id"],
        unique=True,
    )
    op.create_index("ix_research_groups_project_id", "research_groups", ["project_id"])
    op.create_index(
        "ix_research_groups_project_status", "research_groups", ["project_id", "status"]
    )

    op.create_table(
        "research_participants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("participant_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("group_id", sa.String(40), nullable=True),
        sa.Column("participant_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("gender", sa.String(16), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("school", sa.String(128), nullable=True),
        sa.Column("grade", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("source_local_id", sa.String(64), nullable=True),
        sa.Column("client_request_id", sa.String(128), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.project_id"],
            name="fk_research_participants_project",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["research_groups.group_id"],
            name="fk_research_participants_group",
        ),
        sa.UniqueConstraint(
            "project_id",
            "participant_code",
            name="uq_research_participants_project_code",
        ),
        sa.UniqueConstraint(
            "project_id",
            "source_local_id",
            name="uq_research_participants_project_local",
        ),
    )
    op.create_index(
        "ix_research_participants_participant_id",
        "research_participants",
        ["participant_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_participants_project_id",
        "research_participants",
        ["project_id"],
    )
    op.create_index(
        "ix_research_participants_group_id", "research_participants", ["group_id"]
    )
    op.create_index(
        "ix_research_participants_participant_code",
        "research_participants",
        ["participant_code"],
    )
    op.create_index(
        "ix_research_participants_source_local_id",
        "research_participants",
        ["source_local_id"],
    )
    op.create_index(
        "ix_research_participants_client_request_id",
        "research_participants",
        ["client_request_id"],
    )
    op.create_index(
        "ix_research_participants_project_status",
        "research_participants",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_research_participants_project_group",
        "research_participants",
        ["project_id", "group_id"],
    )

    op.create_table(
        "research_assessments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("assessment_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("participant_id", sa.String(40), nullable=False),
        sa.Column("group_id", sa.String(40), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("questionnaire_code", sa.String(64), nullable=False),
        sa.Column("questionnaire_version", sa.String(32), nullable=True),
        sa.Column("scale_name", sa.String(128), nullable=True),
        sa.Column(
            "assessment_type", sa.String(32), nullable=False, server_default="scale"
        ),
        sa.Column(
            "assessment_status",
            sa.String(16),
            nullable=False,
            server_default="completed",
        ),
        sa.Column("current_snapshot_id", sa.String(64), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("reported_at", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="live"),
        sa.Column("source_local_key", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.project_id"],
            name="fk_research_assessments_project",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"], ["research_participants.participant_id"],
            name="fk_research_assessments_participant",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["research_groups.group_id"],
            name="fk_research_assessments_group",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"],
            name="fk_research_assessments_session",
        ),
        sa.UniqueConstraint(
            "session_id",
            "questionnaire_code",
            name="uq_research_assessments_session_code",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_research_assessments_idempotency"
        ),
        sa.UniqueConstraint(
            "current_snapshot_id",
            name="uq_research_assessments_current_snapshot",
        ),
    )
    op.create_index(
        "ix_research_assessments_assessment_id",
        "research_assessments",
        ["assessment_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_assessments_project_id",
        "research_assessments",
        ["project_id"],
    )
    op.create_index(
        "ix_research_assessments_participant_id",
        "research_assessments",
        ["participant_id"],
    )
    op.create_index(
        "ix_research_assessments_group_id", "research_assessments", ["group_id"]
    )
    op.create_index(
        "ix_research_assessments_session_id", "research_assessments", ["session_id"]
    )
    op.create_index(
        "ix_research_assessments_questionnaire_code",
        "research_assessments",
        ["questionnaire_code"],
    )
    op.create_index(
        "ix_research_assessments_reported_at",
        "research_assessments",
        ["reported_at"],
    )
    op.create_index(
        "ix_research_assessments_source_local_key",
        "research_assessments",
        ["source_local_key"],
    )
    op.create_index(
        "ix_research_assessments_project_participant",
        "research_assessments",
        ["project_id", "participant_id"],
    )
    op.create_index(
        "ix_research_assessments_participant_code_reported",
        "research_assessments",
        ["participant_id", "questionnaire_code", "reported_at"],
    )
    op.create_index(
        "ix_research_assessments_project_created",
        "research_assessments",
        ["project_id", "created_at"],
    )

    op.create_table(
        "research_assessment_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String(40), nullable=False),
        sa.Column("assessment_id", sa.String(40), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("question_index", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(32), nullable=True),
        sa.Column("answer_value", sa.JSON(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Float(), nullable=True),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="live"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["research_assessments.assessment_id"],
            name="fk_research_assessment_items_assessment",
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "result_version",
            "question_id",
            name="uq_research_assessment_items_assessment_version_question",
        ),
    )
    op.create_index(
        "ix_research_assessment_items_item_id",
        "research_assessment_items",
        ["item_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_assessment_items_assessment_id",
        "research_assessment_items",
        ["assessment_id"],
    )
    op.create_index(
        "ix_research_assessment_items_question_id",
        "research_assessment_items",
        ["question_id"],
    )
    op.create_index(
        "ix_research_assessment_items_assessment",
        "research_assessment_items",
        ["assessment_id", "result_version"],
    )

    op.create_table(
        "research_conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("participant_id", sa.String(40), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("assessment_id", sa.String(40), nullable=True),
        sa.Column("external_conversation_id", sa.String(128), nullable=True),
        sa.Column(
            "source", sa.String(32), nullable=False, server_default="xin_tan_tan"
        ),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.project_id"],
            name="fk_research_conversations_project",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"], ["research_participants.participant_id"],
            name="fk_research_conversations_participant",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"],
            name="fk_research_conversations_session",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["research_assessments.assessment_id"],
            name="fk_research_conversations_assessment",
        ),
        sa.UniqueConstraint(
            "source",
            "external_conversation_id",
            name="uq_research_conversations_source_external",
        ),
    )
    op.create_index(
        "ix_research_conversations_conversation_id",
        "research_conversations",
        ["conversation_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_conversations_participant_id",
        "research_conversations",
        ["participant_id"],
    )
    op.create_index(
        "ix_research_conversations_session_id",
        "research_conversations",
        ["session_id"],
    )
    op.create_index(
        "ix_research_conversations_assessment_id",
        "research_conversations",
        ["assessment_id"],
    )
    op.create_index(
        "ix_research_conversations_external_conversation_id",
        "research_conversations",
        ["external_conversation_id"],
    )
    op.create_index(
        "ix_research_conversations_project", "research_conversations", ["project_id"]
    )
    op.create_index(
        "ix_research_conversations_session", "research_conversations", ["session_id"]
    )

    op.create_table(
        "research_conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_type", sa.String(16), nullable=False, server_default="text"
        ),
        sa.Column("attachment_ref", sa.String(256), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["research_conversations.conversation_id"],
            name="fk_research_conversation_messages_conversation",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "seq",
            name="uq_research_conversation_messages_conv_seq",
        ),
    )
    op.create_index(
        "ix_research_conversation_messages_message_id",
        "research_conversation_messages",
        ["message_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_conversation_messages_conversation_id",
        "research_conversation_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_research_conversation_messages_conversation",
        "research_conversation_messages",
        ["conversation_id", "seq"],
    )

    op.create_table(
        "research_export_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("export_id", sa.String(40), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=True),
        sa.Column("requested_ip", sa.String(64), nullable=True),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "progress_current", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counts_json", sa.JSON(), nullable=True),
        sa.Column("file_path", sa.String(512), nullable=True),
        sa.Column("file_name", sa.String(256), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["research_projects.project_id"],
            name="fk_research_export_jobs_project",
        ),
    )
    op.create_index(
        "ix_research_export_jobs_export_id",
        "research_export_jobs",
        ["export_id"],
        unique=True,
    )
    op.create_index(
        "ix_research_export_jobs_project_id", "research_export_jobs", ["project_id"]
    )
    op.create_index(
        "ix_research_export_jobs_status_created",
        "research_export_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_research_export_jobs_user_created",
        "research_export_jobs",
        ["requested_by", "created_at"],
    )
    op.create_index(
        "ix_research_export_jobs_expires_at", "research_export_jobs", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("research_export_jobs")
    op.drop_table("research_conversation_messages")
    op.drop_table("research_conversations")
    op.drop_table("research_assessment_items")
    op.drop_table("research_assessments")
    op.drop_table("research_participants")
    op.drop_table("research_groups")
    op.drop_table("research_projects")

    with op.batch_alter_table("external_result_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_external_result_snapshots_participant")
        batch_op.drop_index("ix_external_result_snapshots_assessment")
        batch_op.drop_index("ix_external_result_snapshots_participant_id")
        batch_op.drop_index("ix_external_result_snapshots_assessment_id")
        batch_op.drop_column("participant_id")
        batch_op.drop_column("assessment_id")

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_sessions_participant_started")
        batch_op.drop_index("ix_sessions_project_created")
        batch_op.drop_index("ix_sessions_participant_id")
        batch_op.drop_index("ix_sessions_participant")
        batch_op.drop_column("data_origin")
        batch_op.drop_column("group_id")
        batch_op.drop_column("project_id")
        batch_op.drop_column("participant_id")
