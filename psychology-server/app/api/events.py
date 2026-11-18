"""Timeline 事件 API：批量上传与查询（v1.4 §12.4 U-2 / §12.5 Q-1）。"""
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as DBSession

from app.core.security import get_optional_device, require_api_key
from app.db.database import get_db
from app.models.device import Device
from app.models.session import SessionModel
from app.schemas.event import (
    TimelineEventBatchRequest,
    TimelineEventListResponse,
    TimelineUploadResponse,
)
from app.services import event_service, session_service

router = APIRouter(prefix="/sessions", tags=["events"])


def _get_session_or_404(db: DBSession, session_id: str) -> SessionModel:
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return session


@router.post(
    "/{session_id}/events",
    response_model=TimelineUploadResponse,
    summary="批量上传 timeline 事件",
    description=(
        "批量上报量表/流程/语音事件（13 类白名单）。\n\n"
        "- **事件级幂等**：`UNIQUE(session_id, source, event_id)`，重复事件计入 `skipped_duplicate`\n"
        "- **答案类字段硬拒绝**：`answer_value` / `answer_text` / `selected_option_ids` / "
        "`score_delta` / `user_name` → 400，整批不入库\n"
        "- `relative_seconds` 允许 `-5s` 容差；超出会话时长 +600s 只标记 `out_of_range`，不拒绝\n"
        "- 会话 `aborted` → 409；`completed`/`interrupted` 仅在补传窗口内接受"
    ),
)
def upload_events(
    session_id: str,
    req: TimelineEventBatchRequest,
    db: DBSession = Depends(get_db),
    device: Optional[Device] = Depends(get_optional_device),
) -> Any:
    session = _get_session_or_404(db, session_id)
    # 带设备凭证时校验归属；Flutter 集成鉴权（X-App-Key）属 P0-17
    if device is not None and session.device_id != device.device_id:
        raise HTTPException(status_code=403, detail="session belongs to another device")

    try:
        accepted, skipped, upload_id, received_at = event_service.create_events(
            db, session, req
        )
    except event_service.EventIngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return TimelineUploadResponse(
        session_id=session.session_id,
        accepted=accepted,
        skipped_duplicate=skipped,
        rejected=0,
        upload_id=upload_id,
        server_received_at=received_at,
    )


@router.get(
    "/{session_id}/events",
    response_model=TimelineEventListResponse,
    summary="查询 timeline 事件",
    description=(
        "按相对时间窗、事件类型与题目过滤事件（需 `X-Api-Key`）。`from` / `to` 为相对锚点的秒数；"
        "`event_type` 可重复传入；`cursor` 传上一页最后一条的 id。"
    ),
    dependencies=[Depends(require_api_key)],
)
def get_events(
    session_id: str,
    db: DBSession = Depends(get_db),
    from_seconds: Optional[float] = Query(default=None, alias="from"),
    to_seconds: Optional[float] = Query(default=None, alias="to"),
    event_type: Optional[List[str]] = Query(default=None),
    question_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=2000),
    cursor: Optional[int] = Query(default=None, ge=0),
) -> Any:
    session = _get_session_or_404(db, session_id)
    events = event_service.list_events(
        db,
        session.session_id,
        from_seconds=from_seconds,
        to_seconds=to_seconds,
        event_types=event_type,
        question_id=question_id,
        limit=limit,
        cursor=cursor,
    )
    return TimelineEventListResponse(
        session_id=session.session_id,
        count=len(events),
        events=events,
    )
