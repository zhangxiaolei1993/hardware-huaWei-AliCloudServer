"""M13: device identity protection columns and device_audit_events (§17.4 D3)

Revision ID: 0008_m13_device_identity
Revises: 0007_m6_emotion_record_quality
Create Date: 2026-11-17

Additive only: three nullable timestamp columns on ``devices`` and a new
``device_audit_events`` table. No backfill, no destructive change.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_m13_device_identity"
down_revision: Union[str, None] = "0007_m6_emotion_record_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("identity_changed_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(sa.Column("token_rotated_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("token_revoked_at", sa.DateTime(), nullable=True))

    op.create_table(
        "device_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_device_audit_events_device_created",
        "device_audit_events",
        ["device_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_device_audit_events_type_created",
        "device_audit_events",
        ["event_type", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_device_audit_events_type_created", table_name="device_audit_events"
    )
    op.drop_index(
        "ix_device_audit_events_device_created", table_name="device_audit_events"
    )
    op.drop_table("device_audit_events")

    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_column("token_revoked_at")
        batch_op.drop_column("token_rotated_at")
        batch_op.drop_column("identity_changed_at")
