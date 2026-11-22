"""会话 API：创建 / 查询 / 状态流转（running / completed / aborted）。"""
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from sqlalchemy.orm import Session as DBSession

from app.core.security import get_optional_device, require_api_key
from app.db.database import get_db
from app.models.device import Device
from app.models.session import SessionModel
from app.schemas.session import (
    AnchorOut,
    SessionCreatedResponse,
    SessionCreateRequest,
    SessionDeviceBindRequest,
    SessionDeviceBindResponse,
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


def _anchor(session: SessionModel) -> Optional[AnchorOut]:
    if session.anchor_utc_at is None or session.anchor_source is None:
        return None
    return AnchorOut(
        utc_at=session.anchor_utc_at,
        source=session.anchor_source,
        uncertainty_ms=session.anchor_uncertainty_ms or 0,
    )


def _to_out(session: SessionModel) -> SessionOut:
    return SessionOut(
        session_id=session.session_id,
        device_id=session.device_id,
        user_id=session.user_id,
        subject_ref=session.subject_ref,
        deprecated_identity_used=session.deprecated_identity_used,
        session_type=session.session_type,
        source_system=session.source_system,
        external_session_id=session.external_session_id,
        status=session.status,
        anchor=_anchor(session),
        late_upload_deadline=session.late_upload_deadline,
        ended_reason=session.ended_reason,
        started_at=session.started_at,
        ended_at=session.ended_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post(
    "",
    response_model=SessionCreatedResponse,
    status_code=201,
    summary="创建会话",
    description=(
        "创建一次测评/采集会话。\n\n"
        "**session_type**：`emotion` / `multimodal` / `assessment` / "
        "`physiological` / `speech` / `custom`。\n\n"
        "**source_system**（不传＝`standalone`）：`xin_tan_tan` 必须携带 "
        "`external_session_id`（N1，只读原样保存，不设唯一约束）；"
        "`standalone` 不允许携带；`custom` 不接受客户端提交（403）。\n\n"
        "**创建幂等**：同 `source_system` + `client_request_id` 重放 → "
        "`200 {created:false, session_id:<原会话>}`，不新建。"
        "同一 N1 用新的 `client_request_id` 可再次创建（响应带 `related_session_ids`）。\n\n"
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
    response: Response,
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

    source_system = req.source_system or "standalone"
    if source_system == "custom":
        raise HTTPException(
            status_code=403,
            detail="SOURCE_SYSTEM_NOT_ALLOWED: custom source_system is reserved",
        )
    if source_system == "standalone" and req.external_session_id:
        raise HTTPException(
            status_code=422,
            detail="INVALID_EXTERNAL_SESSION_ID: standalone sessions must not carry external_session_id",
        )
    if source_system == "xin_tan_tan" and not req.external_session_id:
        raise HTTPException(
            status_code=422,
            detail="EXTERNAL_SESSION_ID_REQUIRED: xin_tan_tan requires external_session_id",
        )

    target_device = device_service.get_device(db, req.device_id)
    if target_device is None:
        raise HTTPException(status_code=404, detail=f"device {req.device_id} not registered")

    # 幂等重放优先于在线检查：网络重试时设备可能刚好离线，仍应返回原会话
    if req.client_request_id:
        existing = session_service.find_by_client_request_id(
            db, source_system, req.client_request_id
        )
        if existing is not None:
            if existing.device_id != req.device_id:
                raise HTTPException(
                    status_code=409,
                    detail="CLIENT_REQUEST_ID_CONFLICT: client_request_id used by another device",
                )
            response.status_code = 200
            return SessionCreatedResponse(
                session_id=existing.session_id,
                status=existing.status,
                created=False,
                source_system=existing.source_system,
                external_session_id=existing.external_session_id,
                session_type=existing.session_type,
                related_session_ids=session_service.related_session_ids(
                    db, existing, existing.session_id
                ),
                anchor=_anchor(existing),
                late_upload_deadline=existing.late_upload_deadline,
                created_at=existing.created_at,
                subject_ref=existing.subject_ref,
                deprecated_identity_used=existing.deprecated_identity_used,
            )

    # 设备必须在线才允许创建会话；离线时拒绝，避免手机误开测评
    if device_service.effective_status(target_device) != "online":
        raise HTTPException(
            status_code=409,
            detail=f"device {req.device_id} is offline, cannot create session",
        )
    session, created, related = session_service.create_session(
        db,
        target_device,
        session_type=req.session_type,
        user_id=req.user_id,
        source_system=source_system,
        external_session_id=req.external_session_id,
        client_request_id=req.client_request_id,
        external_user_id=req.external_user_id,
    )
    if not created:
        response.status_code = 200
    return SessionCreatedResponse(
        session_id=session.session_id,
        status=session.status,
        created=created,
        source_system=session.source_system,
        external_session_id=session.external_session_id,
        session_type=session.session_type,
        related_session_ids=related,
        anchor=_anchor(session),
        late_upload_deadline=session.late_upload_deadline,
        created_at=session.created_at,
        subject_ref=session.subject_ref,
        deprecated_identity_used=session.deprecated_identity_used,
    )


@router.get(
    "/{session_id}",
    response_model=SessionOut,
    summary="查询会话",
    description=(
        "按 session_id 查询会话详情、来源系统、外部会话编号与锚点信息（需 `X-Api-Key`）。"
    ),
    dependencies=[Depends(require_api_key)],
)
def get_session(session_id: str, db: DBSession = Depends(get_db)) -> Any:
    return _to_out(_get_session_or_404(db, session_id))


@router.post(
    "/{session_id}/status",
    response_model=SessionOut,
    summary="更新会话状态",
    description=(
        "状态流转：`created → running → completed / aborted`。传 `running` 记录 started_at；"
        "传 `completed` 或 `aborted` 记录 ended_at 与 ended_reason。\n\n"
        "**免认证即可调用**（session_id 为随机 UUID、不可猜测）：手机 App 可直接收尾；"
        "若请求携带设备认证头，则必须是本设备的会话（不匹配返回 403）。"
    ),
)
def update_session_status(
    session_id: Annotated[str, Path(examples=["s_20261119_ab12cd34"], description="会话号 S1")],
    req: SessionStatusUpdateRequest,
    db: DBSession = Depends(get_db),
    device: Device | None = Depends(get_optional_device),
) -> Any:
    session = _get_session_or_404(db, session_id)
    # 带设备凭证时校验归属；不带凭证（手机端）凭 session_id 即可操作
    if device is not None and session.device_id != device.device_id:
        raise HTTPException(status_code=403, detail="session belongs to another device")
    try:
        session = session_service.update_status(db, session, req.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(session)


@router.post(
    "/{session_id}/devices",
    response_model=SessionDeviceBindResponse,
    summary="绑定设备到会话",
    description=(
        "一个会话可绑定多台设备（主设备在创建会话时自动绑定）。\n\n"
        "- 设备或会话不存在 → 404\n"
        "- 已绑定（status=bound）重复调用幂等 → `created=false`\n"
        "- 已解绑设备再次绑定 → 恢复为 bound 并更新 role"
    ),
)
def bind_session_device(
    session_id: Annotated[str, Path(examples=["s_20261119_ab12cd34"], description="会话号 S1")],
    req: SessionDeviceBindRequest,
    db: DBSession = Depends(get_db),
) -> Any:
    session = _get_session_or_404(db, session_id)
    device = device_service.get_device(db, req.device_id)
    if device is None:
        raise HTTPException(
            status_code=404, detail=f"device {req.device_id} not registered"
        )
    binding, created = session_service.bind_device(
        db, session, device, role=req.role
    )
    return SessionDeviceBindResponse(
        session_id=binding.session_id,
        device_id=binding.device_id,
        role=binding.role,
        status=binding.status,
        bound_at=binding.bound_at,
        created=created,
    )
