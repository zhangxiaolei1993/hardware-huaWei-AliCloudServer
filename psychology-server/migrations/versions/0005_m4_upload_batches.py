"""M4: create upload_batches (§11.3)

Revision ID: 0005_m4_upload_batches
Revises: 0004_m3_external_result_snapshots
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_m4_upload_batches"
down_revision: Union[str, None] = "0004_m3_external_result_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "upload_batches",
        sa.Column("upload_id", sa.String(length=40), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("app_client_id", sa.String(length=128), nullable=True),
        sa.Column("client_request_id", sa.String(length=128), nullable=False),
        sa.Column("batch_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("upload_id"),
        sa.UniqueConstraint(
            "session_id",
            "client_request_id",
            name="uq_upload_batches_session_request",
        ),
    )
    with op.batch_alter_table("upload_batches", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_upload_batches_device_id"), ["device_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_upload_batches_session_id"), ["session_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_batches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_upload_batches_session_id"))
        batch_op.drop_index(batch_op.f("ix_upload_batches_device_id"))

    op.drop_table("upload_batches")
