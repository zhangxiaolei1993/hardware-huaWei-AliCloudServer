"""P1-D: 新增只追加表 emotion_result_versions（结果版本历史）

Revision ID: 0011_p1d_emotion_result_versions
Revises: 0010_m7_subject_ref
Create Date: 2026-11-19

只新建表，不改 P0 表结构与约束（``emotion_results`` 仍是每会话一行当前值）。
历史结果由 ``scripts/backfill_emotion_result_versions.py`` 幂等回填（默认 dry-run）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_p1d_emotion_result_versions"
down_revision: Union[str, None] = "0010_m7_subject_ref"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "emotion_result_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("sessions.session_id"),
            nullable=False,
        ),
        sa.Column(
            "result_id",
            sa.Integer(),
            sa.ForeignKey("emotion_results.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger_source", sa.String(length=32), nullable=False),
        sa.Column("source_record_count", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "session_id",
            "version",
            name="uq_emotion_result_versions_session_version",
        ),
    )
    op.create_index(
        "ix_emotion_result_versions_session_id",
        "emotion_result_versions",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_emotion_result_versions_session_sha256",
        "emotion_result_versions",
        ["session_id", "source_sha256"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_emotion_result_versions_session_sha256",
        table_name="emotion_result_versions",
    )
    op.drop_index(
        "ix_emotion_result_versions_session_id",
        table_name="emotion_result_versions",
    )
    op.drop_table("emotion_result_versions")
