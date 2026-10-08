"""收尾补传：手机先 completed 或 reaper 置 interrupted 后，设备才批量上传。

新时序：Atlas 轮询到会话结束/消失后才上传 data。云端对"已结束但尚无结果"
的会话允许补传一次；已有结果后仅相同 client_request_id 幂等、其余 409。
"""
import uuid

from app.db.database import SessionLocal
from app.models.session import SessionModel
from app.services import session_service

DEV = "atlas_late_test"
STATE: dict = {}

TIMELINE = [
    {"relative_seconds": 0.0, "expression": "neutral", "confidence": 0.88},
    {"relative_seconds": 0.5, "expression": "happiness", "confidence": 0.81},
    {"relative_seconds": 1.0, "expression": "neutral", "confidence": 0.9},
]


def _headers():
    return {"X-Device-Id": DEV, "X-Device-Token": STATE["token"]}


def _register_and_online(client):
    resp = client.post(
        "/api/v1/devices/register",
        json={"device_id": DEV, "device_name": "补传测试机",
              "device_type": "edge_ai_device"},
    )
    assert resp.status_code == 200, resp.text
    STATE["token"] = resp.json()["device_token"]
    resp = client.post(f"/api/v1/devices/{DEV}/heartbeat",
                       json={}, headers=_headers())
    assert resp.status_code == 200


def _create_session(client):
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "session_type": "emotion"},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _upload(client, sid, request_id):
    return client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": request_id,
            "session_meta": {"elapsed_seconds": 1.0, "video_frames": 10,
                             "display_fps": 5.0, "face_detected_frames": 10,
                             "inference_runs": 3},
            "timeline": TIMELINE,
        },
        headers=_headers(),
    )


def test_01_upload_after_completed_without_result(client):
    """手机先 completed（无结果）→ 设备补传成功。"""
    _register_and_online(client)
    sid = _create_session(client)
    # 手机直接收尾，此时设备还没传任何数据
    resp = client.post(f"/api/v1/sessions/{sid}/status",
                       json={"status": "completed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    request_id = str(uuid.uuid4())
    resp = _upload(client, sid, request_id)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["duplicate"] is False
    assert data["session_status"] == "completed"
    assert data["result"]["dominant_expression"] in ("neutral", "happiness")
    STATE["completed_sid"] = sid
    STATE["completed_req"] = request_id

    # result 可查
    resp = client.get(f"/api/v1/emotion/sessions/{sid}/result")
    assert resp.status_code == 200
    assert resp.json()["result"]["face_coverage_percent"] == 100.0


def test_02_upload_after_interrupted_without_result(client):
    """reaper 置 interrupted（无结果）→ 设备恢复后补传成功，状态回到 completed。"""
    sid = _create_session(client)
    client.post(f"/api/v1/sessions/{sid}/status", json={"status": "running"})

    db = SessionLocal()
    try:
        session_obj = (
            db.query(SessionModel)
            .filter(SessionModel.session_id == sid)
            .one()
        )
        session_service.mark_interrupted(db, session_obj, reason="device heartbeat lost")
    finally:
        db.close()

    resp = _upload(client, sid, str(uuid.uuid4()))
    assert resp.status_code == 200, resp.text
    assert resp.json()["duplicate"] is False
    assert resp.json()["session_status"] == "completed"

    db = SessionLocal()
    try:
        session_obj = (
            db.query(SessionModel)
            .filter(SessionModel.session_id == sid)
            .one()
        )
        assert session_obj.status == "completed"
        assert session_obj.ended_at is not None
    finally:
        db.close()


def test_03_late_upload_retry_same_uuid_is_idempotent(client):
    """补传成功后的网络重试（同 UUID）→ duplicate=true，不重复入库。"""
    sid = STATE["completed_sid"]
    resp = _upload(client, sid, STATE["completed_req"])
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is True


def test_04_late_upload_new_uuid_after_result_rejected(client):
    """已有结果后换 UUID 再传 → 409，绝不重复入库。"""
    sid = STATE["completed_sid"]
    resp = _upload(client, sid, str(uuid.uuid4()))
    assert resp.status_code == 409
