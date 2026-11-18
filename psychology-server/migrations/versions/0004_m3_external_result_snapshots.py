"""M3: create external_result_snapshots (§11.3)

Revision ID: 0004_m3_external_result_snapshots
Revises: 0003_m2_timeline_events
Create Date: 2026-11-17

New table only; no existing data touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_m3_external_result_snapshots"
down_revision: Union[str, None] = "0003_m2_timeline_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_result_snapshots",
        sa.Column("snapshot_id", sa.String(length=40), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("source_result_id", sa.String(length=128), nullable=True),
        sa.Column("questionnaire_code", sa.String(length=64), nullable=False),
        sa.Column("questionnaire_version", sa.String(length=32), nullable=True),
        sa.Column("scale_algorithm_version", sa.String(length=32), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("supersedes_snapshot_id", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=True),
        sa.Column("scoring_mode", sa.String(length=16), nullable=True),
        sa.Column("state_update_status", sa.String(length=32), nullable=True),
        sa.Column("scale_name", sa.String(length=128), nullable=True),
        sa.Column("scale_question_count", sa.Integer(), nullable=True),
        sa.Column("dimensions", sa.JSON(), nullable=True),
        sa.Column("radar_norm", sa.JSON(), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=True),
        sa.Column("level_code", sa.String(length=32), nullable=True),
        sa.Column("recommendation_code", sa.String(length=64), nullable=True),
        sa.Column("ai_profile_score", sa.Float(), nullable=True),
        sa.Column("summary_code", sa.String(length=64), nullable=True),
        sa.Column("trust_level", sa.String(length=24), nullable=False),
        sa.Column("signature", sa.String(length=512), nullable=True),
        sa.Column("signature_algorithm", sa.String(length=32), nullable=True),
        sa.Column("source_payload_sha256", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "trust_level IN ('client_relayed','server_signed','server_verified')",
            name="ck_result_snapshots_trust_level",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_snapshot_id"],
            ["external_result_snapshots.snapshot_id"],
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_result_snapshots_idempotency_key"
        ),
        sa.UniqueConstraint(
            "session_id",
            "questionnaire_code",
            "result_version",
            name="uq_result_snapshots_session_code_version",
        ),
    )
    with op.batch_alter_table("external_result_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_external_result_snapshots_questionnaire_code"),
            ["questionnaire_code"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_external_result_snapshots_session_id"),
            ["session_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_result_snapshots_session_code",
            ["session_id", "questionnaire_code"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("external_result_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_result_snapshots_session_code")
        batch_op.drop_index(batch_op.f("ix_external_result_snapshots_session_id"))
        batch_op.drop_index(
            batch_op.f("ix_external_result_snapshots_questionnaire_code")
        )

    op.drop_table("external_result_snapshots")
