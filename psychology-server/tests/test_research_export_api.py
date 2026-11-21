"""科研导出 HTTP API 测试：单会话、批量/时间筛选、数据类型、鉴权与输出结构。

全部使用 conftest 的本地临时 SQLite，不连接任何真实库。
"""
import hashlib
import json
import zipfile
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.session import SessionModel
from app.models.timeline_event import TimelineEvent

API_KEY = get_settings().query_api_key
HEADERS = {"X-Api-Key": API_KEY}

EARLY = datetime(2020, 1, 1, 8, 0, 0)
LATE = datetime(2026, 11, 20, 9, 0, 0)


def _seed_session(
    session_id: str,
    started_at: datetime,
    records: list[tuple[float, str]],
    *,
    with_event: bool = False,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            SessionModel(
                session_id=session_id,
                device_id="atlas_api_test",
                session_type="emotion",
                source_system="standalone",
                client_request_id=f"req-{session_id}",
                anchor_utc_at=started_at,
                anchor_source="server",
                status="completed",
                started_at=started_at,
                ended_at=started_at + timedelta(minutes=2),
                created_at=started_at,
                updated_at=started_at,
            )
        )
        for index, (rel, expression) in enumerate(records):
            db.add(
                EmotionRecord(
                    session_id=session_id,
                    relative_seconds=rel,
                    expression=expression,
                    confidence=0.8,
                    created_at=started_at,
                )
            )
        db.add(
            EmotionResult(
                session_id=session_id,
                client_request_id=f"req-{session_id}",
                valid_expression_seconds=1.0,
                dominant_expression="neutral",
                average_confidence=0.8,
                face_coverage_percent=50.0,
                result_json={"expressions": [], "quality": {"total_records": len(records)}},
                created_at=started_at,
                updated_at=started_at,
            )
        )
        if with_event:
            db.add(
                TimelineEvent(
                    event_id=f"e{session_id}"[:32].ljust(32, "0"),
                    session_id=session_id,
                    stream_id="app_timeline",
                    event_type="question_shown",
                    source="app",
                    occurred_at=started_at,
                    relative_seconds=-2.0,
                    server_received_at=started_at,
                    payload={
                        "question_type": "single_choice",
                        "answer_text": "SECRET_ANSWER",
                        "transcript": "SECRET_TRANSCRIPT",
                    },
                    schema_version="1.0",
                    quality="estimated",
                    quality_info={"flags": ["pre_anchor"]},
                    client_request_id=f"evt-{session_id}",
                    created_at=started_at,
                )
            )
        db.commit()
    finally:
        db.close()


def _extract(tmp_path, content: bytes):
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(content)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        zf.extractall(tmp_path)
    prefix = names[0].split("/")[0]
    return tmp_path / prefix, names


def test_post_single_session_export_structure(client, tmp_path):
    sid = "s-api-single-0001"
    _seed_session(sid, LATE, [(0.5, "neutral"), (1.5, "happiness")], with_event=True)

    resp = client.post(
        "/api/v1/research/exports",
        json={
            "session_ids": [sid],
            "data_types": ["timeline", "emotion", "results"],
            "format": "both",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["x-export-sessions"] == "1"
    assert len(resp.headers["x-export-manifest-sha256"]) == 64

    root, names = _extract(tmp_path, resp.content)
    assert "manifest.json" in [n.split("/", 1)[1] for n in names]
    assert (root / "data_dictionary.md").is_file()
    assert (root / "quality_report.json").is_file()
    assert (root / "sessions_index.csv").is_file()
    session_dir = root / "sessions" / sid
    assert (session_dir / "raw" / "timeline_events.csv").is_file()
    assert (session_dir / "raw" / "timeline_events.jsonl").is_file()
    assert (session_dir / "raw" / "emotion_records.jsonl").is_file()
    assert (session_dir / "derived" / "emotion_results.csv").is_file()
    assert (session_dir / "manifest.json").is_file()

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sessions"] == [sid]
    assert manifest["total_counts"]["timeline_events"] == 1
    assert manifest["total_counts"]["emotion_records"] == 2
    assert manifest["total_counts"]["emotion_results"] == 1
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])

    expected_sha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    assert resp.headers["x-export-manifest-sha256"] == expected_sha

    events = [
        json.loads(line)
        for line in (session_dir / "raw" / "timeline_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(events) == 1
    assert events[0]["event_timestamp_estimated"] is True
    assert "event_timestamp" in events[0]
    # 事件 payload 中的禁字段被裁掉（quality_report 只记录被裁剪的键名与次数）
    assert "answer_text" not in events[0]
    assert "transcript" not in events[0]

    # 答案原文不得出现在任何导出文件中
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "SECRET_ANSWER" not in text
            assert "SECRET_TRANSCRIPT" not in text


def test_batch_time_filter_and_data_types(client, tmp_path):
    early_sid = "s-api-early-0001"
    late_sid = "s-api-late-0001"
    _seed_session(early_sid, EARLY, [(0.5, "neutral")])
    _seed_session(late_sid, LATE, [(0.5, "neutral"), (2.0, "happiness")])

    resp = client.post(
        "/api/v1/research/exports",
        json={
            "export_all": True,
            "from_utc": "2026-11-10T00:00:00Z",
            "data_types": ["emotion"],
            "format": "csv",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    root, names = _extract(tmp_path, resp.content)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    assert late_sid in manifest["sessions"]
    assert early_sid not in manifest["sessions"]
    assert manifest["total_counts"]["emotion_records"] >= 2
    assert manifest["total_counts"].get("timeline_events", 0) == 0
    assert manifest["filters"]["format"] == "csv"
    # 只要 csv，不得产出 jsonl 数据文件
    assert not [n for n in names if n.endswith(".jsonl")]
    assert (root / "sessions" / late_sid / "raw" / "emotion_records.csv").is_file()


def test_get_single_session_with_relative_seconds_and_jsonl(client, tmp_path):
    sid = "s-api-window-0001"
    _seed_session(
        sid,
        LATE,
        [(0.5, "neutral"), (1.5, "happiness"), (10.0, "neutral")],
    )

    resp = client.get(
        f"/api/v1/research/sessions/{sid}/export",
        params={
            "format": "jsonl",
            "data_types": "emotion",
            "from_seconds": 1.0,
            "to_seconds": 20.0,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    root, names = _extract(tmp_path, resp.content)
    session_dir = root / "sessions" / sid
    assert (session_dir / "raw" / "emotion_records.jsonl").is_file()
    assert not list(session_dir.rglob("*.csv"))
    rows = [
        json.loads(line)
        for line in (session_dir / "raw" / "emotion_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert [row["relative_seconds"] for row in rows] == [1.5, 10.0]


def test_export_requires_api_key_and_validates_filters(client):
    resp = client.post(
        "/api/v1/research/exports",
        json={"session_ids": ["whatever"]},
    )
    assert resp.status_code == 401

    resp = client.post(
        "/api/v1/research/exports",
        json={"data_types": ["emotion"]},
        headers=HEADERS,
    )
    assert resp.status_code == 422

    resp = client.get(
        "/api/v1/research/sessions/s-not-exist/export", headers=HEADERS
    )
    assert resp.status_code == 404


def test_list_sessions_returns_s1_and_counts(client):
    sid = "s-api-list-0001"
    _seed_session(sid, LATE, [(0.5, "neutral"), (1.5, "happiness")], with_event=True)

    resp = client.get("/api/v1/research/sessions", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    db = SessionLocal()
    try:
        expected_total = db.query(SessionModel).count()
    finally:
        db.close()
    assert data["total"] == len(data["sessions"]) == expected_total

    row = next(item for item in data["sessions"] if item["session_id"] == sid)
    assert row["emotion_records"] == 2
    assert row["timeline_events"] == 1
    assert row["has_result"] is True
    assert row["source_system"] == "standalone"
    assert row["started_at_bj"] == "2026-11-20T17:00:00+08:00"
    assert row["ended_at_bj"] == "2026-11-20T17:02:00+08:00"


def test_export_all_endpoint_without_params(client, tmp_path):
    sid = "s-api-all-0001"
    _seed_session(sid, LATE, [(0.5, "neutral")])

    resp = client.get("/api/v1/research/export/all", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"

    root, names = _extract(tmp_path, resp.content)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert sid in manifest["sessions"]
    assert manifest["filters"]["format"] == "both"
    assert (root / "sessions" / sid / "raw" / "emotion_records.csv").is_file()
    assert (root / "sessions" / sid / "derived" / "emotion_results.jsonl").is_file()


def test_export_by_beijing_time_range(client, tmp_path):
    early_sid = "s-api-time-early-0001"
    late_sid = "s-api-time-late-0001"
    _seed_session(early_sid, EARLY, [(0.5, "neutral")])
    _seed_session(late_sid, LATE, [(0.5, "neutral")])

    # 北京时间 2026-11-20 全天：UTC 09:00 = 北京 17:00，应命中；2020 年那场应排除
    resp = client.get(
        "/api/v1/research/export",
        params={"from": "2026-11-20", "to": "2026-11-20"},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    root, _ = _extract(tmp_path, resp.content)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert late_sid in manifest["sessions"]
    assert early_sid not in manifest["sessions"]

    # 只给 from 也可以
    resp = client.get(
        "/api/v1/research/export",
        params={"from": "2026-11-20"},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text

    # 格式错误、from 晚于 to → 422
    resp = client.get(
        "/api/v1/research/export", params={"from": "2026-13-40"}, headers=HEADERS
    )
    assert resp.status_code == 422
    resp = client.get(
        "/api/v1/research/export",
        params={"from": "2026-11-21", "to": "2026-11-20"},
        headers=HEADERS,
    )
    assert resp.status_code == 422


def test_api_key_query_parameter_for_get_endpoints(client, tmp_path):
    sid = "s-api-querykey-0001"
    _seed_session(sid, LATE, [(0.5, "neutral")])

    resp = client.get("/api/v1/research/sessions", params={"api_key": API_KEY})
    assert resp.status_code == 200, resp.text

    resp = client.get(
        f"/api/v1/research/sessions/{sid}/export", params={"api_key": API_KEY}
    )
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/v1/research/export/all", params={"api_key": API_KEY})
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/v1/research/sessions", params={"api_key": "wrong"})
    assert resp.status_code == 401
