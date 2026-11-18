"""baseline：v1.4 之前的现有 6 张表（与生产库 .schema 一致）

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-17

说明：
- 本 revision 仅用于给已存在的数据库 stamp（§20.3.1），不应对生产库执行 upgrade。
- 结构严格对应 M1 之前的真实 DDL（由 ORM create_all 生成）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=128), nullable=False),
        sa.Column("device_type", sa.String(length=64), nullable=False),
        sa.Column("manufacturer", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("firmware_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("device_token", sa.String(length=128), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_token"),
    )
    op.create_index("ix_devices_device_id", "devices", ["device_id"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("session_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_device_id", "sessions", ["device_id"], unique=False)
    op.create_index("ix_sessions_session_id", "sessions", ["session_id"], unique=True)
    op.create_index("ix_sessions_status", "sessions", ["status"], unique=False)

    op.create_table(
        "emotion_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("relative_seconds", sa.Float(), nullable=False),
        sa.Column("expression", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_emotion_records_expression", "emotion_records", ["expression"], unique=False
    )
    op.create_index(
        "ix_emotion_records_session_id", "emotion_records", ["session_id"], unique=False
    )

    op.create_table(
        "emotion_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("client_request_id", sa.String(length=128), nullable=False),
        sa.Column("valid_expression_seconds", sa.Float(), nullable=False),
        sa.Column("dominant_expression", sa.String(length=32), nullable=False),
        sa.Column("average_confidence", sa.Float(), nullable=False),
        sa.Column("face_coverage_percent", sa.Float(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # 生产库中该唯一约束由 scripts/ensure_index.py 以命名唯一索引形式补建，
    # 因此 baseline 也用命名唯一索引，避免 stamp 后结构与生产不一致。
    op.create_index(
        "uq_emotion_result_session_request",
        "emotion_results",
        ["session_id", "client_request_id"],
        unique=True,
    )
    op.create_index(
        "ix_emotion_results_client_request_id",
        "emotion_results",
        ["client_request_id"],
        unique=False,
    )
    op.create_index(
        "ix_emotion_results_session_id",
        "emotion_results",
        ["session_id"],
        unique=True,
    )

    op.create_table(
        "device_connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("app_client_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_connections_app_client_id",
        "device_connections",
        ["app_client_id"],
        unique=False,
    )
    op.create_index(
        "ix_device_connections_connection_id",
        "device_connections",
        ["connection_id"],
        unique=True,
    )
    op.create_index(
        "ix_device_connections_device_id",
        "device_connections",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        "ix_device_connections_status", "device_connections", ["status"], unique=False
    )

    op.create_table(
        "device_emotion_status",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("face_detected", sa.Boolean(), nullable=False),
        sa.Column("expression_detected", sa.Boolean(), nullable=False),
        sa.Column("current_expression", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_emotion_status_device_id",
        "device_emotion_status",
        ["device_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_emotion_status_device_id", table_name="device_emotion_status"
    )
    op.drop_table("device_emotion_status")

    op.drop_index("ix_device_connections_status", table_name="device_connections")
    op.drop_index("ix_device_connections_device_id", table_name="device_connections")
    op.drop_index("ix_device_connections_connection_id", table_name="device_connections")
    op.drop_index(
        "ix_device_connections_app_client_id", table_name="device_connections"
    )
    op.drop_table("device_connections")

    op.drop_index("ix_emotion_results_session_id", table_name="emotion_results")
    op.drop_index(
        "ix_emotion_results_client_request_id", table_name="emotion_results"
    )
    op.drop_index(
        "uq_emotion_result_session_request", table_name="emotion_results"
    )
    op.drop_table("emotion_results")

    op.drop_index("ix_emotion_records_session_id", table_name="emotion_records")
    op.drop_index("ix_emotion_records_expression", table_name="emotion_records")
    op.drop_table("emotion_records")

    op.drop_index("ix_sessions_status", table_name="sessions")
    op.drop_index("ix_sessions_session_id", table_name="sessions")
    op.drop_index("ix_sessions_device_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_devices_device_id", table_name="devices")
    op.drop_table("devices")
