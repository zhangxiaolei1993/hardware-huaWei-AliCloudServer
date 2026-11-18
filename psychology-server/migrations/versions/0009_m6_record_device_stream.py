"""M6 补齐：emotion_records 增加 device_id / stream_id 与索引（§11.4）

Revision ID: 0009_m6_record_device_stream
Revises: 0008_m13_device_identity
Create Date: 2026-11-17

Additive only. Historical rows are backfilled:
- device_id ← sessions.device_id
- stream_id ← 'facial_expression'（v1 默认流）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_m6_record_device_stream"
down_revision: Union[str, None] = "0008_m13_device_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("emotion_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("device_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("stream_id", sa.String(length=40), nullable=True))

    op.execute(
        """
        UPDATE emotion_records
        SET device_id = (
            SELECT sessions.device_id FROM sessions
            WHERE sessions.session_id = emotion_records.session_id
        )
        WHERE device_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE emotion_records
        SET stream_id = 'facial_expression'
        WHERE stream_id IS NULL
        """
    )

    op.create_index(
        "ix_emotion_records_session_quality",
        "emotion_records",
        ["session_id", "quality"],
        unique=False,
    )
    op.create_index(
        "ix_emotion_records_session_stream_time",
        "emotion_records",
        ["session_id", "stream_id", "relative_seconds"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_emotion_records_session_stream_time", table_name="emotion_records"
    )
    op.drop_index("ix_emotion_records_session_quality", table_name="emotion_records")

    with op.batch_alter_table("emotion_records", schema=None) as batch_op:
        batch_op.drop_column("stream_id")
        batch_op.drop_column("device_id")
