"""Timeline 事件服务：批量入库、事件级幂等、质量推导、查询。

P0 事实（§8）：设备/客户端不上报锚点与 monotonic 时钟，``protocol_version``
缺失时推断为 1.0，因此 P0 的事件 ``quality`` 只会是 ``legacy`` 或 ``missing``，
不会产出 ``estimated`` / ``device``。
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.db.database import utcnow
from app.models.session import SessionModel
from app.models.timeline_event import TimelineEvent
from app.schemas.event import (
    ALLOWED_PAYLOAD_KEYS,
    FORBIDDEN_EVENT_KEYS,
    TimelineEventBatchRequest,
    TimelineEventIn,
)

#: 单批事件上限（§12.4 U-2）
MAX_EVENTS_PER_BATCH = 2000
#: 未来时间容差
FUTURE_TOLERANCE_SECONDS = 60
#: 时间上界额外容差（§8.4）
UPPER_BOUND_MARGIN_SECONDS = 600
#: 墙钟偏移告警阈值
CLOCK_SKEW_THRESHOLD_MS = 300000


class EventIngestError(Exception):
    """事件入库失败；由 API 层转换为统一错误响应。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _find_forbidden_key(value: Any) -> Optional[str]:
    """递归查找答案类禁字段。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_EVENT_KEYS:
                return key
            found = _find_forbidden_key(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_forbidden_key(item)
            if found is not None:
                return found
    return None


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _protocol_version(value: Optional[str], fallback: str) -> str:
    return (value or fallback or "1.0").strip()


def _protocol_lt(version: str, target: str = "2.0") -> bool:
    try:
        return float(version) < float(target)
    except (TypeError, ValueError):
        return True


def _derive_quality(
    protocol_version: str,
    stream_id: Optional[str],
    device_timestamp: Optional[str],
    monotonic_ms: Optional[int],
) -> str:
    """按 §8.5 优先级推导质量等级（自上而下第一个命中）。"""
    if not stream_id or _protocol_lt(protocol_version):
        return "legacy"
    if device_timestamp is None and monotonic_ms is None:
        return "missing"
    if monotonic_ms is not None or device_timestamp is not None:
        # 设备时钟未校验，P0 不会产出 device
        return "estimated"
    return "missing"


def _validate_event(
    raw: Dict[str, Any], request_protocol: str
) -> TimelineEventIn:
    if not isinstance(raw, dict):
        raise EventIngestError(400, "BAD_REQUEST: each event must be an object")
    try:
        event = TimelineEventIn.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = first.get("msg", "invalid event")
        raise EventIngestError(
            422, f"VALIDATION_FAILED: {location}: {message}"
        ) from exc

    if event.payload:
        allowed = ALLOWED_PAYLOAD_KEYS.get(event.event_type, set())
        unexpected = set(event.payload) - allowed
        if unexpected:
            raise EventIngestError(
                422,
                "FORBIDDEN_PAYLOAD: "
                f"{event.event_type} payload contains {sorted(unexpected)}",
            )
    # 协议版本：事件级优先，其次批次级，最后推断 1.0
    if event.protocol_version is None:
        event.protocol_version = request_protocol
    return event


def _session_elapsed_seconds(session: SessionModel, now: datetime) -> float:
    start = session.started_at or session.created_at
    end = session.ended_at if session.ended_at is not None else now
    elapsed = (end - start).total_seconds()
    return max(elapsed, 0.0)


def _quality_info(
    event: TimelineEventIn,
    protocol_version: str,
    flags: List[str],
    now: datetime,
) -> Dict[str, Any]:
    return {
        "protocol_version": protocol_version,
        "stream_id": event.stream_id,
        "stream_source": "client" if event.stream_id else None,
        "device_timestamp": event.device_timestamp,
        "monotonic_ms": event.monotonic_ms,
        "clock_sync_status": "unverified",
        "server_received_at": now.isoformat(),
        "flags": flags,
    }


def _existing_event_ids(
    db: DBSession, session_id: str, source: str, event_ids: Iterable[str]
) -> set:
    ids = list(event_ids)
    if not ids:
        return set()
    rows = (
        db.query(TimelineEvent.event_id)
        .filter(
            TimelineEvent.session_id == session_id,
            TimelineEvent.source == source,
            TimelineEvent.event_id.in_(ids),
        )
        .all()
    )
    return {row[0] for row in rows}


def create_events(
    db: DBSession,
    session: SessionModel,
    req: TimelineEventBatchRequest,
) -> Tuple[int, int, str, datetime]:
    """批量写入事件，返回 (accepted, skipped_duplicate, upload_id, server_received_at)。"""
    now = utcnow()

    if session.status == "aborted":
        raise EventIngestError(
            409, "SESSION_ABORTED: session is aborted, events are not accepted"
        )
    if session.status in ("completed", "interrupted"):
        deadline = session.late_upload_deadline
        if deadline is None or now > deadline:
            raise EventIngestError(
                409, "LATE_UPLOAD_EXPIRED: late upload window has expired"
            )

    # 1) 禁字段检查必须先于 schema 校验（AC-06：整批 400，不部分入库）
    for raw in req.events:
        forbidden = _find_forbidden_key(raw)
        if forbidden is not None:
            raise EventIngestError(
                400,
                f"FORBIDDEN_FIELD: {forbidden} is not accepted by timeline events",
            )

    # 2) 逐条结构校验
    validated: List[TimelineEventIn] = [
        _validate_event(raw, req.schema_version) for raw in req.events
    ]

    # 3) 未来时间（L1）
    future_limit = now + timedelta(seconds=FUTURE_TOLERANCE_SECONDS)
    for event in validated:
        occurred_at = _to_naive_utc(event.occurred_at)
        if occurred_at > future_limit:
            raise EventIngestError(
                422, "CLOCK_SKEW_TOO_LARGE: occurred_at is too far in the future"
            )

    bound = _session_elapsed_seconds(session, now) + UPPER_BOUND_MARGIN_SECONDS

    # 4) 事件级去重（同批次 + 已入库）
    seen: set = set()
    pending: List[TimelineEvent] = []
    skipped = 0
    existing = _existing_event_ids(
        db, session.session_id, req.source, (e.event_id for e in validated)
    )
    timestamps: set = set()
    for event in validated:
        if event.event_id in seen or event.event_id in existing:
            skipped += 1
            continue
        seen.add(event.event_id)
        # 同批次严格重复时间戳（无 frame_index 可区分）→ L1 拒绝
        if event.relative_seconds in timestamps:
            raise EventIngestError(
                422,
                "DUPLICATE_TIMESTAMP: duplicate relative_seconds in one batch",
            )
        timestamps.add(event.relative_seconds)

        protocol = _protocol_version(event.protocol_version, req.schema_version)
        flags: List[str] = ["clock_unverified"]
        if -60.0 <= event.relative_seconds < 0:
            flags.append("pre_anchor")
        if event.relative_seconds > bound:
            flags.append("out_of_range")
        if (
            event.clock_offset_ms is not None
            and abs(event.clock_offset_ms) > CLOCK_SKEW_THRESHOLD_MS
        ):
            flags.append("clock_skew")

        pending.append(
            TimelineEvent(
                event_id=event.event_id.lower(),
                session_id=session.session_id,
                device_id=event.device_id,
                stream_id=event.stream_id,
                event_type=event.event_type,
                source=req.source,
                occurred_at=_to_naive_utc(event.occurred_at),
                relative_seconds=event.relative_seconds,
                device_timestamp=event.device_timestamp,
                monotonic_ms=event.monotonic_ms,
                clock_offset_ms=event.clock_offset_ms,
                time_uncertainty_ms=event.time_uncertainty_ms,
                server_received_at=now,
                task_id=event.task_id,
                questionnaire_id=event.questionnaire_id,
                questionnaire_version=event.questionnaire_version,
                question_id=event.question_id,
                question_index=event.question_index,
                duration_ms=event.duration_ms,
                payload=event.payload,
                schema_version=req.schema_version,
                protocol_version=protocol,
                quality=_derive_quality(
                    protocol,
                    event.stream_id,
                    event.device_timestamp,
                    event.monotonic_ms,
                ),
                quality_info=_quality_info(event, protocol, flags, now),
                client_request_id=req.client_request_id,
                created_at=now,
            )
        )

    if pending:
        db.add_all(pending)
        try:
            db.commit()
        except IntegrityError:
            # 并发重放：唯一约束兜底，回滚后重新按 event_id 去重
            db.rollback()
            existing = _existing_event_ids(
                db, session.session_id, req.source, (e.event_id for e in pending)
            )
            retry = [e for e in pending if e.event_id not in existing]
            skipped += len(pending) - len(retry)
            if retry:
                db.add_all(retry)
                db.commit()
            pending = retry

    return len(pending), skipped, uuid.uuid4().hex, now


def list_events(
    db: DBSession,
    session_id: str,
    from_seconds: Optional[float] = None,
    to_seconds: Optional[float] = None,
    event_types: Optional[List[str]] = None,
    question_id: Optional[str] = None,
    limit: int = 200,
    cursor: Optional[int] = None,
) -> List[TimelineEvent]:
    query = db.query(TimelineEvent).filter(TimelineEvent.session_id == session_id)
    if from_seconds is not None:
        query = query.filter(TimelineEvent.relative_seconds >= from_seconds)
    if to_seconds is not None:
        query = query.filter(TimelineEvent.relative_seconds <= to_seconds)
    if event_types:
        query = query.filter(TimelineEvent.event_type.in_(event_types))
    if question_id is not None:
        query = query.filter(TimelineEvent.question_id == question_id)
    if cursor is not None:
        query = query.filter(TimelineEvent.id > cursor)
    return query.order_by(TimelineEvent.id.asc()).limit(limit).all()
