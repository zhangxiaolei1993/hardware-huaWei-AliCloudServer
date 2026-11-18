"""M2: create timeline_events (§7.3)

Revision ID: 0003_m2_timeline_events
Revises: 0002_m1_session_extension
Create Date: 2026-11-17

New table only; no existing data touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_m2_timeline_events"
down_revision: Union[str, None] = "0002_m1_session_extension"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "timeline_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=True),
        sa.Column("stream_id", sa.String(length=40), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("relative_seconds", sa.Float(), nullable=False),
        sa.Column("device_timestamp", sa.String(length=40), nullable=True),
        sa.Column("monotonic_ms", sa.BigInteger(), nullable=True),
        sa.Column("clock_offset_ms", sa.Integer(), nullable=True),
        sa.Column("time_uncertainty_ms", sa.Integer(), nullable=True),
        sa.Column("server_received_at", sa.DateTime(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("questionnaire_id", sa.String(length=64), nullable=True),
        sa.Column("questionnaire_version", sa.String(length=32), nullable=True),
        sa.Column("question_id", sa.String(length=64), nullable=True),
        sa.Column("question_index", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("protocol_version", sa.String(length=16), nullable=True),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.Column("quality_info", sa.JSON(), nullable=True),
        sa.Column("client_request_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "source",
            "event_id",
            name="uq_timeline_events_session_source_event",
        ),
    )
    with op.batch_alter_table("timeline_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_timeline_events_client_request",
            ["client_request_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_timeline_events_device_id"), ["device_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_timeline_events_event_id"), ["event_id"], unique=True
        )
        batch_op.create_index(
            batch_op.f("ix_timeline_events_event_type"),
            ["event_type"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_timeline_events_questionnaire_id"),
            ["questionnaire_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_timeline_events_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            "ix_timeline_events_session_occurred",
            ["session_id", "occurred_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_timeline_events_session_quality",
            ["session_id", "quality"],
            unique=False,
        )
        batch_op.create_index(
            "ix_timeline_events_session_question_occurred",
            ["session_id", "question_id", "occurred_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_timeline_events_session_type_occurred",
            ["session_id", "event_type", "occurred_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("timeline_events", schema=None) as batch_op:
        batch_op.drop_index("ix_timeline_events_session_type_occurred")
        batch_op.drop_index("ix_timeline_events_session_question_occurred")
        batch_op.drop_index("ix_timeline_events_session_quality")
        batch_op.drop_index("ix_timeline_events_session_occurred")
        batch_op.drop_index(batch_op.f("ix_timeline_events_session_id"))
        batch_op.drop_index(batch_op.f("ix_timeline_events_questionnaire_id"))
        batch_op.drop_index(batch_op.f("ix_timeline_events_event_type"))
        batch_op.drop_index(batch_op.f("ix_timeline_events_event_id"))
        batch_op.drop_index(batch_op.f("ix_timeline_events_device_id"))
        batch_op.drop_index("ix_timeline_events_client_request")

    op.drop_table("timeline_events")
