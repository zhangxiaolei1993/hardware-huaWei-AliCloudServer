"""会话 API：创建 / 查询 / 状态流转（running / completed）。"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.core.security import get_current_device, get_optional_device
from app.db.database import get_db
from app.models.device import Device
from app.models.session import SessionModel
from app.schemas.session import (
    SessionCreatedResponse,
    SessionCreateRequest,
    SessionOut,
    SessionStatusUpdateRequest,
)
from app.services import device_service, session_service

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _get_session_or_404(db: DBSession, session_id: str) -> SessionModel:
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return session


@router.post(
    "",
    response_model=SessionCreatedResponse,
    status_code=201,
    summary="创建会话",
    description=(
        "创建一次测评/采集会话。当前 `session_type` 仅支持 `emotion`。\n\n"
        "**前置条件：设备必须在线**（30 秒内有心跳），否则返回 409，手机应提示设备离线。\n\n"
        "**两种鉴权方式二选一：**\n"
        "- **设备端（Atlas）**：请求头带 `X-Device-Id` / `X-Device-Token`，"
        "`connection_id` 留空\n"
        "- **手机端（Flutter）**：先 connect 拿到 `connection_id` 并放入请求体，"
        "无需设备 token；连接必须仍为 connected 状态，且设备一致"
    ),
)
def create_session(
    req: SessionCreateRequest,
    db: DBSession = Depends(get_db),
    device: Device | None = Depends(get_optional_device),
) -> Any:
    if req.connection_id:
        # 手机端：校验 connection_id 有效且属于该设备
        connection = device_service.get_connection(db, req.connection_id)
        if connection is None:
            raise HTTPException(
                status_code=404, detail=f"connection {req.connection_id} not found"
            )
        if connection.status != "connected":
            raise HTTPException(
                status_code=409, detail="connection is not active, please reconnect"
            )
        if connection.device_id != req.device_id:
            raise HTTPException(
                status_code=403, detail="connection belongs to another device"
            )
    elif device is not None:
        # 设备端：只能为自己创建
        if req.device_id != device.device_id:
            raise HTTPException(
                status_code=403, detail="cannot create session for another device"
            )
    else:
        raise HTTPException(
            status_code=401,
            detail="either connection_id in body or device credentials in headers is required",
        )

    target_device = device_service.get_device(db, req.device_id)
    if target_device is None:
        raise HTTPException(status_code=404, detail=f"device {req.device_id} not registered")
    # 设备必须在线才允许创建会话；离线时拒绝，避免手机误开测评
    if device_service.effective_status(target_device) != "online":
        raise HTTPException(
            status_code=409,
            detail=f"device {req.device_id} is offline, cannot create session",
        )
    session = session_service.create_session(
        db, target_device, session_type=req.session_type, user_id=req.user_id
    )
    return SessionCreatedResponse(session_id=session.session_id, status=session.status)


@router.get(
    "/{session_id}",
    response_model=SessionOut,
    summary="查询会话",
    description="按 session_id 查询会话详情与当前状态。",
)
def get_session(session_id: str, db: DBSession = Depends(get_db)) -> Any:
    return _get_session_or_404(db, session_id)


@router.post(
    "/{session_id}/status",
    response_model=SessionOut,
    summary="更新会话状态",
    description=(
        "状态流转：`created → running → completed`。传 `running` 记录 started_at，"
        "传 `completed` 记录 ended_at；需设备认证，且只能操作本设备的会话。"
    ),
)
def update_session_status(
    session_id: str,
    req: SessionStatusUpdateRequest,
    db: DBSession = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> Any:
    session = _get_session_or_404(db, session_id)
    if session.device_id != device.device_id:
        raise HTTPException(status_code=403, detail="session belongs to another device")
    try:
        session = session_service.update_status(db, session, req.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return session
