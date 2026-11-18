"""M6: add time & quality columns to emotion_records (§11.4)

Revision ID: 0007_m6_emotion_record_quality
Revises: 0006_m5_session_devices
Create Date: 2026-11-17

All new columns are nullable except ``quality`` (server_default 'legacy'),
so existing rows and old firmware requests remain valid.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_m6_emotion_record_quality"
down_revision: Union[str, None] = "0006_m5_session_devices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("emotion_records", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("device_timestamp", sa.String(length=40), nullable=True)
        )
        batch_op.add_column(sa.Column("monotonic_ms", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("clock_offset_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("frame_index", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "quality",
                sa.String(length=16),
                nullable=False,
                server_default="legacy",
            )
        )
        batch_op.add_column(sa.Column("quality_info", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("protocol_version", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("emotion_records", schema=None) as batch_op:
        batch_op.drop_column("protocol_version")
        batch_op.drop_column("quality_info")
        batch_op.drop_column("quality")
        batch_op.drop_column("frame_index")
        batch_op.drop_column("clock_offset_ms")
        batch_op.drop_column("monotonic_ms")
        batch_op.drop_column("device_timestamp")
