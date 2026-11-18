"""修复验证：upload_batches.total_bytes 统计实际接收字节数。

覆盖：首次上传、相同 client_request_id 重试（不重复建批次、字节不变）、
不同批次大小各自准确。
"""
import uuid

from app.db.database import SessionLocal
from app.models.upload_batch import UploadBatch

DEV = "atlas_bytes_dev"
STATE: dict = {}


def _online(client) -> dict:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": DEV,
            "device_name": "字节统计测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    STATE["token"] = resp.json()["device_token"]
    headers = {"X-Device-Id": DEV, "X-Device-Token": STATE["token"]}
    assert (
        client.post(f"/api/v1/devices/{DEV}/heartbeat", json={}, headers=headers).status_code
        == 200
    )
    return headers


def _session(client, headers) -> str:
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": DEV, "session_type": "emotion"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _payload(request_id: str, count: int) -> dict:
    return {
        "client_request_id": request_id,
        "timeline": [
            {
                "relative_seconds": i * 0.5,
                "expression": "neutral",
                "confidence": 0.9,
            }
            for i in range(count)
        ],
    }


def _upload(client, sid: str, payload: dict, headers: dict):
    return client.post(
        f"/api/v1/emotion/sessions/{sid}/data", json=payload, headers=headers
    )


def _batch(sid: str, request_id: str) -> UploadBatch:
    db = SessionLocal()
    try:
        return (
            db.query(UploadBatch)
            .filter(
                UploadBatch.session_id == sid,
                UploadBatch.client_request_id == request_id,
            )
            .one()
        )
    finally:
        db.close()


def test_total_bytes_matches_received_body(client):
    headers = _online(client)
    sid = _session(client, headers)
    request_id = str(uuid.uuid4())
    resp = _upload(client, sid, _payload(request_id, 3), headers)
    assert resp.status_code == 200, resp.text

    expected = len(resp.request.content)
    assert expected > 0
    batch = _batch(sid, request_id)
    assert batch.total_bytes == expected
    assert batch.item_count == 3


def test_duplicate_upload_keeps_single_batch_and_bytes(client):
    headers = _online(client)
    sid = _session(client, headers)
    request_id = str(uuid.uuid4())
    payload = _payload(request_id, 2)

    first = _upload(client, sid, payload, headers)
    assert first.status_code == 200
    expected = len(first.request.content)

    second = _upload(client, sid, payload, headers)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True

    db = SessionLocal()
    try:
        batches = (
            db.query(UploadBatch)
            .filter(
                UploadBatch.session_id == sid,
                UploadBatch.client_request_id == request_id,
            )
            .all()
        )
    finally:
        db.close()
    assert len(batches) == 1
    assert batches[0].total_bytes == expected


def test_each_batch_records_its_own_size(client):
    headers = _online(client)
    sid = _session(client, headers)

    small_id, large_id = str(uuid.uuid4()), str(uuid.uuid4())
    small = _upload(client, sid, _payload(small_id, 1), headers)
    large = _upload(client, sid, _payload(large_id, 20), headers)
    assert small.status_code == 200 and large.status_code == 200

    small_bytes = _batch(sid, small_id).total_bytes
    large_bytes = _batch(sid, large_id).total_bytes
    assert small_bytes == len(small.request.content)
    assert large_bytes == len(large.request.content)
    assert large_bytes > small_bytes
