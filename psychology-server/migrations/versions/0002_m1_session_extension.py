"""M1：sessions 扩展（source_system / N1 / 幂等键 / 锚点 / 补传截止 / 结束原因）

Revision ID: 0002_m1_session_extension
Revises: 0001_baseline
Create Date: 2026-11-17

对应 v1.4 §11.4 M1：
- 新增列并设置默认值（source_system 默认 standalone，旧数据自动填充）
- 新增 UNIQUE(source_system, client_request_id)，external_session_id 仅普通索引
- 新增 device_id+status、status+ended_at 查询索引
- status 允许新值 aborted（无 DDL 变更）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_m1_session_extension"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_system",
                sa.String(length=32),
                nullable=False,
                server_default="standalone",
            )
        )
        batch_op.add_column(
            sa.Column("external_session_id", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(
            sa.Column("client_request_id", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(sa.Column("anchor_utc_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("anchor_monotonic_ms", sa.BigInteger(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("anchor_source", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("anchor_uncertainty_ms", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("late_upload_deadline", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("ended_reason", sa.String(length=64), nullable=True)
        )

        batch_op.create_unique_constraint(
            "uq_sessions_source_request", ["source_system", "client_request_id"]
        )
        batch_op.create_index(
            "ix_sessions_external_session_id", ["external_session_id"], unique=False
        )
        batch_op.create_index(
            "ix_sessions_client_request_id", ["client_request_id"], unique=False
        )
        batch_op.create_index(
            "ix_sessions_device_status", ["device_id", "status"], unique=False
        )
        batch_op.create_index(
            "ix_sessions_status_ended", ["status", "ended_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_index("ix_sessions_status_ended")
        batch_op.drop_index("ix_sessions_device_status")
        batch_op.drop_index("ix_sessions_client_request_id")
        batch_op.drop_index("ix_sessions_external_session_id")
        batch_op.drop_constraint("uq_sessions_source_request", type_="unique")
        batch_op.drop_column("ended_reason")
        batch_op.drop_column("late_upload_deadline")
        batch_op.drop_column("anchor_uncertainty_ms")
        batch_op.drop_column("anchor_source")
        batch_op.drop_column("anchor_monotonic_ms")
        batch_op.drop_column("anchor_utc_at")
        batch_op.drop_column("client_request_id")
        batch_op.drop_column("external_session_id")
        batch_op.drop_column("source_system")
