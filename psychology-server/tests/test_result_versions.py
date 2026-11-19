"""P1-D：emotion_result_versions 迁移、版本追加与幂等回填测试。

全部使用本地临时 SQLite，不连接任何生产库。
"""
import importlib.util
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.database import SessionLocal
from app.models.device import Device
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.emotion_result_version import EmotionResultVersion
from app.models.session import SessionModel
from app.services.emotion_service import (
    EMOTION_ALGORITHM_VERSION,
    recompute_emotion_result,
    source_timeline_fingerprint,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKFILL_PATH = Path(ROOT) / "scripts" / "backfill_emotion_result_versions.py"


def _alembic_config(db_url: str) -> Config:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location(
        "backfill_emotion_result_versions", BACKFILL_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------


def test_migration_upgrade_and_downgrade_on_empty_db(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'empty.db'}"
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "0010_m7_subject_ref")
    engine = create_engine(db_url)
    try:
        assert "emotion_result_versions" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "emotion_result_versions" in insp.get_table_names()
        columns = {c["name"] for c in insp.get_columns("emotion_result_versions")}
        assert {
            "id",
            "session_id",
            "result_id",
            "version",
            "algorithm_version",
            "status",
            "trigger_source",
            "source_record_count",
            "source_sha256",
            "result_json",
            "generated_at",
            "created_at",
        } <= columns
        uniques = {
            tuple(u["column_names"])
            for u in insp.get_unique_constraints("emotion_result_versions")
        }
        assert ("session_id", "version") in uniques
        indexes = {i["name"] for i in insp.get_indexes("emotion_result_versions")}
        assert {
            "ix_emotion_result_versions_session_id",
            "ix_emotion_result_versions_session_sha256",
        } <= indexes
    finally:
        engine.dispose()

    # 回退只删新表，P0 表不受影响
    command.downgrade(cfg, "0010_m7_subject_ref")
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert "emotion_result_versions" not in tables
        assert {
            "sessions",
            "emotion_records",
            "emotion_results",
            "external_result_snapshots",
        } <= tables
    finally:
        engine.dispose()


def test_migration_upgrade_existing_db_keeps_rows(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'existing.db'}"
    cfg = _alembic_config(db_url)
    command.upgrade(cfg, "0010_m7_subject_ref")

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into sessions (session_id, device_id, session_type, "
                    "source_system, deprecated_identity_used, status, created_at, "
                    "updated_at) values ('s-existing', 'dev-1', 'emotion', "
                    "'standalone', 0, 'completed', '2026-01-01 00:00:00', "
                    "'2026-01-01 00:00:00')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "emotion_result_versions" in insp.get_table_names()
        with engine.connect() as conn:
            count = conn.execute(text("select count(*) from sessions")).scalar()
        assert count == 1
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 导入 / 重算 → 版本追加
# ---------------------------------------------------------------------------


def _create_device_and_session(client, tag: str) -> tuple[dict, str]:
    device_id = f"atlas_p1d_{tag}"
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": device_id,
            "device_name": f"P1D {tag}",
            "device_type": "edge_ai_device",
        },
    )
    assert resp.status_code == 200, resp.text
    headers = {
        "X-Device-Id": device_id,
        "X-Device-Token": resp.json()["device_token"],
    }
    resp = client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        json={"firmware_version": "1.0.0"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": device_id, "session_type": "emotion"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return headers, resp.json()["session_id"]


def _upload(client, headers: dict, session_id: str, client_request_id: str) -> dict:
    resp = client.post(
        f"/api/v1/emotion/sessions/{session_id}/data",
        json={
            "client_request_id": client_request_id,
            "session_meta": {
                "elapsed_seconds": 2.0,
                "video_frames": 60,
                "display_fps": 30.0,
                "face_detected_frames": 45,
            },
            "timeline": [
                {"relative_seconds": 0.5, "expression": "neutral", "confidence": 0.8},
                {"relative_seconds": 1.0, "expression": "neutral", "confidence": 0.8},
                {"relative_seconds": 1.5, "expression": "happiness", "confidence": 0.9},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_import_then_recompute_appends_versions(client):
    headers, session_id = _create_device_and_session(client, "flow")
    _upload(client, headers, session_id, "req-p1d-flow-0001")

    db = SessionLocal()
    try:
        versions = (
            db.query(EmotionResultVersion)
            .filter(EmotionResultVersion.session_id == session_id)
            .order_by(EmotionResultVersion.version.asc())
            .all()
        )
        assert len(versions) == 1
        first = versions[0]
        assert first.version == 1
        assert first.status == "generated"
        assert first.trigger_source == "first_import"
        assert first.algorithm_version == EMOTION_ALGORITHM_VERSION
        assert first.source_record_count == 3
        assert len(first.source_sha256) == 64
        first_json = first.result_json
        first_sha = first.source_sha256

        session = (
            db.query(SessionModel)
            .filter(SessionModel.session_id == session_id)
            .one()
        )
        recompute_emotion_result(db, session)

        versions = (
            db.query(EmotionResultVersion)
            .filter(EmotionResultVersion.session_id == session_id)
            .order_by(EmotionResultVersion.version.asc())
            .all()
        )
        assert [v.version for v in versions] == [1, 2]
        assert versions[0].status == "superseded"
        assert versions[1].status == "generated"
        assert versions[1].trigger_source == "manual_recompute"
        # 旧版本内容仍可查，且保持生成时的快照
        assert versions[0].result_json == first_json
        # 源数据未变，sha 与记录数应一致
        assert versions[1].source_sha256 == first_sha
        assert versions[1].source_record_count == 3

        # emotion_results 仍只有一行当前值
        results = (
            db.query(EmotionResult)
            .filter(EmotionResult.session_id == session_id)
            .all()
        )
        assert len(results) == 1
        assert results[0].result_json == versions[1].result_json
        row_count, sha256 = source_timeline_fingerprint(db, session_id)
        assert row_count == 3
        assert sha256 == first_sha
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 历史结果幂等回填
# ---------------------------------------------------------------------------


def _seed_result_db(db_url: str) -> None:
    engine = create_engine(db_url)
    DBSession = sessionmaker(bind=engine)
    db = DBSession()
    now = datetime(2026, 11, 19, 10, 0, 0)
    try:
        db.add(
            Device(
                device_id="dev-p1d",
                device_name="P1D",
                device_type="edge_ai_device",
                status="online",
                device_token="t" * 64,
                capabilities=["emotion"],
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            SessionModel(
                session_id="s-p1d-backfill",
                device_id="dev-p1d",
                session_type="emotion",
                source_system="standalone",
                status="completed",
                created_at=now,
                updated_at=now,
            )
        )
        for rel in (0.5, 1.0):
            db.add(
                EmotionRecord(
                    session_id="s-p1d-backfill",
                    relative_seconds=rel,
                    expression="neutral",
                    confidence=0.8,
                    created_at=now,
                )
            )
        db.add(
            EmotionResult(
                session_id="s-p1d-backfill",
                client_request_id="req-backfill",
                valid_expression_seconds=0.5,
                dominant_expression="neutral",
                average_confidence=0.8,
                face_coverage_percent=0.0,
                result_json={"expressions": [], "quality": {"total_records": 2}},
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()
        engine.dispose()


def test_backfill_is_idempotent_and_keeps_p0_unchanged(tmp_path):
    module = _load_backfill_module()
    db_path = tmp_path / "backfill.db"
    db_url = f"sqlite:///{db_path}"
    command.upgrade(_alembic_config(db_url), "head")
    _seed_result_db(db_url)

    conn = sqlite3.connect(str(db_path))
    try:
        before = module._p0_fingerprint(conn)

        dry = module.backfill(conn, apply=False)
        assert dry["planned"]
        assert dry["p0_unchanged"]

        first = module.backfill(conn, apply=True)
        assert first["p0_unchanged"], first["p0_before"] == first["p0_after"]
        rows = conn.execute(
            "select session_id, version, status, trigger_source, "
            "source_record_count, algorithm_version from emotion_result_versions"
        ).fetchall()
        assert rows == [
            ("s-p1d-backfill", 1, "generated", "backfill", 2,
             EMOTION_ALGORITHM_VERSION)
        ]

        second = module.backfill(conn, apply=True)
        assert second["planned"] == []
        assert second["skipped"] == 1
        assert second["p0_unchanged"]
        count = conn.execute(
            "select count(*) from emotion_result_versions"
        ).fetchone()[0]
        assert count == 1

        after = module._p0_fingerprint(conn)
        assert after == before
        assert before["counts"]["emotion_results"] == 1
        assert before["counts"]["emotion_records"] == 2
    finally:
        conn.close()
