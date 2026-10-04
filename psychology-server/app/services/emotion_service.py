"""表情识别业务服务：数据校验、timeline 时长计算、结果重算与落库。

核心原则：
1. 开发板只上传原始 timeline，最终统计结果一律由服务器重新计算；
2. 算法只封装在本文件，API 路由不写业务逻辑，以后修改算法只改这里。
"""
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
from app.schemas.emotion import (
    DeviceEmotionStatusRequest,
    SessionMeta,
    TimelineItem,
)

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


def upload_emotion_data(
    db: DBSession,
    session: SessionModel,
    client_request_id: str,
    session_meta: SessionMeta,
    timeline: List[TimelineItem],
) -> tuple[EmotionResult, bool]:
    """处理批量上传：幂等（重复请求不重复插入），成功后 session 自动 completed。

    返回 (result, duplicate)。
    """
    if len(timeline) == 0:
        raise EmotionBusinessError("timeline is empty", status_code=422)
    if session.status == "completed":
        # 已完成：只有完全相同的 client_request_id 才幂等返回，否则拒绝
        existing = _get_result(db, session.session_id)
        if existing is not None and existing.client_request_id == client_request_id:
            return existing, True
        raise EmotionBusinessError(
            "session already completed, re-upload is not allowed",
            status_code=409,
        )

    existing = _get_result(db, session.session_id)
    if existing is not None:
        if existing.client_request_id == client_request_id:
            return existing, True
        raise EmotionBusinessError(
            "session already has data from another request", status_code=409
        )

    computed = compute_result(session_meta, timeline)
    now = utcnow()

    db.add_all(
        [
            EmotionRecord(
                session_id=session.session_id,
                relative_seconds=item.relative_seconds,
                expression=item.expression,
                confidence=item.confidence,
                created_at=now,
            )
            for item in timeline
        ]
    )
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

    # 上传完成即视为该次测评结束
    if session.started_at is None:
        session.started_at = now
    session.status = "completed"
    session.ended_at = now
    session.updated_at = now
    db.commit()
    db.refresh(result)
    logger.info(
        "emotion data uploaded: session=%s records=%d dominant=%s",
        session.session_id,
        len(timeline),
        result.dominant_expression,
    )
    return result, False


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
