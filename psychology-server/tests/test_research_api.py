"""科研版云端（0012_research_core）接口测试。

覆盖：项目/实验组/被试 CRUD 与幂等、会话绑定、测评与逐题作答、
AI 对话、异步下载包、历史迁移、P1 interrupted 兼容、鉴权。
全部使用 conftest 的本地临时 SQLite。
"""
import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.session import SessionModel

API_KEY = get_settings().query_api_key
HEADERS = {"X-Api-Key": API_KEY}


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _online_device(client) -> tuple[str, dict]:
    dev = _uid("research_dev")
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": dev,
            "device_name": "科研接口测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code in (200, 201), resp.text
    token = resp.json()["device_token"]
    headers = {"X-Device-Id": dev, "X-Device-Token": token}
    resp = client.post(f"/api/v1/devices/{dev}/heartbeat", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    return dev, headers


def _project(client, name: str = "科研项目") -> dict:
    resp = client.post(
        "/api/v1/research/projects", json={"name": f"{name}-{_uid('p')}"}, headers=HEADERS
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _group(client, project_id: str, name: str) -> dict:
    resp = client.post(
        f"/api/v1/research/projects/{project_id}/groups",
        json={"name": name},
        headers=HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _participant(client, project_id: str, code: str, **extra) -> dict:
    body = {
        "project_id": project_id,
        "participant_code": code,
        "name": "张三",
        "phone": "13800000000",
        "gender": "male",
        "age": 20,
        "school": "示例大学",
        "grade": "大二",
    }
    body.update(extra)
    resp = client.post("/api/v1/research/participants", json=body, headers=HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _insert_session(
    session_id: str, *, participant_id: str | None = None, project_id: str | None = None,
    group_id: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        now = datetime(2026, 11, 20, 1, 0, 0)
        db.add(
            SessionModel(
                session_id=session_id,
                device_id="atlas_api_test",
                session_type="emotion",
                source_system="standalone",
                participant_id=participant_id,
                project_id=project_id,
                group_id=group_id,
                data_origin="live",
                anchor_utc_at=now,
                anchor_source="server",
                status="completed",
                started_at=now,
                ended_at=now + timedelta(minutes=2),
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()


def _result_payload(session_id: str, code: str = "22", version: int = 1) -> dict:
    return {
        "idempotency_key": f"res:{session_id}:{code}:v{version}",
        "source_system": "xin_tan_tan",
        "questionnaire_code": code,
        "scale_name": "PHQ-9抑郁症筛查量表",
        "scale_question_count": 9,
        "result_version": version,
        "status": "complete",
        "total_score": 12.0,
        "scoring_mode": "option",
        "risk_level": "moderate",
        "level_code": "moderate",
        "dimensions": {
            "schema": "raw_map_v1",
            "items": [{"code": "chart2_phq9_groups#0", "name": "情绪症状", "score": 3}],
        },
        "radar_norm": {
            "schema": "radar_norm_v1",
            "series": [
                {
                    "chart": "chart2_phq9_groups",
                    "unit": "group_mean_0_3",
                    "unit_max": 3.0,
                    "unit_max_source": "bank_scores",
                    "raw_precision": 2,
                    "norm_status": "ok",
                    "items": [
                        {
                            "code": "chart2_phq9_groups#0",
                            "name": "情绪症状",
                            "raw": 3.0,
                            "norm": 1.0,
                        }
                    ],
                }
            ],
        },
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "source_payload_sha256": "b" * 64,
    }


# ---------------------------------------------------------------------------
# 项目 / 组 / 被试
# ---------------------------------------------------------------------------
def test_project_group_participant_crud_and_idempotency(client):
    project = _project(client)
    group = _group(client, project["project_id"], "实验组 A")
    code = _uid("S")

    first = _participant(client, project["project_id"], code, group_id=group["group_id"])
    assert first["created"] is True
    assert first["name"] == "张三" and first["phone"] == "13800000000"
    assert first["group_name"] == "实验组 A"

    # 幂等：同 project_id + participant_code 返回既有记录
    dup = _participant(client, project["project_id"], code)
    assert dup["created"] is False
    assert dup["participant_id"] == first["participant_id"]
    # 幂等请求不覆盖既有字段
    assert dup["name"] == "张三"

    # 按自定义组名精确匹配
    resp = client.get(
        "/api/v1/research/participants",
        params={"project_id": project["project_id"], "group_name": "实验组 A"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert [p["participant_id"] for p in resp.json()["items"]] == [first["participant_id"]]
    resp = client.get(
        "/api/v1/research/participants",
        params={"project_id": project["project_id"], "group_name": "实验组"},
        headers=HEADERS,
    )
    assert resp.json()["items"] == []

    # 乐观锁：旧 row_version 冲突
    resp = client.patch(
        f"/api/v1/research/participants/{first['participant_id']}",
        json={"name": "李四", "row_version": first["row_version"]},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_version"] == first["row_version"] + 1
    resp = client.patch(
        f"/api/v1/research/participants/{first['participant_id']}",
        json={"name": "王五", "row_version": first["row_version"]},
        headers=HEADERS,
    )
    assert resp.status_code == 409

    # 归档 / 恢复
    resp = client.post(
        f"/api/v1/research/participants/{first['participant_id']}/archive",
        headers=HEADERS,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "archived"
    resp = client.post(
        f"/api/v1/research/participants/{first['participant_id']}/restore",
        headers=HEADERS,
    )
    assert resp.status_code == 200 and resp.json()["status"] == "active"

    # 组名重复
    resp = client.post(
        f"/api/v1/research/projects/{project['project_id']}/groups",
        json={"name": "实验组 A"},
        headers=HEADERS,
    )
    assert resp.status_code == 409


def test_batch_sync_is_idempotent(client):
    project = _project(client)
    group = _group(client, project["project_id"], "对照组")
    code = _uid("B")
    body = {
        "project_id": project["project_id"],
        "items": [
            {
                "participant_code": code,
                "name": "批量甲",
                "phone": "13900000000",
                "group_id": group["group_id"],
                "source_local_id": _uid("local"),
            }
        ],
    }
    first = client.post(
        "/api/v1/research/participants/batch-sync", json=body, headers=HEADERS
    )
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 1

    second = client.post(
        "/api/v1/research/participants/batch-sync", json=body, headers=HEADERS
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 1
    assert second.json()["results"][0]["action"] == "unchanged"


# ---------------------------------------------------------------------------
# 会话绑定 / 测评 / 逐题 / P1
# ---------------------------------------------------------------------------
def test_session_binding_assessment_items_and_interrupted(client):
    project = _project(client)
    group = _group(client, project["project_id"], "实验组 B")
    participant = _participant(
        client, project["project_id"], _uid("S"), group_id=group["group_id"]
    )
    dev, device_headers = _online_device(client)

    resp = client.post(
        "/api/v1/sessions",
        json={
            "device_id": dev,
            "session_type": "emotion",
            "participant_id": participant["participant_id"],
        },
        headers=device_headers,
    )
    assert resp.status_code == 201, resp.text
    session = resp.json()
    assert session["participant_id"] == participant["participant_id"]
    assert session["project_id"] == project["project_id"]
    assert session["group_id"] == group["group_id"]
    assert session["data_origin"] == "live"
    session_id = session["session_id"]

    # 结果同步：自动生成 assessment
    resp = client.post(
        f"/api/v1/sessions/{session_id}/results",
        json=_result_payload(session_id, "22"),
        headers=device_headers,
    )
    assert resp.status_code == 201, resp.text
    assessment_id = resp.json()["assessment_id"]
    assert assessment_id

    detail = client.get(
        f"/api/v1/research/assessments/{assessment_id}", headers=HEADERS
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["participant_id"] == participant["participant_id"]
    assert body["participant_name"] == "张三"
    assert body["questionnaire_code"] == "22"
    assert body["risk_level"] == "moderate"
    assert body["item_count"] == 0

    # 逐题作答上传 / 读取
    items_body = {
        "result_version": 1,
        "items": [
            {
                "question_id": "q1",
                "question_index": 0,
                "question_type": "single",
                "answer_value": "经常",
                "score": 2,
                "max_score": 3,
                "response_ms": 1800,
                "answered_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "question_id": "q2",
                "question_index": 1,
                "question_type": "single",
                "answer_value": 1,
                "score": 1,
                "max_score": 3,
            },
        ],
    }
    resp = client.put(
        f"/api/v1/research/experiment-sessions/{session_id}/assessments/22/items",
        json=items_body,
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["written"] == 2

    # 重放同一批：覆盖而不新增
    resp = client.put(
        f"/api/v1/research/experiment-sessions/{session_id}/assessments/22/items",
        json=items_body,
        headers=HEADERS,
    )
    assert resp.status_code == 200 and resp.json()["total"] == 2

    resp = client.get(
        f"/api/v1/research/assessments/{assessment_id}/items", headers=HEADERS
    )
    assert resp.status_code == 200 and resp.json()["total"] == 2

    # 按会话读取结果（含逐题）
    resp = client.get(
        f"/api/v1/research/experiment-sessions/{session_id}/results",
        headers=HEADERS,
    )
    assert resp.status_code == 200
    results = resp.json()["items"]
    assert len(results) == 1 and len(results[0]["items"]) == 2

    # 每被试每量表最新一次
    resp = client.get(
        "/api/v1/research/assessments",
        params={
            "participant_id": participant["participant_id"],
            "mode": "latest_per_scale",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # P1：interrupted 落库为 aborted
    resp = client.post(
        f"/api/v1/sessions/{session_id}/status",
        json={"status": "interrupted"},
        headers=device_headers,
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(
        f"/api/v1/research/experiment-sessions/{session_id}", headers=HEADERS
    )
    assert detail.json()["status"] == "aborted"
    assert detail.json()["ended_reason"] == "client_interrupted"


def test_assign_participant_backfills_assessments(client):
    project = _project(client)
    participant = _participant(client, project["project_id"], _uid("S"))
    session_id = _uid("sess")
    _insert_session(session_id)

    # 先有快照、后绑定 → 回填 assessment
    db = SessionLocal()
    try:
        from app.models.external_result_snapshot import ExternalResultSnapshot

        db.add(
            ExternalResultSnapshot(
                snapshot_id=_uid("rs"),
                session_id=session_id,
                source_system="xin_tan_tan",
                questionnaire_code="22",
                result_version=1,
                status="complete",
                total_score=9.0,
                trust_level="client_relayed",
                source_payload_sha256="c" * 64,
                idempotency_key=f"res:{session_id}:22:v1",
                received_at=datetime(2026, 11, 20, 1, 0, 0),
                created_at=datetime(2026, 11, 20, 1, 0, 0),
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.post(
        f"/api/v1/research/experiment-sessions/{session_id}/assign-participant",
        json={"participant_id": participant["participant_id"]},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["backfilled_assessments"] == 1
    assert resp.json()["project_id"] == project["project_id"]

    resp = client.get(
        f"/api/v1/research/experiment-sessions/{session_id}/results",
        headers=HEADERS,
    )
    assert resp.json()["items"][0]["questionnaire_code"] == "22"


# ---------------------------------------------------------------------------
# AI 对话
# ---------------------------------------------------------------------------
def test_conversation_and_messages(client):
    project = _project(client)
    participant = _participant(client, project["project_id"], _uid("S"))
    session_id = _uid("sess")
    _insert_session(
        session_id,
        participant_id=participant["participant_id"],
        project_id=project["project_id"],
        group_id=participant["group_id"],
    )
    resp = client.post(
        "/api/v1/research/conversations",
        json={
            "session_id": session_id,
            "external_conversation_id": _uid("ext"),
            "source": "xin_tan_tan",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 201, resp.text
    conversation_id = resp.json()["conversation_id"]

    messages = {
        "messages": [
            {
                "message_id": _uid("msg"),
                "seq": 1,
                "role": "user",
                "content": "我最近睡不好",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "message_id": _uid("msg"),
                "seq": 2,
                "role": "assistant",
                "content": "可以具体说说吗？",
                "model": "xin-tan-tan",
                "token_count": 12,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        ]
    }
    resp = client.post(
        f"/api/v1/research/conversations/{conversation_id}/messages",
        json=messages,
        headers=HEADERS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["inserted"] == 2

    # 完全重放按 seq 幂等
    resp = client.post(
        f"/api/v1/research/conversations/{conversation_id}/messages",
        json=messages,
        headers=HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["skipped"] == 2

    resp = client.get(
        f"/api/v1/research/conversations/{conversation_id}/messages",
        params={"after_seq": 1},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert [m["seq"] for m in resp.json()["items"]] == [2]

    resp = client.get(
        f"/api/v1/research/conversations/{conversation_id}",
        params={"include_messages": "true"},
        headers=HEADERS,
    )
    assert resp.json()["message_count"] == 2
    assert len(resp.json()["messages"]) == 2


# ---------------------------------------------------------------------------
# 下载中心
# ---------------------------------------------------------------------------
def test_download_job_package(client):
    project = _project(client)
    group = _group(client, project["project_id"], "实验组 C")
    participant = _participant(
        client, project["project_id"], _uid("S"), group_id=group["group_id"]
    )
    session_id = _uid("sess")
    _insert_session(
        session_id,
        participant_id=participant["participant_id"],
        project_id=project["project_id"],
        group_id=group["group_id"],
    )

    db = SessionLocal()
    try:
        now = datetime(2026, 11, 20, 1, 0, 0)
        db.add(
            EmotionRecord(
                session_id=session_id,
                relative_seconds=1.5,
                expression="neutral",
                confidence=0.9,
                created_at=now,
            )
        )
        db.add(
            EmotionResult(
                session_id=session_id,
                client_request_id=f"req-{session_id}",
                valid_expression_seconds=1.0,
                dominant_expression="neutral",
                average_confidence=0.9,
                face_coverage_percent=80.0,
                result_json={"expressions": []},
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.post(
        "/api/v1/research/download-jobs",
        json={
            "project_id": project["project_id"],
            "scope": {"mode": "group", "group_names": ["实验组 C"]},
            "format": "csv",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 202, resp.text
    export_id = resp.json()["export_id"]

    resp = client.get(
        f"/api/v1/research/download-jobs/{export_id}", headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    job = resp.json()
    assert job["status"] == "succeeded", job
    assert job["file_name"].endswith(".zip")

    resp = client.get(
        f"/api/v1/research/download-jobs/{export_id}/download", headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        names = set(archive.namelist())
        expected = {
            "participants.csv",
            "sessions.csv",
            "assessments.csv",
            "assessment_items.csv",
            "expression_summary.csv",
            "expression_timeline.csv",
            "conversation.csv",
            "metadata.json",
            "data_dictionary.csv",
        }
        assert expected.issubset(names), names
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["timezone"] == "UTC"
        assert metadata["project"]["project_id"] == project["project_id"]
        text = archive.read("participants.csv").decode("utf-8")
        assert participant["participant_id"] in text
        assert "13800000000" in text
        dictionary = archive.read("data_dictionary.csv").decode("utf-8")
        assert "research_participants.name" in dictionary
        assert "null: 云端未采集" in dictionary


# ---------------------------------------------------------------------------
# 历史迁移
# ---------------------------------------------------------------------------
def test_legacy_import_dry_run_and_idempotent(client):
    project = _project(client)
    local_id = _uid("local")
    code = _uid("L")
    body = {
        "project_id": project["project_id"],
        "dry_run": True,
        "participants": [
            {"source_local_id": local_id, "participant_code": code, "name": "历史甲"}
        ],
        "assessments": [
            {
                "source_local_id": local_id,
                "questionnaire_code": "22",
                "reported_at": "2026-05-01T10:00:00+08:00",
                "total_score": 7.0,
                "result_payload": {
                    "status": "complete",
                    "scale_name": "PHQ-9",
                    "risk_level": "mild",
                },
            }
        ],
    }
    resp = client.post(
        "/api/v1/research/import/legacy", json=body, headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    preview = resp.json()
    assert preview["participants_created"] == 1
    assert preview["assessments_created"] == 1
    resp = client.get(
        "/api/v1/research/participants",
        params={"project_id": project["project_id"]},
        headers=HEADERS,
    )
    assert resp.json()["items"] == []

    body["dry_run"] = False
    resp = client.post(
        "/api/v1/research/import/legacy", json=body, headers=HEADERS
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["sessions_created"] == 1
    assert result["assessments_created"] == 1

    # 同一内容重复导入 → 幂等跳过
    resp = client.post(
        "/api/v1/research/import/legacy", json=body, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["assessments_skipped"] == 1
    assert resp.json()["assessments_created"] == 0

    # 合成会话可见且 data_origin=legacy_import
    resp = client.get(
        "/api/v1/research/experiment-sessions",
        params={"project_id": project["project_id"]},
        headers=HEADERS,
    )
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["data_origin"] == "legacy_import"
    assert items[0]["participant_id"]


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
def test_research_requires_api_key(client):
    resp = client.get("/api/v1/research/projects")
    assert resp.status_code == 401
    resp = client.get("/api/v1/research/projects", headers={"X-Api-Key": "wrong"})
    assert resp.status_code == 401
