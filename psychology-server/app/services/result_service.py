"""测评结果快照服务（v1.4 §14.3.3 / §14.4 / §14.6）。

- 归一化只在服务器执行：请求带原始 ``radar_charts`` 时由服务端计算
  ``dimensions`` + ``radar_norm``；只带 ``dimensions``/``radar_norm`` 时
  也按 series 的 ``unit_max`` 重算 ``norm``，不信任客户端算出的 norm。
- 幂等：``UNIQUE(idempotency_key)`` + ``UNIQUE(session_id, questionnaire_code,
  result_version)``；同键同摘要 → ``duplicate``，同键不同摘要 → 409。
"""
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.db.database import utcnow
from app.models.external_result_snapshot import ExternalResultSnapshot
from app.models.session import SessionModel
from app.schemas.result_snapshot import (
    DimensionsPayload,
    RadarNormPayload,
    RawRadarChart,
    ResultSyncRequest,
)

TRUST_LEVEL = "client_relayed"
MAX_DIMENSIONS = 32
#: 只能出现在聚合层的图；逐题图一律拒绝（§14.2 D 类）
FORBIDDEN_CHART_PREFIXES = ("chart1",)
#: 顶层硬拒绝字段（答案等价物 / 敏感长文本 / 个人信息）
FORBIDDEN_TOP_LEVEL = {
    "score_list",
    "answer_text",
    "answer_value",
    "result_interpretation",
    "recommendation",
    "aiMessage",
    "aiProfileText",
    "followUpHistory",
    "radarAnalysisResults",
    "summary_prompt",
    "psychological_profile_prompt",
    "explain_radar_chart_prompt",
    "page_title",
    "imageUrl",
    "cover_image",
    "radar_title",
    "description",
    "question_id",
    "question_number",
    "score",
    "questions",
    "voucher",
    "email",
    "phone",
    "trust_level",
}
FORBIDDEN_CHART_KEYS = {"radar_title", "description"}
#: 仅接受无前导零的十进制整数字符串（AC-36）
_CODE_PATTERN = re.compile(r"^(0|[1-9][0-9]*)$")


class ResultSyncError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _forbidden_field(payload: Dict[str, Any]) -> Optional[str]:
    for key in payload:
        if key in FORBIDDEN_TOP_LEVEL:
            return key
    charts = payload.get("radar_charts")
    if isinstance(charts, dict):
        for key in charts:
            if key.startswith(FORBIDDEN_CHART_PREFIXES):
                return key
            if key in FORBIDDEN_CHART_KEYS:
                return key
    return None


def _validate_request(payload: Dict[str, Any]) -> ResultSyncRequest:
    try:
        return ResultSyncRequest.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = first.get("msg", "invalid request")
        raise ResultSyncError(
            422, f"VALIDATION_FAILED: {location}: {message}"
        ) from exc


def _check_idempotency_key(session: SessionModel, req: ResultSyncRequest) -> None:
    if not _CODE_PATTERN.match(req.questionnaire_code):
        raise ResultSyncError(
            422,
            "IDEMPOTENCY_KEY_MALFORMED: questionnaire_code must be a decimal "
            "integer without leading zeros",
        )
    expected = (
        f"res:{session.session_id}:{req.questionnaire_code}:v{req.result_version}"
    )
    if req.idempotency_key != expected:
        raise ResultSyncError(
            422,
            "IDEMPOTENCY_KEY_MALFORMED: expected "
            f"{expected}",
        )


def _truncate(items: List[dict], flag: Dict[str, Any]) -> List[dict]:
    if len(items) > MAX_DIMENSIONS:
        flag["truncated"] = True
        return items[:MAX_DIMENSIONS]
    return items


def _build_from_charts(
    charts: Dict[str, Any], questionnaire_code: str, question_count: Optional[int]
) -> Tuple[dict, dict]:
    """从原始聚合图（chart2/chart3）计算 dimensions 与 radar_norm（§14.3）。"""
    dims: List[dict] = []
    series: List[dict] = []
    dropped: List[dict] = []
    for key, raw in charts.items():
        if key.startswith(FORBIDDEN_CHART_PREFIXES):
            raise ResultSyncError(
                422, f"FORBIDDEN_FIELD: {key} is not accepted"
            )
        try:
            chart = RawRadarChart.model_validate(raw)
        except ValidationError as exc:
            raise ResultSyncError(
                422, f"VALIDATION_FAILED: radar_charts.{key}: invalid labels/values"
            ) from exc

        # R1：values 长度等于题量且全为整数 → 逐题向量图，整图拒收
        if (
            question_count
            and len(chart.values) == question_count
            and all(float(value).is_integer() for value in chart.values)
        ):
            dropped.append({"code": key, "reason": "per_item_vector"})
            continue

        # unit_max：PHQ-9（code=22）的 chart2 可由题库 0–3 量纲推导（§14.3.3）
        unit_max = 3.0 if key.startswith("chart2") and questionnaire_code == "22" else None
        unit = "group_mean_0_3" if unit_max is not None else "unknown"
        source = "bank_scores" if unit_max is not None else "not_provided"
        precision = 2 if unit_max is not None else 1
        series_items: List[dict] = []
        for index, value in enumerate(chart.values):
            name = chart.labels[index] if index < len(chart.labels) else None
            code = f"{key}#{index}"
            dims.append({"code": code, "name": name, "score": value})
            if unit_max:
                norm = round(min(max(value / unit_max, 0.0), 1.0), 4)
            else:
                norm = None
            series_items.append(
                {"code": code, "name": name, "raw": value, "norm": norm}
            )
        series.append(
            {
                "chart": key,
                "unit": unit,
                "unit_max": unit_max,
                "unit_max_source": source,
                "raw_precision": precision,
                "norm_status": "ok" if unit_max else "unit_max_unknown",
                "items": series_items,
            }
        )

    dim_flag: Dict[str, Any] = {}
    dims = _truncate(dims, dim_flag)
    dimensions: Dict[str, Any] = {"schema": "raw_map_v1", "items": dims}
    if dropped:
        dimensions["dropped"] = dropped
    if dim_flag.get("truncated"):
        dimensions["truncated"] = True

    radar_flag: Dict[str, Any] = {}
    for entry in series:
        entry["items"] = _truncate(entry["items"], radar_flag)
    radar: Dict[str, Any] = {"schema": "radar_norm_v1", "series": series}
    if radar_flag.get("truncated"):
        radar["truncated"] = True
    return dimensions, radar


def _recompute_radar_norm(payload: RadarNormPayload) -> dict:
    """按 series 的 unit_max 重算 norm（§14.3.3），客户端 norm 不被采信。"""
    data = payload.model_dump()
    for series in data["series"]:
        unit_max = series.get("unit_max")
        items = series.get("items", [])
        series["items"] = _truncate(items, series)
        if not unit_max:
            for item in series["items"]:
                item["norm"] = None
            series["norm_status"] = "unit_max_unknown"
            continue
        status = "ok"
        for item in series["items"]:
            raw = item.get("raw")
            if raw is None:
                item["norm"] = None
                continue
            quotient = raw / unit_max
            if quotient < 0 or quotient > 1:
                status = "out_of_range"
            item["norm"] = round(min(max(quotient, 0.0), 1.0), 4)
        series["norm_status"] = status
    return data


def _resolve_dimensions_and_radar(req: ResultSyncRequest) -> Tuple[Optional[dict], Optional[dict]]:
    if req.radar_charts:
        return _build_from_charts(
            req.radar_charts, req.questionnaire_code, req.scale_question_count
        )

    dimensions = None
    if req.dimensions is not None:
        try:
            dimensions = DimensionsPayload.model_validate(req.dimensions).model_dump()
        except ValidationError as exc:
            raise ResultSyncError(
                422, "VALIDATION_FAILED: dimensions does not match raw_map_v1"
            ) from exc
        flag: Dict[str, Any] = {}
        dimensions["items"] = _truncate(dimensions["items"], flag)
        if flag.get("truncated"):
            dimensions["truncated"] = True

    radar = None
    if req.radar_norm is not None:
        try:
            radar = _recompute_radar_norm(
                RadarNormPayload.model_validate(req.radar_norm)
            )
        except ValidationError as exc:
            raise ResultSyncError(
                422, "VALIDATION_FAILED: radar_norm does not match radar_norm_v1"
            ) from exc
    return dimensions, radar


def _previous_snapshot(
    db: DBSession, session_id: str, questionnaire_code: str, result_version: int
) -> Optional[ExternalResultSnapshot]:
    return (
        db.query(ExternalResultSnapshot)
        .filter(
            ExternalResultSnapshot.session_id == session_id,
            ExternalResultSnapshot.questionnaire_code == questionnaire_code,
            ExternalResultSnapshot.result_version < result_version,
        )
        .order_by(ExternalResultSnapshot.result_version.desc())
        .first()
    )


def _duplicate_response(
    snapshot: ExternalResultSnapshot, duplicate: bool
) -> Tuple[ExternalResultSnapshot, bool]:
    return snapshot, duplicate


def create_snapshot(
    db: DBSession, session: SessionModel, payload: Dict[str, Any]
) -> Tuple[ExternalResultSnapshot, bool]:
    forbidden = _forbidden_field(payload)
    if forbidden is not None:
        raise ResultSyncError(
            422, f"FORBIDDEN_FIELD: {forbidden} is not accepted"
        )

    req = _validate_request(payload)
    _check_idempotency_key(session, req)

    if session.status == "aborted":
        raise ResultSyncError(
            409, "SESSION_ABORTED: session is aborted, results are not accepted"
        )

    if (
        req.external_session_id is not None
        and session.external_session_id is not None
        and req.external_session_id != session.external_session_id
    ):
        raise ResultSyncError(
            422,
            "RESULT_SESSION_MISMATCH: external_session_id does not match session",
        )

    existing = (
        db.query(ExternalResultSnapshot)
        .filter(ExternalResultSnapshot.idempotency_key == req.idempotency_key)
        .first()
    )
    if existing is not None:
        if existing.source_payload_sha256 == req.source_payload_sha256:
            return _duplicate_response(existing, True)
        raise ResultSyncError(
            409, "IDEMPOTENCY_CONFLICT: same key with different payload"
        )

    conflict = (
        db.query(ExternalResultSnapshot)
        .filter(
            ExternalResultSnapshot.session_id == session.session_id,
            ExternalResultSnapshot.questionnaire_code == req.questionnaire_code,
            ExternalResultSnapshot.result_version == req.result_version,
        )
        .first()
    )
    if conflict is not None:
        raise ResultSyncError(
            409,
            "IDEMPOTENCY_CONFLICT: session/questionnaire/result_version already exists",
        )

    dimensions, radar = _resolve_dimensions_and_radar(req)

    supersedes = req.supersedes_snapshot_id
    if supersedes is None and req.result_version > 1:
        previous = _previous_snapshot(
            db, session.session_id, req.questionnaire_code, req.result_version
        )
        supersedes = previous.snapshot_id if previous is not None else None

    now: datetime = utcnow()
    snapshot = ExternalResultSnapshot(
        snapshot_id="rs_" + uuid.uuid4().hex,
        session_id=session.session_id,
        source_system=req.source_system or session.source_system,
        source_result_id=req.source_result_id,
        questionnaire_code=req.questionnaire_code,
        questionnaire_version=req.questionnaire_version,
        scale_algorithm_version=req.scale_algorithm_version,
        result_version=req.result_version,
        supersedes_snapshot_id=supersedes,
        status=req.status,
        total_score=req.total_score,
        scoring_mode=req.scoring_mode,
        state_update_status=req.state_update_status,
        scale_name=req.scale_name,
        scale_question_count=req.scale_question_count,
        dimensions=dimensions,
        radar_norm=radar,
        risk_level=req.risk_level,
        level_code=req.level_code,
        recommendation_code=req.recommendation_code,
        ai_profile_score=req.ai_profile_score,
        summary_code=req.summary_code,
        trust_level=TRUST_LEVEL,
        signature=req.signature,
        signature_algorithm=req.signature_algorithm,
        source_payload_sha256=req.source_payload_sha256,
        idempotency_key=req.idempotency_key,
        received_at=now,
        created_at=now,
    )
    db.add(snapshot)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(ExternalResultSnapshot)
            .filter(ExternalResultSnapshot.idempotency_key == req.idempotency_key)
            .first()
        )
        if existing is not None:
            if existing.source_payload_sha256 == req.source_payload_sha256:
                return _duplicate_response(existing, True)
            raise ResultSyncError(
                409, "IDEMPOTENCY_CONFLICT: same key with different payload"
            )
        raise ResultSyncError(
            409, "IDEMPOTENCY_CONFLICT: snapshot already exists"
        )
    db.refresh(snapshot)
    return _duplicate_response(snapshot, False)
