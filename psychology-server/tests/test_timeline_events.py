"""阶段 2 验收：timeline_events（P0-3 / M2）与时间校验放宽（P0-8）。

覆盖 AC-06（禁字段整批 400）、AC-09（pre_anchor）、AC-10（out_of_range）、
事件级幂等、13 类白名单、payload 白名单、aborted 拒收、查询过滤。
"""
from datetime import datetime, timedelta, timezone
from typing import Dict
from uuid import uuid4

from app.core.config import get_settings

STATE: Dict[str, str] = {}
QH = {"X-Api-Key": get_settings().query_api_key}


def _headers(token: str, dev: str) -> dict:
    return {"X-Device-Id": dev, "X-Device-Token": token}


def _ensure_device(client, dev: str) -> dict:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": dev,
            "device_name": "事件测试设备",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["device_token"]
    hb = client.post(
        f"/api/v1/devices/{dev}/heartbeat", json={}, headers=_headers(token, dev)
    )
    assert hb.status_code == 200, hb.text
    return _headers(token, dev)


def _new_session(client, dev: str, headers: dict) -> str:
    resp = client.post(
        "/api/v1/sessions",
        json={"device_id": dev, "session_type": "emotion"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(event_type: str = "question_shown", **overrides) -> dict:
    payload = {
        "event_id": uuid4().hex,
        "event_type": event_type,
        "occurred_at": _now_iso(),
        "relative_seconds": 1.0,
    }
    payload.update(overrides)
    return payload


def _upload(client, sid: str, headers: dict, events: list, **extra):
    body = {"client_request_id": f"evt-{uuid4().hex}", "source": "app", "events": events}
    body.update(extra)
    return client.post(f"/api/v1/sessions/{sid}/events", json=body, headers=headers)


# ---------- AC-06：答案类字段整批拒绝 ----------
def test_answer_value_rejected_whole_batch(client):
    dev = "atlas_evt_forbid"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)

    events = [
        _event("question_shown", payload={"question_type": "single"}),
        _event(
            "response_submitted",
            payload={"response_changed": True, "answer_value": 3},
        ),
    ]
    resp = _upload(client, sid, headers, events)
    assert resp.status_code == 400, resp.text
    assert "FORBIDDEN_FIELD" in resp.json()["message"]

    # 整批不入库
    listed = client.get(f"/api/v1/sessions/{sid}/events", headers=QH)
    assert listed.status_code == 200
    assert listed.json()["count"] == 0


def test_top_level_answer_text_rejected(client):
    dev = "atlas_evt_forbid2"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)
    resp = _upload(client, sid, headers, [_event(answer_text="非常同意")])
    assert resp.status_code == 400
    assert "answer_text" in resp.json()["message"]


# ---------- 正常上传 + 事件级幂等 ----------
def test_upload_events_and_idempotent_replay(client):
    dev = "atlas_evt_ok"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)

    events = [
        _event("question_shown", payload={"question_type": "single", "render_ms": 120}),
        _event(
            "response_submitted",
            relative_seconds=19.98,
            payload={"response_changed": True, "input_method": "tap"},
        ),
    ]
    first = _upload(client, sid, headers, events)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["accepted"] == 2
    assert body["skipped_duplicate"] == 0
    assert body["upload_id"]
    assert body["server_received_at"]

    # 同一批事件重放（event_id 不变，client_request_id 可不同）
    second = _upload(client, sid, headers, events)
    assert second.status_code == 200
    assert second.json()["accepted"] == 0
    assert second.json()["skipped_duplicate"] == 2

    listed = client.get(f"/api/v1/sessions/{sid}/events", headers=QH).json()
    assert listed["count"] == 2
    assert listed["events"][0]["event_type"] == "question_shown"
    # P0 无 stream_id / protocol 1.0 → legacy
    assert listed["events"][0]["quality"] == "legacy"
    assert "clock_unverified" in listed["events"][0]["quality_info"]["flags"]


# ---------- AC-09：-3 秒 → pre_anchor ----------
def test_pre_anchor_flag(client):
    dev = "atlas_evt_pre"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)

    resp = _upload(client, sid, headers, [_event(relative_seconds=-3.0)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    event = client.get(f"/api/v1/sessions/{sid}/events", headers=QH).json()["events"][0]
    assert "pre_anchor" in event["quality_info"]["flags"]


def test_minus_sixty_accepted_with_pre_anchor(client):
    dev = "atlas_evt_pre60"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)

    resp = _upload(client, sid, headers, [_event(relative_seconds=-60.0)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    event = client.get(f"/api/v1/sessions/{sid}/events", headers=QH).json()["events"][0]
    assert "pre_anchor" in event["quality_info"]["flags"]


def test_minus_six_accepted_with_pre_anchor(client):
    dev = "atlas_evt_pre6"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)

    resp = _upload(client, sid, headers, [_event(relative_seconds=-6.0)])
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    event = client.get(f"/api/v1/sessions/{sid}/events", headers=QH).json()["events"][0]
    assert "pre_anchor" in event["quality_info"]["flags"]


def test_below_minus_sixty_rejected(client):
    dev = "atlas_evt_neg"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)
    resp = _upload(client, sid, headers, [_event(relative_seconds=-60.1)])
    assert resp.status_code == 422


# ---------- AC-10：超上界只标记 ----------
def test_out_of_range_marked_not_rejected(client):
    dev = "atlas_evt_range"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)

    resp = _upload(
        client,
        sid,
        headers,
        [_event(relative_seconds=100000.0)],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    event = client.get(f"/api/v1/sessions/{sid}/events", headers=QH).json()["events"][0]
    assert "out_of_range" in event["quality_info"]["flags"]


# ---------- 白名单与校验 ----------
def test_invalid_event_type_rejected(client):
    dev = "atlas_evt_type"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)
    resp = _upload(client, sid, headers, [_event("heart_rate")])
    assert resp.status_code == 422


def test_payload_key_not_allowed(client):
    dev = "atlas_evt_payload"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)
    resp = _upload(
        client,
        sid,
        headers,
        [_event("question_shown", payload={"option_text": "A"})],
    )
    assert resp.status_code == 422
    assert "FORBIDDEN_PAYLOAD" in resp.json()["message"]


def test_future_occurred_at_rejected(client):
    dev = "atlas_evt_future"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    resp = _upload(client, sid, headers, [_event(occurred_at=future)])
    assert resp.status_code == 422
    assert "CLOCK_SKEW_TOO_LARGE" in resp.json()["message"]


# ---------- 会话状态 ----------
def test_aborted_session_rejects_events(client):
    dev = "atlas_evt_aborted"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)
    client.post(f"/api/v1/sessions/{sid}/status", json={"status": "aborted"})

    resp = _upload(client, sid, headers, [_event()])
    assert resp.status_code == 409
    assert "SESSION_ABORTED" in resp.json()["message"]


def test_completed_session_accepts_late_upload_within_window(client):
    dev = "atlas_evt_late"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)
    client.post(f"/api/v1/sessions/{sid}/status", json={"status": "completed"})

    resp = _upload(client, sid, headers, [_event()])
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1


# ---------- 查询过滤 ----------
def test_list_events_filters(client):
    dev = "atlas_evt_query"
    headers = _ensure_device(client, dev)
    sid = _new_session(client, dev, headers)

    events = [
        _event("question_shown", relative_seconds=1.0, question_id="q01"),
        _event("response_submitted", relative_seconds=2.0, question_id="q01"),
        _event("question_shown", relative_seconds=30.0, question_id="q02"),
    ]
    assert _upload(client, sid, headers, events).status_code == 200

    only_shown = client.get(
        f"/api/v1/sessions/{sid}/events",
        params={"event_type": "question_shown"},
        headers=QH,
    ).json()
    assert only_shown["count"] == 2

    window = client.get(
        f"/api/v1/sessions/{sid}/events", params={"from": 0, "to": 5}, headers=QH
    ).json()
    assert window["count"] == 2

    by_question = client.get(
        f"/api/v1/sessions/{sid}/events",
        params={"question_id": "q02"},
        headers=QH,
    ).json()
    assert by_question["count"] == 1

    limited = client.get(
        f"/api/v1/sessions/{sid}/events", params={"limit": 1}, headers=QH
    ).json()
    assert limited["count"] == 1


def test_unknown_session_404(client):
    resp = client.get("/api/v1/sessions/no-such-session/events", headers=QH)
    assert resp.status_code == 404
