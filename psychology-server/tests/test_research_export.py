"""P1-A 科研导出脚本测试：筛选、质量报告、禁字段、时间归一化、可复现。"""
import importlib.util
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.device import Device
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.external_result_snapshot import ExternalResultSnapshot
from app.models.session import SessionModel
from app.models.session_device import SessionDevice
from app.models.timeline_event import TimelineEvent
from app.models.upload_batch import UploadBatch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research_export.py"


@pytest.fixture(scope="module")
def exporter():
    spec = importlib.util.spec_from_file_location("research_export", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "sample.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    DBSession = sessionmaker(bind=engine)
    db = DBSession()
    try:
        _seed(db)
        db.commit()
    finally:
        db.close()
        engine.dispose()
    return path


SID1 = "a" * 32
SID2 = "b" * 32
ANCHOR = datetime(2026, 11, 19, 10, 0, 0)
START = datetime(2026, 11, 19, 10, 0, 0)
END = datetime(2026, 11, 19, 10, 5, 0)


def _seed(db):
    db.add(
        Device(
            device_id="atlas_test",
            device_name="Atlas Test",
            device_type="edge_ai_device",
            status="online",
            device_token="t" * 64,
            capabilities=["emotion"],
            created_at=START,
            updated_at=START,
        )
    )
    db.add(
        SessionModel(
            session_id=SID1,
            device_id="atlas_test",
            user_id="user-1",
            external_user_id="EXT-1",
            subject_ref="subject-abc",
            session_type="emotion",
            source_system="standalone",
            client_request_id="req-1",
            anchor_utc_at=ANCHOR,
            anchor_source="server",
            anchor_uncertainty_ms=5000,
            status="completed",
            started_at=START,
            ended_at=END,
            created_at=START,
            updated_at=END,
        )
    )
    db.add(
        SessionModel(
            session_id=SID2,
            device_id="atlas_test",
            user_id="user-2",
            external_user_id="EXT-2",
            subject_ref="subject-def",
            session_type="emotion",
            source_system="standalone",
            client_request_id="req-2",
            anchor_utc_at=datetime(2026, 11, 19, 11, 0, 0),
            anchor_source="server",
            anchor_uncertainty_ms=5000,
            status="completed",
            started_at=datetime(2026, 11, 19, 11, 0, 0),
            ended_at=datetime(2026, 11, 19, 11, 2, 0),
            created_at=datetime(2026, 11, 19, 11, 0, 0),
            updated_at=datetime(2026, 11, 19, 11, 2, 0),
        )
    )
    db.add(
        SessionDevice(
            session_id=SID1,
            device_id="atlas_test",
            role="primary",
            status="bound",
            bound_at=START,
            created_at=START,
            updated_at=START,
        )
    )
    events = [
        ("1" * 32, "question_shown", -5.0, {
            "question_type": "single_choice",
            "render_ms": 120,
            "answer_text": "SECRET_ANSWER",
            "transcript": "SECRET_TRANSCRIPT",
        }),
        ("2" * 32, "response_submitted", 1.0, {
            "input_method": "touch",
            "answer_value": "SECRET_VALUE",
        }),
        ("3" * 32, "response_submitted", 400.0, {"input_method": "touch"}),
    ]
    for event_id, event_type, rel, payload in events:
        flags = ["clock_unverified"] + (["pre_anchor"] if rel < 0 else [])
        db.add(
            TimelineEvent(
                event_id=event_id,
                session_id=SID1,
                device_id=None,
                stream_id="app_timeline",
                event_type=event_type,
                source="app",
                occurred_at=START,
                relative_seconds=rel,
                server_received_at=START,
                payload=payload,
                schema_version="1.0",
                protocol_version="1.0",
                quality="estimated",
                quality_info={"flags": flags, "stream_defaulted": True},
                client_request_id="req-events",
                created_at=START,
            )
        )
    db.add(
        TimelineEvent(
            event_id="4" * 32,
            session_id=SID2,
            event_type="session_started",
            source="app",
            occurred_at=datetime(2026, 11, 19, 11, 0, 0),
            relative_seconds=0.0,
            server_received_at=datetime(2026, 11, 19, 11, 0, 0),
            payload={"client_version": "1.0"},
            schema_version="1.0",
            quality="estimated",
            client_request_id="req-events-2",
            created_at=datetime(2026, 11, 19, 11, 0, 0),
        )
    )
    for i, rel in enumerate((-2.0, 0.0, 1.0, 2.0)):
        db.add(
            EmotionRecord(
                session_id=SID1,
                device_id="atlas_test",
                stream_id="facial_expression",
                relative_seconds=rel,
                expression="neutral",
                confidence=0.9,
                created_at=START,
                quality="legacy",
                quality_info={"flags": ["stream_defaulted"]},
                protocol_version="1.0",
            )
        )
    db.add(
        EmotionRecord(
            session_id=SID2,
            device_id="atlas_test",
            stream_id="facial_expression",
            relative_seconds=0.0,
            expression="happy",
            confidence=0.8,
            created_at=datetime(2026, 11, 19, 11, 0, 0),
            quality="legacy",
        )
    )
    db.add(
        EmotionResult(
            session_id=SID1,
            client_request_id="req-result",
            valid_expression_seconds=4.0,
            dominant_expression="neutral",
            average_confidence=0.9,
            face_coverage_percent=98.5,
            result_json={"expressions": {"neutral": 4}, "quality": "legacy"},
            created_at=END,
            updated_at=END,
        )
    )
    db.add(
        ExternalResultSnapshot(
            snapshot_id="snap_" + "1" * 32,
            session_id=SID1,
            source_system="xin_tan_tan",
            questionnaire_code="PHQ-9",
            result_version=1,
            status="completed",
            total_score=7.0,
            dimensions={"anxiety": 3, "mood": 4},
            radar_norm={"anxiety": 0.3, "mood": 0.4},
            trust_level="client_relayed",
            idempotency_key=f"res:{SID1}:PHQ-9:v1",
            received_at=END,
            created_at=END,
        )
    )
    db.add(
        UploadBatch(
            upload_id="up_" + "1" * 32,
            session_id=SID1,
            device_id="atlas_test",
            client_request_id="req-emotion",
            batch_type="emotion",
            status="received",
            item_count=4,
            total_bytes=1024,
            received_at=START,
            created_at=START,
            updated_at=START,
        )
    )


def _read_csv(path):
    import csv

    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_single_session_layout_and_counts(exporter, db_path, tmp_path):
    out = tmp_path / "out"
    manifest = exporter.run_export(
        db_path, out, sessions=[SID1], make_zip=False
    )
    base = out / "sessions" / SID1
    assert (base / "meta" / "sessions.csv").exists()
    assert (base / "meta" / "session_devices.csv").exists()
    assert (base / "meta" / "upload_batches.csv").exists()
    assert (base / "raw" / "timeline_events.csv").exists()
    assert (base / "raw" / "emotion_records.csv").exists()
    assert (base / "derived" / "emotion_results.csv").exists()
    assert (base / "derived" / "external_result_snapshots.csv").exists()

    assert manifest["total_counts"]["timeline_events"] == 3
    session_manifest = json.loads((base / "manifest.json").read_text("utf-8"))
    assert session_manifest["counts"]["timeline_events"] == 3
    assert session_manifest["counts"]["emotion_records"] == 4
    assert session_manifest["counts"]["emotion_results"] == 1
    assert session_manifest["counts"]["external_result_snapshots"] == 1
    assert session_manifest["counts"]["upload_batches"] == 1
    assert len(_read_csv(base / "raw" / "emotion_records.csv")) == 4
    assert len(_read_jsonl(base / "raw" / "timeline_events.jsonl")) == 3


def test_payload_whitelist_and_no_banned_fields(exporter, db_path, tmp_path):
    out = tmp_path / "safe"
    exporter.run_export(db_path, out, sessions=[SID1], make_zip=False)
    events = _read_jsonl(out / "sessions" / SID1 / "raw" / "timeline_events.jsonl")
    payloads = [row["payload"] for row in events if row["payload"]]
    assert {"question_type", "render_ms"} <= set(payloads[0])
    assert "answer_text" not in payloads[0]
    assert "transcript" not in payloads[0]
    assert all("answer_value" not in (p or {}) for p in payloads)

    sessions_csv = (out / "sessions" / SID1 / "meta" / "sessions.csv").read_text("utf-8")
    assert "external_user_id" not in sessions_csv
    assert "user_id" not in sessions_csv
    joined = "\n".join(
        p.read_text(encoding="utf-8")
        for p in out.rglob("*")
        if p.is_file() and p.suffix in (".csv", ".jsonl")
    )
    for banned in ("answer_text", "answer_value", "transcript", "SECRET_ANSWER", "SECRET_VALUE"):
        assert banned not in joined

    out2 = tmp_path / "with_ids"
    exporter.run_export(
        db_path,
        out2,
        sessions=[SID1],
        include_external_ids=True,
        make_zip=False,
    )
    rows = _read_csv(out2 / "sessions" / SID1 / "meta" / "sessions.csv")
    assert rows[0]["external_user_id"] == "EXT-1"
    assert rows[0]["user_id"] == "user-1"


def test_quality_report_stats(exporter, db_path, tmp_path):
    out = tmp_path / "quality"
    exporter.run_export(db_path, out, sessions=[SID1], make_zip=False)
    report = json.loads(
        (out / "sessions" / SID1 / "quality_report.json").read_text("utf-8")
    )
    emotions = report["tables"]["emotion_records"]
    assert emotions["rows"] == 4
    assert emotions["fill_rates"]["device_timestamp"] == 0.0
    assert emotions["fill_rates"]["monotonic_ms"] == 0.0
    assert emotions["fill_rates"]["frame_index"] == 0.0
    assert emotions["fill_rates"]["stream_id"] == 1.0
    assert report["quality_distribution"]["emotion_records"] == {"legacy": 4}
    assert report["quality_info_flags"]["emotion_records"]["stream_defaulted"] == 4
    assert report["quality_info_flags"]["timeline_events"]["pre_anchor"] == 1
    assert report["anomalies"]["negative_relative_seconds"] == 2
    assert report["anomalies"]["outside_session_window"] == 1
    assert report["anomalies"]["duplicate_event_keys"] == 0
    assert report["payload_keys_dropped"]["answer_text"] == 1
    assert report["payload_keys_dropped"]["transcript"] == 1
    assert report["payload_keys_dropped"]["answer_value"] == 1


def test_anomalies_duplicate_and_empty_keys(exporter):
    rows = {
        "timeline_events": [
            {"session_id": "s", "source": "app", "event_id": "e1", "relative_seconds": -1},
            {"session_id": "s", "source": "app", "event_id": "e1", "relative_seconds": 0},
            {"session_id": "s", "source": "app", "event_id": "", "relative_seconds": 1},
        ],
        "upload_batches": [
            {"session_id": "s", "client_request_id": "c"},
            {"session_id": "s", "client_request_id": "c"},
        ],
        "emotion_results": [],
        "emotion_records": [],
    }
    anomalies = exporter._anomalies(
        rows,
        {"started_at": "2026-11-19T10:00:00.000Z", "ended_at": "2026-11-19T10:00:10.000Z"},
    )
    assert anomalies["duplicate_event_keys"] == 1
    assert anomalies["empty_event_id"] == 1
    assert anomalies["duplicate_upload_batches"] == 1


def test_time_normalization_and_reproducibility(exporter, db_path, tmp_path):
    out1 = tmp_path / "r1"
    out2 = tmp_path / "r2"
    exporter.run_export(db_path, out1, sessions=[SID1], make_zip=False)
    exporter.run_export(db_path, out2, sessions=[SID1], make_zip=False)

    row = _read_jsonl(out1 / "sessions" / SID1 / "raw" / "emotion_records.jsonl")[1]
    assert row["created_at"] == "2026-11-19T10:00:00.000Z"
    assert row["relative_seconds"] == 0.0
    assert row["event_timestamp"] == "2026-11-19T10:00:00.000Z"
    assert row["event_timestamp_estimated"] is True
    assert row["derived_time_source"] == "server_anchor"
    assert row["anchor_uncertainty_ms"] == 5000

    files1 = {
        str(path.relative_to(out1)): exporter._sha256_file(path)
        for path in out1.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    files2 = {
        str(path.relative_to(out2)): exporter._sha256_file(path)
        for path in out2.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    assert files1 == files2


def test_batch_export_zip_and_time_filter(exporter, db_path, tmp_path):
    out = tmp_path / "batch"
    manifest = exporter.run_export(db_path, out, export_all=True, make_zip=True)
    assert len(manifest["sessions"]) == 2
    zip_path = out.parent / f"{out.name}.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(f"/{SID1}/" in n for n in names)
    assert any(f"/{SID2}/" in n for n in names)

    filtered = tmp_path / "filtered"
    only_first = exporter.run_export(
        db_path,
        filtered,
        export_all=True,
        from_utc="2026-11-19T09:00:00Z",
        to_utc="2026-11-19T10:30:00Z",
        make_zip=False,
    )
    assert only_first["sessions"] == [SID1]

    bounded = tmp_path / "bounded"
    exporter.run_export(
        db_path,
        bounded,
        sessions=[SID1],
        from_seconds=0.0,
        to_seconds=10.0,
        make_zip=False,
    )
    assert len(_read_jsonl(bounded / "sessions" / SID1 / "raw" / "emotion_records.jsonl")) == 3


def test_default_refuses_non_empty_output(exporter, db_path, tmp_path):
    out = tmp_path / "existing"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        exporter.run_export(db_path, out, sessions=[SID1], make_zip=False)
