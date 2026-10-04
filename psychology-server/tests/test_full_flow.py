"""第一阶段完整流程测试：覆盖任务要求的 12 项检查。

测试按定义顺序执行（pytest 保持定义顺序），前序步骤产出的
token / session_id 通过 STATE 字典传递。
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session as DBSession

from app.db.database import utcnow
from app.models.device import Device
from app.services.emotion_service import compute_result
from app.schemas.emotion import SessionMeta, TimelineItem

STATE: dict = {}

HEADERS = lambda: {  # noqa: E731
    "X-Device-Id": "atlas_001",
    "X-Device-Token": STATE["token"],
}

TIMELINE = [
    {"relative_seconds": 0.5, "expression": "neutral", "confidence": 0.82},
    {"relative_seconds": 1.0, "expression": "neutral", "confidence": 0.85},
    {"relative_seconds": 1.5, "expression": "happiness", "confidence": 0.78},
    {"relative_seconds": 2.0, "expression": "happiness", "confidence": 0.80},
]


# ---------- 1. 注册 Atlas ----------
def test_01_register_device(client):
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": "atlas_001",
            "device_name": "心理测评设备01",
            "device_type": "edge_ai_device",
            "manufacturer": "Huawei",
            "model": "Atlas 200I DK A2",
            "firmware_version": "1.0.0",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["device_id"] == "atlas_001"
    assert data["created"] is True
    assert len(data["device_token"]) >= 32
    STATE["token"] = data["device_token"]


def test_01b_register_twice_is_idempotent(client):
    resp = client.post(
        "/api/v1/devices/register",
        json={"device_id": "atlas_001", "device_name": "x", "device_type": "edge_ai_device"},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] is False
    assert resp.json()["device_token"] == STATE["token"]


def test_01c_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "psychology-server"}


# ---------- 2. heartbeat → online ----------
def test_02_heartbeat_online(client):
    resp = client.post(
        "/api/v1/devices/atlas_001/heartbeat",
        json={"firmware_version": "1.0.0"},
        headers=HEADERS(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["device_id"] == "atlas_001"
    assert data["status"] == "online"


def test_02b_heartbeat_requires_auth(client):
    resp = client.post("/api/v1/devices/atlas_001/heartbeat")
    assert resp.status_code == 401


def test_02c_heartbeat_wrong_token(client):
    resp = client.post(
        "/api/v1/devices/atlas_001/heartbeat",
        headers={"X-Device-Id": "atlas_001", "X-Device-Token": "bad-token"},
    )
    assert resp.status_code == 401


def test_02d_device_query(client):
    resp = client.get("/api/v1/devices/atlas_001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert data["model"] == "Atlas 200I DK A2"
    assert data["capabilities"] == ["emotion"]


# ---------- 3. 创建 emotion session ----------
def test_03_create_session(client):
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": "atlas_001", "user_id": None, "session_type": "emotion"},
        headers=HEADERS(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "created"
    STATE["session_id"] = data["session_id"]


def test_03b_cannot_create_for_other_device(client):
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": "other_device", "session_type": "emotion"},
        headers=HEADERS(),
    )
    assert resp.status_code == 403


def test_03c_session_status_running(client):
    resp = client.post(
        f"/api/v1/sessions/{STATE['session_id']}/status",
        json={"status": "running"},
        headers=HEADERS(),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert resp.json()["started_at"] is not None


# ---------- 4/5. 上传 timeline，服务器计算结果 ----------
def test_04_upload_timeline(client):
    resp = client.post(
        f"/api/v1/emotion/sessions/{STATE['session_id']}/data",
        json={
            "client_request_id": "req-20261003-0001",
            "session_meta": {
                "session_start": "2026-10-03T10:00:00",
                "session_end": "2026-10-03T10:01:00",
                "elapsed_seconds": 60.0,
                "video_frames": 1800,
                "display_fps": 30.0,
                "face_detected_frames": 1500,
                "face_coverage_percent": 1.23,  # 服务器应按帧数重算为 83.33
                "inference_runs": 120,
            },
            "timeline": TIMELINE,
        },
        headers=HEADERS(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["duplicate"] is False
    assert data["session_status"] == "completed"
    STATE["upload_response"] = data


def test_05_server_recalculates_result(client):
    """核心断言：服务器重算，不信任开发板统计。"""
    result = STATE["upload_response"]["result"]
    # 0.5-1.0-1.5-2.0 → neutral 1.0s，happiness 0.5s，总计 1.5s
    assert result["valid_expression_seconds"] == 1.5
    assert result["dominant_expression"] == "neutral"
    # 平均置信度 (0.82+0.85+0.78+0.80)/4
    assert result["average_confidence"] == round((0.82 + 0.85 + 0.78 + 0.80) / 4, 4)
    # face coverage 按帧数重算：1500/1800 = 83.33
    assert result["face_coverage_percent"] == 83.33
    exprs = {e["expression"]: e for e in result["result_json"]["expressions"]}
    assert exprs["neutral"]["seconds"] == 1.0
    assert exprs["happiness"]["seconds"] == 0.5
    assert exprs["neutral"]["percentage"] == round(1.0 / 1.5 * 100, 2)
    assert exprs["happiness"]["percentage"] == round(0.5 / 1.5 * 100, 2)


def test_05b_result_service_function_matches(client):
    """直接调用 service 层验证算法封装正确。"""
    meta = SessionMeta(video_frames=1800, face_detected_frames=1500)
    timeline = [TimelineItem(**item) for item in TIMELINE]
    computed = compute_result(meta, timeline)
    assert computed["valid_expression_seconds"] == 1.5
    assert computed["dominant_expression"] == "neutral"


# ---------- 6. 查询 result ----------
def test_06_get_result(client):
    resp = client.get(f"/api/v1/emotion/sessions/{STATE['session_id']}/result")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == STATE["session_id"]
    assert data["result"]["dominant_expression"] == "neutral"


def test_06b_result_of_unknown_session(client):
    resp = client.get("/api/v1/emotion/sessions/no-such-session/result")
    assert resp.status_code == 404


# ---------- 7. 检查数据库 ----------
def test_07_database_check(client):
    from app.db.database import SessionLocal
    from app.models.emotion_record import EmotionRecord
    from app.models.emotion_result import EmotionResult
    from app.models.session import SessionModel

    db = SessionLocal()
    try:
        records = (
            db.query(EmotionRecord)
            .filter(EmotionRecord.session_id == STATE["session_id"])
            .all()
        )
        assert len(records) == len(TIMELINE)
        assert records[0].expression == "neutral"

        result = (
            db.query(EmotionResult)
            .filter(EmotionResult.session_id == STATE["session_id"])
            .one()
        )
        assert result.client_request_id == "req-20261003-0001"
        assert result.dominant_expression == "neutral"

        session = (
            db.query(SessionModel)
            .filter(SessionModel.session_id == STATE["session_id"])
            .one()
        )
        assert session.status == "completed"
        assert session.ended_at is not None
    finally:
        db.close()


# ---------- 8. 重复上传测试（幂等） ----------
def test_08_duplicate_upload_idempotent(client):
    resp = client.post(
        f"/api/v1/emotion/sessions/{STATE['session_id']}/data",
        json={
            "client_request_id": "req-20261003-0001",  # 与第一次相同（模拟网络重试）
            "session_meta": {},
            "timeline": TIMELINE,
        },
        headers=HEADERS(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["duplicate"] is True
    assert data["result"]["valid_expression_seconds"] == 1.5

    # 数据库中仍然只有 4 条原始记录
    from app.db.database import SessionLocal
    from app.models.emotion_record import EmotionRecord

    db = SessionLocal()
    try:
        count = (
            db.query(EmotionRecord)
            .filter(EmotionRecord.session_id == STATE["session_id"])
            .count()
        )
        assert count == len(TIMELINE)
    finally:
        db.close()


def test_08b_completed_session_rejects_new_upload(client):
    resp = client.post(
        f"/api/v1/emotion/sessions/{STATE['session_id']}/data",
        json={
            "client_request_id": "req-20261003-0002",  # 不同的 request id
            "session_meta": {},
            "timeline": TIMELINE,
        },
        headers=HEADERS(),
    )
    assert resp.status_code == 409


# ---------- 9. 非法 expression ----------
def test_09_invalid_expression_rejected(client):
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": "atlas_001", "session_type": "emotion"},
        headers=HEADERS(),
    )
    sid = resp.json()["session_id"]
    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": "req-invalid-expr",
            "session_meta": {},
            "timeline": [
                {"relative_seconds": 0.0, "expression": "excited", "confidence": 0.9}
            ],
        },
        headers=HEADERS(),
    )
    assert resp.status_code == 422


# ---------- 10. 非法 confidence ----------
def test_10_invalid_confidence_rejected(client):
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": "atlas_001", "session_type": "emotion"},
        headers=HEADERS(),
    )
    sid = resp.json()["session_id"]
    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": "req-invalid-conf",
            "session_meta": {},
            "timeline": [
                {"relative_seconds": 0.0, "expression": "neutral", "confidence": 1.5}
            ],
        },
        headers=HEADERS(),
    )
    assert resp.status_code == 422


def test_10b_empty_timeline_rejected(client):
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": "atlas_001", "session_type": "emotion"},
        headers=HEADERS(),
    )
    sid = resp.json()["session_id"]
    resp = client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={"client_request_id": "req-empty-timeline", "session_meta": {}, "timeline": []},
        headers=HEADERS(),
    )
    assert resp.status_code == 422


# ---------- 11. 不存在 session ----------
def test_11_unknown_session_rejected(client):
    resp = client.post(
        "/api/v1/emotion/sessions/no-such-session/data",
        json={
            "client_request_id": "req-unknown-session",
            "session_meta": {},
            "timeline": TIMELINE,
        },
        headers=HEADERS(),
    )
    assert resp.status_code == 404


# ---------- 12. heartbeat offline 状态 ----------
def test_12_heartbeat_offline(client, db_session: DBSession):
    # 模拟 200 秒前的心跳
    device = db_session.query(Device).filter(Device.device_id == "atlas_001").one()
    device.last_seen = utcnow() - timedelta(seconds=200)
    db_session.commit()

    resp = client.get("/api/v1/devices/atlas_001")
    assert resp.status_code == 200
    assert resp.json()["status"] == "offline"

    # 恢复心跳 → online
    resp = client.post(
        "/api/v1/devices/atlas_001/heartbeat", json={}, headers=HEADERS()
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "online"
