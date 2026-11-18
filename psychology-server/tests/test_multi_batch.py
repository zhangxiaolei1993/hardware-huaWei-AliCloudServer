"""阶段 4 验收：多批次上传累加（P0-5 / M4、AC-04 / AC-11）。"""
import uuid

from app.db.database import SessionLocal
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.upload_batch import UploadBatch

DEV = "atlas_batch_dev"
STATE: dict = {}


def _headers() -> dict:
    return {"X-Device-Id": DEV, "X-Device-Token": STATE["token"]}


def _online(client) -> None:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": DEV,
            "device_name": "多批次测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    STATE["token"] = resp.json()["device_token"]
    assert (
        client.post(f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=_headers()).status_code
        == 200
    )


def _session(client) -> str:
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "session_type": "emotion"},
        headers=_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _upload(client, sid: str, request_id: str, start: float, count: int = 3):
    timeline = [
        {
            "relative_seconds": start + index * 0.5,
            "expression": "neutral" if index % 2 == 0 else "happiness",
            "confidence": 0.8,
        }
        for index in range(count)
    ]
    return client.post(
        f"/api/v1/emotion/sessions/{sid}/data",
        json={
            "client_request_id": request_id,
            "session_meta": {"video_frames": 300, "face_detected_frames": 270},
            "timeline": timeline,
        },
        headers=_headers(),
    )


def test_two_batches_accumulate_and_recompute(client):
    _online(client)
    sid = _session(client)
    req1, req2 = str(uuid.uuid4()), str(uuid.uuid4())

    first = _upload(client, sid, req1, 0.0)
    assert first.status_code == 200, first.text
    assert first.json()["duplicate"] is False

    second = _upload(client, sid, req2, 1.5)
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is False
    # 6 条记录：0/0.5/1.0/1.5/2.0/2.5 → 5 段间隔 = 2.5s
    assert second.json()["result"]["valid_expression_seconds"] == 2.5

    db = SessionLocal()
    try:
        assert (
            db.query(EmotionRecord)
            .filter(EmotionRecord.session_id == sid)
            .count()
            == 6
        )
        assert (
            db.query(EmotionResult)
            .filter(EmotionResult.session_id == sid)
            .count()
            == 1
        )
        batches = (
            db.query(UploadBatch)
            .filter(UploadBatch.session_id == sid)
            .order_by(UploadBatch.created_at.asc())
            .all()
        )
        assert len(batches) == 2
        assert {b.client_request_id for b in batches} == {req1, req2}
        assert all(b.batch_type == "emotion" and b.status == "received" for b in batches)
        assert all(b.sha256 and len(b.sha256) == 64 for b in batches)
    finally:
        db.close()

    STATE["sid"] = sid
    STATE["req1"] = req1


def test_batch_replay_is_idempotent(client):
    sid = STATE["sid"]
    before = None
    db = SessionLocal()
    try:
        before = (
            db.query(EmotionRecord)
            .filter(EmotionRecord.session_id == sid)
            .count()
        )
    finally:
        db.close()

    resp = _upload(client, sid, STATE["req1"], 0.0)
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is True

    db = SessionLocal()
    try:
        assert (
            db.query(EmotionRecord)
            .filter(EmotionRecord.session_id == sid)
            .count()
            == before
        )
        assert (
            db.query(UploadBatch)
            .filter(UploadBatch.session_id == sid)
            .count()
            == 2
        )
    finally:
        db.close()


def test_aborted_session_rejects_batch(client):
    sid = _session(client)
    client.post(f"/api/v1/sessions/{sid}/status", json={"status": "aborted"})
    resp = _upload(client, sid, str(uuid.uuid4()), 0.0)
    assert resp.status_code == 409
    assert "SESSION_ABORTED" in resp.json()["message"]
