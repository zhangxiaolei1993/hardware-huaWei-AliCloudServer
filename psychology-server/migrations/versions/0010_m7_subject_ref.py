"""M7: sessions 增加 external_user_id / subject_ref / deprecated_identity_used

Revision ID: 0010_m7_subject_ref
Revises: 0009_m6_record_device_stream
Create Date: 2026-11-17

Additive only. 历史行由 ``scripts/backfill_subject_ref.py`` 分批回填
（支持 ``--dry-run``），不在迁移中做全表计算。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_m7_subject_ref"
down_revision: Union[str, None] = "0009_m6_record_device_stream"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("external_user_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("subject_ref", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "deprecated_identity_used",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.create_index(
            "ix_sessions_subject_ref", ["subject_ref"], unique=False
        )
        batch_op.create_index(
            "ix_sessions_subject_created",
            ["subject_ref", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_sessions_subject_created")
        batch_op.drop_index("ix_sessions_subject_ref")
        batch_op.drop_column("deprecated_identity_used")
        batch_op.drop_column("subject_ref")
        batch_op.drop_column("external_user_id")
