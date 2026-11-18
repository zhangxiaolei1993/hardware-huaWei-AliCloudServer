"""表情识别业务服务：数据校验、timeline 时长计算、结果重算与落库。

核心原则：
1. 开发板只上传原始 timeline，最终统计结果一律由服务器重新计算；
2. 算法只封装在本文件，API 路由不写业务逻辑，以后修改算法只改这里。
3. 多批次累加（P0-5）：每次上传登记 upload_batches，原始记录累加，
   结果按该会话全部记录重算并 UPDATE 同一行 emotion_results。
"""
import hashlib
import json
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import utcnow
from app.models.device_emotion_status import DeviceEmotionStatus
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.session import SessionModel
from app.models.upload_batch import UploadBatch
from app.schemas.emotion import (
    DeviceEmotionStatusRequest,
    SessionMeta,
    TimelineItem,
)
from app.services import compat

logger = get_logger(__name__)


class EmotionBusinessError(Exception):
    """业务错误：由 API 层转成对应的 HTTP 错误。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def compute_face_coverage(meta: SessionMeta) -> float:
    """服务器根据帧数重新计算人脸覆盖率，不直接信任开发板给的百分比。"""
    if meta.video_frames and meta.face_detected_frames is not None:
        return round(meta.face_detected_frames / meta.video_frames * 100.0, 2)
    return round(meta.face_coverage_percent or 0.0, 2)


def compute_durations(timeline: List[TimelineItem]) -> Dict[str, float]:
    """按相邻 relative_seconds 的差值计算每种表情的持续时间。

    区间 [t_i, t_{i+1}) 归属于第 i 条记录的表情。
    例：0.5 neutral / 1.0 neutral / 1.5 happiness / 2.0 happiness
    → neutral 1.0s，happiness 0.5s
    """
    items = sorted(timeline, key=lambda x: x.relative_seconds)
    durations: Dict[str, float] = OrderedDict()
    for prev, nxt in zip(items, items[1:]):
        gap = max(nxt.relative_seconds - prev.relative_seconds, 0.0)
        durations[prev.expression] = durations.get(prev.expression, 0.0) + gap
    return {k: round(v, 3) for k, v in durations.items()}


def compute_result(
    session_meta: SessionMeta, timeline: List[TimelineItem]
) -> Dict[str, Any]:
    """服务器重算完整结果：时长、百分比、主导表情、平均置信度、质量指标。"""
    items = sorted(timeline, key=lambda x: x.relative_seconds)

    durations = compute_durations(timeline)
    total_valid = round(sum(durations.values()), 3)

    expressions: List[Dict[str, Any]] = []
    for expr, seconds in sorted(durations.items(), key=lambda kv: -kv[1]):
        percentage = round(seconds / total_valid * 100.0, 2) if total_valid > 0 else 0.0
        expressions.append(
            {"expression": expr, "seconds": round(seconds, 2), "percentage": percentage}
        )

    # 主导表情：时长最长；并列时取出现记录更多者，保证确定性
    counts: Dict[str, int] = {}
    for item in items:
        counts[item.expression] = counts.get(item.expression, 0) + 1
    dominant = ""
    if durations:
        dominant = max(durations, key=lambda e: (durations[e], counts.get(e, 0)))

    average_confidence = (
        round(sum(i.confidence for i in items) / len(items), 4) if items else 0.0
    )

    timeline_span = (
        round(items[-1].relative_seconds - items[0].relative_seconds, 3)
        if len(items) >= 2
        else 0.0
    )

    result_json: Dict[str, Any] = {
        "expressions": expressions,
        "quality": {
            "total_records": len(items),
            "timeline_span_seconds": timeline_span,
            "valid_expression_seconds": total_valid,
            "video_frames": session_meta.video_frames,
            "display_fps": session_meta.display_fps,
            "face_detected_frames": session_meta.face_detected_frames,
            "inference_runs": session_meta.inference_runs,
            "elapsed_seconds": session_meta.elapsed_seconds,
        },
    }
    if session_meta.session_start:
        result_json["session_start"] = session_meta.session_start
    if session_meta.session_end:
        result_json["session_end"] = session_meta.session_end

    return {
        "valid_expression_seconds": total_valid,
        "dominant_expression": dominant,
        "average_confidence": average_confidence,
        "face_coverage_percent": compute_face_coverage(session_meta),
        "result_json": result_json,
    }


def _timeline_sha256(timeline: List[TimelineItem]) -> str:
    payload = [item.model_dump() for item in timeline]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _recompute_and_upsert(
    db: DBSession,
    session: SessionModel,
    client_request_id: str,
    session_meta: SessionMeta,
    now: Any,
) -> EmotionResult:
    """按会话**全部**记录重算结果，并更新同一行（P0-5：不新增结果行）。"""
    records = (
        db.query(EmotionRecord)
        .filter(EmotionRecord.session_id == session.session_id)
        .order_by(EmotionRecord.relative_seconds.asc(), EmotionRecord.id.asc())
        .all()
    )
    items = [
        TimelineItem(
            relative_seconds=record.relative_seconds,
            expression=record.expression,
            confidence=record.confidence,
        )
        for record in records
    ]
    computed = compute_result(session_meta, items)

    result = _get_result(db, session.session_id)
    if result is None:
        result = EmotionResult(
            session_id=session.session_id,
            client_request_id=client_request_id,
            valid_expression_seconds=computed["valid_expression_seconds"],
            dominant_expression=computed["dominant_expression"],
            average_confidence=computed["average_confidence"],
            face_coverage_percent=computed["face_coverage_percent"],
            result_json=computed["result_json"],
            created_at=now,
            updated_at=now,
        )
        db.add(result)
    else:
        result.client_request_id = client_request_id
        result.valid_expression_seconds = computed["valid_expression_seconds"]
        result.dominant_expression = computed["dominant_expression"]
        result.average_confidence = computed["average_confidence"]
        result.face_coverage_percent = computed["face_coverage_percent"]
        result.result_json = computed["result_json"]
        result.updated_at = now
    db.flush()
    return result


def upload_emotion_data(
    db: DBSession,
    session: SessionModel,
    client_request_id: str,
    session_meta: SessionMeta,
    timeline: List[TimelineItem],
    device_id: Optional[str] = None,
    app_client_id: Optional[str] = None,
    protocol_header: Optional[str] = None,
    stream_id: Optional[str] = None,
    device_timestamp: Optional[str] = None,
    monotonic_ms: Optional[int] = None,
    clock_offset_ms: Optional[int] = None,
    protocol_version: Optional[str] = None,
    total_bytes: int = 0,
) -> tuple[EmotionResult, bool, Dict[str, Any]]:
    """处理批量上传：批次级幂等、多批次累加、结果重算，成功后 session 自动 completed。

    返回 (result, duplicate, compatibility)，其中 compatibility 用于响应头：
    ``protocol_version`` / ``inferred`` / ``flags``。
    """
    if len(timeline) == 0:
        raise EmotionBusinessError("timeline is empty", status_code=422)

    if session.status == "aborted":
        raise EmotionBusinessError(
            "SESSION_ABORTED: session is aborted, data is not accepted",
            status_code=409,
        )

    now = utcnow()
    protocol, inferred = compat.infer_protocol_version(
        protocol_header, protocol_version
    )
    start = session.started_at or session.created_at
    end = session.ended_at if session.ended_at is not None else now
    upper_bound = (
        max((end - start).total_seconds(), 0.0)
        + compat.UPPER_BOUND_MARGIN_SECONDS
    )

    # 批次级兼容信息（用于响应头）
    _, _, effective_stream, batch_flags = compat.derive_record_quality(
        protocol_version=protocol,
        stream_id=stream_id,
        device_timestamp=device_timestamp,
        monotonic_ms=monotonic_ms,
        frame_index=None,
        clock_offset_ms=clock_offset_ms,
        relative_seconds=0.0,
        upper_bound_seconds=upper_bound,
        server_received_at=now,
    )
    compatibility = {
        "protocol_version": protocol,
        "inferred": inferred,
        "flags": batch_flags,
        "stream_id": effective_stream,
    }

    # 批次级幂等：同一 (session_id, client_request_id) 只入库一次
    batch = (
        db.query(UploadBatch)
        .filter(
            UploadBatch.session_id == session.session_id,
            UploadBatch.client_request_id == client_request_id,
        )
        .first()
    )
    if batch is not None:
        result = _get_result(db, session.session_id)
        if result is None:
            result = _recompute_and_upsert(
                db, session, client_request_id, session_meta, now
            )
            db.commit()
            db.refresh(result)
        return result, True, compatibility

    # 已收尾的会话仅在补传窗口内接受（P0-1/P0-5）
    if session.status in ("completed", "interrupted"):
        deadline = session.late_upload_deadline
        if deadline is None or now > deadline:
            raise EmotionBusinessError(
                "LATE_UPLOAD_EXPIRED: late upload window has expired",
                status_code=409,
            )
        logger.info(
            "late emotion upload accepted: session=%s previous_status=%s",
            session.session_id,
            session.status,
        )

    records = []
    for item in timeline:
        quality, quality_info, _, _ = compat.derive_record_quality(
            protocol_version=protocol,
            stream_id=stream_id,
            device_timestamp=device_timestamp,
            monotonic_ms=monotonic_ms,
            frame_index=item.frame_index,
            clock_offset_ms=clock_offset_ms,
            relative_seconds=item.relative_seconds,
            upper_bound_seconds=upper_bound,
            server_received_at=now,
        )
        records.append(
            EmotionRecord(
                session_id=session.session_id,
                device_id=device_id or session.device_id,
                stream_id=effective_stream,
                relative_seconds=item.relative_seconds,
                expression=item.expression,
                confidence=item.confidence,
                created_at=now,
                device_timestamp=device_timestamp,
                monotonic_ms=monotonic_ms,
                clock_offset_ms=clock_offset_ms,
                frame_index=item.frame_index,
                quality=quality,
                quality_info=quality_info,
                protocol_version=protocol,
            )
        )
    db.add_all(records)
    db.add(
        UploadBatch(
            upload_id="up_" + uuid.uuid4().hex,
            session_id=session.session_id,
            device_id=device_id or session.device_id,
            app_client_id=app_client_id,
            client_request_id=client_request_id,
            batch_type="emotion",
            status="received",
            item_count=len(timeline),
            total_bytes=total_bytes,
            sha256=_timeline_sha256(timeline),
            received_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()

    result = _recompute_and_upsert(db, session, client_request_id, session_meta, now)

    # 上传完成即视为该次测评结束
    if session.started_at is None:
        session.started_at = now
    session.status = "completed"
    # 手机先收尾 / reaper 中断时 ended_at 已写入，补传不覆盖该时间
    if session.ended_at is None:
        session.ended_at = now
    session.updated_at = now
    db.commit()
    db.refresh(result)
    logger.info(
        "emotion batch uploaded: session=%s batch_records=%d dominant=%s protocol=%s",
        session.session_id,
        len(timeline),
        result.dominant_expression,
        protocol,
    )
    return result, False, compatibility


def get_result(db: DBSession, session_id: str) -> Optional[EmotionResult]:
    return _get_result(db, session_id)


def _get_result(db: DBSession, session_id: str) -> Optional[EmotionResult]:
    return db.query(EmotionResult).filter(EmotionResult.session_id == session_id).first()


# ---------- 设备实时表情状态 ----------
def upsert_device_status(
    db: DBSession, device_id: str, req: DeviceEmotionStatusRequest
) -> DeviceEmotionStatus:
    """上报实时状态：同一设备只保留最新一行（存在则更新，不存在则插入）。"""
    status = (
        db.query(DeviceEmotionStatus)
        .filter(DeviceEmotionStatus.device_id == device_id)
        .first()
    )
    now = utcnow()
    if status is None:
        status = DeviceEmotionStatus(
            device_id=device_id,
            created_at=now,
            updated_at=now,
        )
        db.add(status)

    status.face_detected = req.face_detected
    status.expression_detected = req.expression_detected
    status.current_expression = req.current_expression
    status.confidence = req.confidence
    status.updated_at = now

    db.commit()
    db.refresh(status)
    logger.info(
        "emotion status upsert: device=%s face=%s expression=%s confidence=%s",
        device_id,
        req.face_detected,
        req.current_expression,
        req.confidence,
    )
    return status


def get_device_status(db: DBSession, device_id: str) -> Optional[DeviceEmotionStatus]:
    return (
        db.query(DeviceEmotionStatus)
        .filter(DeviceEmotionStatus.device_id == device_id)
        .first()
    )


def is_status_stale(status: DeviceEmotionStatus, now: Optional[Any] = None) -> bool:
    """距上次上报超过阈值（默认 10 秒）即视为过期。"""
    current = now or utcnow()
    threshold = get_settings().emotion_status_stale_seconds
    return (current - status.updated_at).total_seconds() > threshold
