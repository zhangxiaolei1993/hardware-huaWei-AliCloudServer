"""M5: create session_devices and backfill primary bindings (§11.3)

Revision ID: 0006_m5_session_devices
Revises: 0005_m4_upload_batches
Create Date: 2026-11-17

Backfill is additive and idempotent: one 'primary'/'bound' row per existing
session. No existing table is altered or deleted.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_m5_session_devices"
down_revision: Union[str, None] = "0005_m4_upload_batches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("bound_at", sa.DateTime(), nullable=False),
        sa.Column("unbound_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "device_id", name="uq_session_devices_session_device"
        ),
    )
    with op.batch_alter_table("session_devices", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_session_devices_device_id"), ["device_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_session_devices_session_id"),
            ["session_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_session_devices_device_status",
            ["device_id", "status"],
            unique=False,
        )

    # 历史会话补齐主设备绑定（仅插入，不修改既有数据）
    op.execute(
        sa.text(
            "INSERT INTO session_devices "
            "(session_id, device_id, role, status, bound_at, unbound_at, created_at, updated_at) "
            "SELECT s.session_id, s.device_id, 'primary', 'bound', "
            "s.created_at, NULL, s.created_at, s.created_at "
            "FROM sessions s "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM session_devices d "
            "  WHERE d.session_id = s.session_id AND d.device_id = s.device_id"
            ")"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("session_devices", schema=None) as batch_op:
        batch_op.drop_index("ix_session_devices_device_status")
        batch_op.drop_index(batch_op.f("ix_session_devices_session_id"))
        batch_op.drop_index(batch_op.f("ix_session_devices_device_id"))

    op.drop_table("session_devices")
