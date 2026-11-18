"""迁移测试：空库 → baseline → M1，且迁移结果与 ORM 模型无差异。

只在本地临时 SQLite 上运行，不连接任何生产库。
"""
import os

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config(db_url: str) -> Config:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_creates_schema_matching_models(tmp_path):
    db_path = tmp_path / "migration.db"
    db_url = f"sqlite:///{db_path}"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert {
            "devices",
            "sessions",
            "emotion_records",
            "emotion_results",
            "device_connections",
            "device_emotion_status",
            "timeline_events",
            "external_result_snapshots",
            "upload_batches",
            "session_devices",
            "device_audit_events",
            "alembic_version",
        } <= tables

        columns = {c["name"] for c in insp.get_columns("sessions")}
        for name in (
            "source_system",
            "external_session_id",
            "client_request_id",
            "anchor_utc_at",
            "anchor_monotonic_ms",
            "anchor_source",
            "anchor_uncertainty_ms",
            "late_upload_deadline",
            "ended_reason",
        ):
            assert name in columns, name

        indexes = {i["name"] for i in insp.get_indexes("sessions")}
        assert {
            "ix_sessions_external_session_id",
            "ix_sessions_client_request_id",
            "ix_sessions_device_status",
            "ix_sessions_status_ended",
        } <= indexes

        uniques = {
            u["name"]: u["column_names"] for u in insp.get_unique_constraints("sessions")
        }
        assert uniques.get("uq_sessions_source_request") == [
            "source_system",
            "client_request_id",
        ]

        # external_session_id 不得有唯一约束
        dual_unique = [
            u for u in insp.get_unique_constraints("sessions")
            if "external_session_id" in u["column_names"]
        ]
        assert dual_unique == []

        # M2：timeline_events 关键列与事件级唯一约束
        event_columns = {c["name"] for c in insp.get_columns("timeline_events")}
        for name in (
            "event_id",
            "session_id",
            "event_type",
            "source",
            "occurred_at",
            "relative_seconds",
            "server_received_at",
            "quality",
            "quality_info",
            "client_request_id",
        ):
            assert name in event_columns, name

        event_uniques = {
            tuple(u["column_names"])
            for u in insp.get_unique_constraints("timeline_events")
        }
        assert ("session_id", "source", "event_id") in event_uniques

        # M6：emotion_records 时间与质量列
        record_columns = {c["name"] for c in insp.get_columns("emotion_records")}
        for name in (
            "device_timestamp",
            "monotonic_ms",
            "clock_offset_ms",
            "frame_index",
            "quality",
            "quality_info",
            "protocol_version",
        ):
            assert name in record_columns, name

        # M6 补齐：emotion_records 的 device_id / stream_id 与索引
        for name in ("device_id", "stream_id"):
            assert name in record_columns, name
        record_indexes = {i["name"] for i in insp.get_indexes("emotion_records")}
        assert {
            "ix_emotion_records_session_quality",
            "ix_emotion_records_session_stream_time",
        } <= record_indexes

        # M7：sessions 受试者脱敏字段
        session_columns = {c["name"] for c in insp.get_columns("sessions")}
        for name in ("external_user_id", "subject_ref", "deprecated_identity_used"):
            assert name in session_columns, name
        session_indexes = {i["name"] for i in insp.get_indexes("sessions")}
        assert {
            "ix_sessions_subject_ref",
            "ix_sessions_subject_created",
        } <= session_indexes

        # M13：devices 身份保护列 + 审计表
        device_columns = {c["name"] for c in insp.get_columns("devices")}
        for name in ("identity_changed_at", "token_rotated_at", "token_revoked_at"):
            assert name in device_columns, name

        audit_columns = {c["name"] for c in insp.get_columns("device_audit_events")}
        assert {
            "id",
            "device_id",
            "event_type",
            "severity",
            "source_ip",
            "detail",
            "created_at",
        } == audit_columns

        audit_indexes = {i["name"] for i in insp.get_indexes("device_audit_events")}
        assert {
            "ix_device_audit_events_device_created",
            "ix_device_audit_events_type_created",
        } <= audit_indexes

        # 迁移结果与 ORM 元数据一致（无待生成变更）
        from app.db.database import Base

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)
        assert diff == [], diff
    finally:
        engine.dispose()
