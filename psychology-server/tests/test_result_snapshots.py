"""阶段 3 验收：external_result_snapshots（P0-4 / M3）。

覆盖 AC-29（答案等价物硬拒绝）、AC-30（真实 PHQ-9 样本归一化）、
AC-31（幂等重放 / 同键异载荷冲突）、AC-36（量表编码格式）。
"""
import copy
from datetime import datetime, timezone
from uuid import uuid4

from app.models.external_result_snapshot import ExternalResultSnapshot

DIMENSIONS = {
    "schema": "raw_map_v1",
    "items": [
        {"code": "chart2_phq9_groups#0", "name": "情绪症状", "score": 0.67},
        {"code": "chart2_phq9_groups#1", "name": "身体症状", "score": 2.0},
        {"code": "chart2_phq9_groups#2", "name": "认知症状", "score": 1.0},
        {"code": "chart3_phq9_function#0", "name": "学习效率", "score": 1.0},
        {"code": "chart3_phq9_function#1", "name": "工作动力", "score": 2.0},
        {"code": "chart3_phq9_function#2", "name": "社交活跃度", "score": 0.5},
        {"code": "chart3_phq9_function#3", "name": "生活自理度", "score": 3.0},
    ],
}

RADAR_NORM = {
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
                {"code": "chart2_phq9_groups#0", "name": "情绪症状", "raw": 0.67, "norm": 0.9},
                {"code": "chart2_phq9_groups#1", "name": "身体症状", "raw": 2.0, "norm": 0.9},
                {"code": "chart2_phq9_groups#2", "name": "认知症状", "raw": 1.0, "norm": 0.9},
            ],
        },
        {
            "chart": "chart3_phq9_function",
            "unit": "unknown",
            "unit_max": None,
            "unit_max_source": "not_provided",
            "raw_precision": 1,
            "norm_status": "ok",
            "items": [
                {"code": "chart3_phq9_function#0", "name": "学习效率", "raw": 1.0, "norm": 0.5},
                {"code": "chart3_phq9_function#1", "name": "工作动力", "raw": 2.0, "norm": 0.5},
                {"code": "chart3_phq9_function#2", "name": "社交活跃度", "raw": 0.5, "norm": 0.5},
                {"code": "chart3_phq9_function#3", "name": "生活自理度", "raw": 3.0, "norm": 0.5},
            ],
        },
    ],
}

RAW_CHARTS = {
    "chart2_phq9_groups": {
        "labels": ["情绪症状", "身体症状", "认知症状"],
        "values": [0.67, 2.0, 1.0],
    },
    "chart3_phq9_function": {
        "labels": ["学习效率", "工作动力", "社交活跃度", "生活自理度"],
        "values": [1.0, 2.0, 0.5, 3.0],
    },
}


def _online_device(client, dev: str) -> dict:
    resp = client.post(
        "/api/v1/devices/register",
        json={
            "device_id": dev,
            "device_name": "结果同步测试",
            "device_type": "edge_ai_device",
            "capabilities": ["emotion"],
        },
    )
    token = resp.json()["device_token"]
    headers = {"X-Device-Id": dev, "X-Device-Token": token}
    client.post(f"/api/v1/devices/{dev}/heartbeat", json={}, headers=headers)
    return headers


def _session(client, dev: str, headers: dict, **extra) -> str:
    body = {"device_id": dev, "session_type": "emotion"}
    body.update(extra)
    resp = client.post("/api/v1/sessions", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _payload(sid: str, code: str = "22", version: int = 1, **overrides) -> dict:
    payload = {
        "idempotency_key": f"res:{sid}:{code}:v{version}",
        "source_system": "xin_tan_tan",
        "external_session_id": None,
        "questionnaire_code": code,
        "questionnaire_version": None,
        "scale_algorithm_version": None,
        "scale_name": "PHQ-9抑郁症筛查量表",
        "scale_question_count": 9,
        "result_version": version,
        "status": "complete",
        "total_score": 12.0,
        "scoring_mode": "option",
        "state_update_status": "scheduled",
        "risk_level": "moderate",
        "level_code": "moderate",
        "recommendation_code": None,
        "dimensions": copy.deepcopy(DIMENSIONS),
        "radar_norm": copy.deepcopy(RADAR_NORM),
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "source_payload_sha256": "a" * 64,
    }
    payload.update(overrides)
    return payload


# ---------- AC-30：真实样本归一化 ----------
def test_real_sample_normalized(client, db_session):
    dev = "atlas_res_phq9"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)

    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=_payload(sid), headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["trust_level"] == "client_relayed"
    assert body["duplicate"] is False
    assert body["snapshot_id"].startswith("rs_")

    snapshot = (
        db_session.query(ExternalResultSnapshot)
        .filter(ExternalResultSnapshot.session_id == sid)
        .one()
    )
    # 服务端重算 norm，客户端伪造的 0.9 / 0.5 不被采信
    series = {s["chart"]: s for s in snapshot.radar_norm["series"]}
    chart2 = series["chart2_phq9_groups"]
    assert chart2["unit_max_source"] == "bank_scores"
    assert chart2["norm_status"] == "ok"
    assert [item["norm"] for item in chart2["items"]] == [0.2233, 0.6667, 0.3333]

    chart3 = series["chart3_phq9_function"]
    assert chart3["norm_status"] == "unit_max_unknown"
    assert all(item["norm"] is None for item in chart3["items"])
    assert all(item["raw"] is not None for item in chart3["items"])

    assert len(snapshot.dimensions["items"]) == 7
    assert snapshot.dimensions["schema"] == "raw_map_v1"
    assert snapshot.scoring_mode == "option"
    assert snapshot.state_update_status == "scheduled"
    assert snapshot.trust_level == "client_relayed"


def test_raw_charts_computed_server_side(client, db_session):
    dev = "atlas_res_raw"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)
    payload = _payload(sid)
    payload.pop("dimensions")
    payload.pop("radar_norm")
    payload["radar_charts"] = copy.deepcopy(RAW_CHARTS)

    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=payload, headers=headers
    )
    assert resp.status_code == 201, resp.text

    snapshot = (
        db_session.query(ExternalResultSnapshot)
        .filter(ExternalResultSnapshot.session_id == sid)
        .one()
    )
    assert len(snapshot.dimensions["items"]) == 7
    chart2 = next(
        s for s in snapshot.radar_norm["series"] if s["chart"] == "chart2_phq9_groups"
    )
    assert [item["norm"] for item in chart2["items"]] == [0.2233, 0.6667, 0.3333]


# ---------- AC-29：答案等价物硬拒绝 ----------
def test_score_list_forbidden(client):
    dev = "atlas_res_scores"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)
    payload = _payload(sid, score_list=[0, 1, 2, 3, 0, 1, 2, 3, 0])
    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=payload, headers=headers
    )
    assert resp.status_code == 422
    assert "FORBIDDEN_FIELD" in resp.json()["message"]


def test_chart1_forbidden_but_missing_chart1_ok(client):
    dev = "atlas_res_chart1"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)

    # 带 chart1 → 422
    bad = _payload(sid)
    bad.pop("dimensions")
    bad.pop("radar_norm")
    bad["radar_charts"] = {
        "chart1_phq9_items": {"labels": ["q1"], "values": [0]},
        "chart2_phq9_groups": copy.deepcopy(RAW_CHARTS["chart2_phq9_groups"]),
    }
    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=bad, headers=headers
    )
    assert resp.status_code == 422
    assert "FORBIDDEN_FIELD" in resp.json()["message"]

    # 只有 chart2/chart3（缺 chart1）是正常请求
    ok = _payload(sid)
    ok.pop("dimensions")
    ok.pop("radar_norm")
    ok["radar_charts"] = copy.deepcopy(RAW_CHARTS)
    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=ok, headers=headers
    )
    assert resp.status_code == 201, resp.text


def test_trust_level_from_body_rejected(client):
    dev = "atlas_res_trust"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)
    payload = _payload(sid, trust_level="server_verified")
    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=payload, headers=headers
    )
    assert resp.status_code == 422
    assert "FORBIDDEN_FIELD" in resp.json()["message"]


# ---------- AC-31：幂等与冲突 ----------
def test_duplicate_replay_and_conflict(client, db_session):
    dev = "atlas_res_idem"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)
    payload = _payload(sid)

    first = client.post(
        f"/api/v1/sessions/{sid}/results", json=payload, headers=headers
    )
    assert first.status_code == 201
    snapshot_id = first.json()["snapshot_id"]

    replay = client.post(
        f"/api/v1/sessions/{sid}/results", json=payload, headers=headers
    )
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["snapshot_id"] == snapshot_id

    conflict = _payload(sid, total_score=20.0, source_payload_sha256="b" * 64)
    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=conflict, headers=headers
    )
    assert resp.status_code == 409
    assert "IDEMPOTENCY_CONFLICT" in resp.json()["message"]

    count = (
        db_session.query(ExternalResultSnapshot)
        .filter(ExternalResultSnapshot.session_id == sid)
        .count()
    )
    assert count == 1


def test_malformed_key_forbidden(client):
    dev = "atlas_res_key"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)
    payload = _payload(sid, idempotency_key=f"random-{uuid4().hex}")
    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=payload, headers=headers
    )
    assert resp.status_code == 422
    assert "IDEMPOTENCY_KEY_MALFORMED" in resp.json()["message"]


# ---------- AC-36：量表编码格式 ----------
def test_questionnaire_code_format(client):
    dev = "atlas_res_code"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)
    for code in ("022", "22.0", "PHQ-9"):
        resp = client.post(
            f"/api/v1/sessions/{sid}/results",
            json=_payload(sid, code=code),
            headers=headers,
        )
        assert resp.status_code == 422, (code, resp.text)
        assert "IDEMPOTENCY_KEY_MALFORMED" in resp.json()["message"]


# ---------- 会话约束 ----------
def test_aborted_session_rejects_result(client):
    dev = "atlas_res_abort"
    headers = _online_device(client, dev)
    sid = _session(client, dev, headers)
    client.post(f"/api/v1/sessions/{sid}/status", json={"status": "aborted"})
    resp = client.post(
        f"/api/v1/sessions/{sid}/results", json=_payload(sid), headers=headers
    )
    assert resp.status_code == 409
    assert "SESSION_ABORTED" in resp.json()["message"]


def test_external_session_mismatch(client):
    dev = "atlas_res_n1"
    headers = _online_device(client, dev)
    sid = _session(
        client,
        dev,
        headers,
        session_type="assessment",
        source_system="xin_tan_tan",
        external_session_id="1742",
    )
    resp = client.post(
        f"/api/v1/sessions/{sid}/results",
        json=_payload(sid, external_session_id="9999"),
        headers=headers,
    )
    assert resp.status_code == 422
    assert "RESULT_SESSION_MISMATCH" in resp.json()["message"]


def test_unknown_session_404(client):
    resp = client.post(
        "/api/v1/sessions/no-such-session/results", json={}
    )
    assert resp.status_code == 404
