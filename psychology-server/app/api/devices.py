"""设备 API：注册 / 查询 / 心跳 / 身份保护。"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.core.security import (
    client_ip,
    get_current_device,
    get_device_reader,
    require_api_key,
    require_app_key,
)
from app.db.database import get_db, utcnow
from app.models.device import Device
from app.schemas.device import (
    DeviceConnectRequest,
    DeviceConnectResponse,
    DeviceDisconnectRequest,
    DeviceDisconnectResponse,
    DeviceOut,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceReinstateResponse,
    DeviceRevokeResponse,
    DeviceTokenRotateResponse,
    HeartbeatRequest,
    HeartbeatResponse,
)
from app.schemas.session import ActiveSessionResponse, AnchorOut
from app.services import device_service, identity_service, session_service

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "/register",
    response_model=DeviceRegisterResponse,
    summary="注册设备",
    description=(
        "注册一台设备并返回 **device_token**，设备需保存 token 用于后续认证。\n\n"
        "同一 `device_id` 重复注册为幂等操作：不会新建记录，返回原 token。\n\n"
        "身份保护（D3）：注册载荷与快照不一致时仍成功，但写审计事件并返回 "
        "`X-Identity-Warning: changed`；被吊销设备返回 403 `DEVICE_REVOKED`。"
    ),
)
def register_device(
    req: DeviceRegisterRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        device, created, changes = device_service.register_device(
            db, req, source_ip=client_ip(request)
        )
    except device_service.DeviceRevokedError as exc:
        raise HTTPException(
            status_code=403, detail=f"DEVICE_REVOKED: {exc.device_id} is revoked"
        ) from exc

    if changes:
        response.headers["X-Identity-Warning"] = "changed"

    return DeviceRegisterResponse(
        device_id=device.device_id,
        device_token=device.device_token,
        created=created,
        status=device_service.effective_status(device),
        last_seen=device.last_seen,
    )


@router.post(
    "/{device_id}/rotate-token",
    response_model=DeviceTokenRotateResponse,
    summary="轮换设备 token",
    description=(
        "管理接口（需 `X-App-Key`）：原子替换 token，**旧 token 立即失效**，"
        "新明文仅本次响应返回一次，并记 `token_rotated` 审计。\n\n"
        "被吊销设备返回 403 `DEVICE_REVOKED`，请改用 `reinstate`。"
    ),
    dependencies=[Depends(require_app_key)],
)
def rotate_device_token(
    device_id: str,
    request: Request,
    db: DBSession = Depends(get_db),
) -> Any:
    device = device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")
    if device.token_revoked_at is not None:
        raise HTTPException(
            status_code=403, detail=f"DEVICE_REVOKED: {device_id} is revoked"
        )
    new_token = identity_service.rotate_token(db, device, client_ip(request))
    return DeviceTokenRotateResponse(
        device_id=device.device_id,
        device_token=new_token,
        token_rotated_at=device.token_rotated_at,
    )


@router.post(
    "/{device_id}/revoke",
    response_model=DeviceRevokeResponse,
    summary="吊销设备 token",
    description=(
        "管理接口（需 `X-App-Key`）：置 `token_revoked_at`，token 立即 401；"
        "该设备再注册返回 403 `DEVICE_REVOKED`。恢复须调用 `reinstate`。重复调用幂等。"
    ),
    dependencies=[Depends(require_app_key)],
)
def revoke_device(
    device_id: str,
    request: Request,
    db: DBSession = Depends(get_db),
) -> Any:
    device = device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")
    if device.token_revoked_at is None:
        identity_service.revoke_device(db, device, client_ip(request))
    return DeviceRevokeResponse(
        device_id=device.device_id,
        status="revoked",
        token_revoked_at=device.token_revoked_at,
    )


@router.post(
    "/{device_id}/reinstate",
    response_model=DeviceReinstateResponse,
    summary="恢复被吊销设备",
    description=(
        "管理接口（需 `X-App-Key`）：清除吊销标记并生成**新 token**（仅返回一次）。"
        "设备需人工更新配置后重启。"
    ),
    dependencies=[Depends(require_app_key)],
)
def reinstate_device(
    device_id: str,
    request: Request,
    db: DBSession = Depends(get_db),
) -> Any:
    device = device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")
    new_token = identity_service.reinstate_device(db, device, client_ip(request))
    return DeviceReinstateResponse(
        device_id=device.device_id,
        device_token=new_token,
        status="active",
        token_rotated_at=device.token_rotated_at,
    )


@router.get(
    "",
    response_model=list[DeviceOut],
    summary="列出所有设备",
    description=(
        "返回所有已注册设备（需 `X-Api-Key`，P0-17），包含每台设备的 **device_id**、"
        "名称、型号与当前在线状态。\n\n"
        "当不知道 device_id 时，先调用本接口查看；手机可据此让用户选择要连接的设备。"
    ),
    dependencies=[Depends(require_api_key)],
)
def list_devices(db: DBSession = Depends(get_db)) -> Any:
    return device_service.list_devices(db)


@router.get(
    "/{device_id}",
    response_model=DeviceOut,
    summary="查询设备",
    description=(
        "按 device_id 查询设备信息。在线状态在查询时惰性判断（超过 30 秒无心跳判为 offline）。\n\n"
        "认证（§12.2 D-4）：设备 token（`X-Device-Id` + `X-Device-Token`）、"
        "`X-App-Key`、`X-Api-Key` 三者任一。\n\n"
        "- 设备启动自检用设备 token 调用本接口；token 失效/吊销返回 401，设备据此重新注册\n"
        "- 设备 token 与 `{device_id}` 不一致返回 403\n"
        "- 响应恒不含 `device_token`"
    ),
)
def get_device(
    device_id: str,
    db: DBSession = Depends(get_db),
    caller: Optional[Device] = Depends(get_device_reader),
) -> Any:
    if caller is not None and caller.device_id != device_id:
        raise HTTPException(
            status_code=403, detail="device token does not match device_id"
        )
    device = device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")
    # 惰性刷新在线状态：不修改 ORM 实例（§12.2 D-4 / A12）
    out = DeviceOut.model_validate(device, from_attributes=True)
    out.status = device_service.effective_status(device)
    return out


@router.get(
    "/{device_id}/active-session",
    response_model=ActiveSessionResponse,
    summary="查询设备当前活跃会话",
    description=(
        "返回该设备当前未完成（created/running）的最新会话，**免认证、无需设备 token**。\n\n"
        "- 无活跃会话 → `session_id=null`，设备可持续轮询\n"
        "- 有会话 → 返回 session_id 与状态，Atlas 据此关联由手机端创建的测评\n\n"
        "建议 Atlas 每 2~3 秒轮询一次。"
    ),
)
def get_active_session(device_id: str, db: DBSession = Depends(get_db)) -> Any:
    settings = get_settings()
    device = device_service.get_device(db, device_id)
    allowed_types = (
        session_service.allowed_session_types(device) if device is not None else None
    )
    session = session_service.get_active_session(
        db, device_id, allowed_types=allowed_types
    )
    if session is None:
        return ActiveSessionResponse(
            device_id=device_id,
            server_time=utcnow(),
            poll_interval_seconds=settings.poll_interval_seconds,
        )
    anchor = None
    if session.anchor_utc_at is not None and session.anchor_source is not None:
        anchor = AnchorOut(
            utc_at=session.anchor_utc_at,
            source=session.anchor_source,
            uncertainty_ms=session.anchor_uncertainty_ms or 0,
        )
    return ActiveSessionResponse(
        device_id=device_id,
        session_id=session.session_id,
        external_session_id=session.external_session_id,
        session_type=session.session_type,
        status=session.status,
        anchor=anchor,
        server_time=utcnow(),
        poll_interval_seconds=settings.poll_interval_seconds,
    )


@router.post(
    "/{device_id}/heartbeat",
    response_model=HeartbeatResponse,
    summary="设备心跳",
    description=(
        "设备上报心跳，服务器更新 `last_seen` 并置为 online。建议约 **30 秒**一次；"
        "需携带设备认证头。超过 30 秒无心跳将被视为 offline。"
    ),
)
def heartbeat(
    device_id: str,
    req: HeartbeatRequest | None = None,
    db: DBSession = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> Any:
    if device.device_id != device_id:
        raise HTTPException(
            status_code=403, detail="device token does not match device_id"
        )
    firmware = req.firmware_version if req is not None else None
    device = device_service.heartbeat(db, device, firmware_version=firmware)
    return HeartbeatResponse(
        device_id=device.device_id,
        status=device.status,
        last_seen=device.last_seen,
    )


@router.post(
    "/{device_id}/connect",
    response_model=DeviceConnectResponse,
    summary="手机连接设备",
    description=(
        "手机 App 点击「连接设备」时调用（**手机端接口，不需要设备 token**）。\n\n"
        "- 设备不存在 → 404\n"
        "- 设备在线 → `connected=true`，返回 connection_id，请保存\n"
        "- 设备离线 → `connected=false`（device_status=offline），手机提示设备离线\n\n"
        "同一手机对同一设备重复连接为幂等操作，返回同一个 connection_id。\n"
        "连接成功后，手机通过定时轮询 `GET /devices/{device_id}` 检测在线状态。"
    ),
)
def connect_device(
    device_id: str,
    req: DeviceConnectRequest,
    db: DBSession = Depends(get_db),
) -> Any:
    device = device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")

    device_status = device_service.effective_status(device)
    connection = device_service.connect_device(
        db, device, app_client_id=req.app_client_id, user_id=req.user_id
    )
    return DeviceConnectResponse(
        connected=device_status == "online",
        device_id=device.device_id,
        device_name=device.device_name,
        device_status=device_status,
        connection_id=connection.connection_id,
        connected_at=connection.connected_at,
    )


@router.post(
    "/{device_id}/disconnect",
    response_model=DeviceDisconnectResponse,
    summary="手机断开设备",
    description=(
        "手机退出设备页时调用，关闭本次连接。\n\n"
        "**两种定位方式**（`connection_id` 优先）：\n"
        "- 传 `connection_id`：精确关闭；不存在 → 404 `CONNECTION_NOT_FOUND`\n"
        "- 只传 `app_client_id`(+`user_id`)：按设备 + 手机实例解析当前 `connected` 连接并关闭；"
        "无活跃连接但存在历史记录时返回最近一条（已断开），完全无记录 → 404 `NO_ACTIVE_CONNECTION`\n\n"
        "两个字段都未提供 → 400 `MISSING_CONNECTION_IDENTIFIER`。重复调用幂等。"
    ),
)
def disconnect_device(
    device_id: str,
    req: DeviceDisconnectRequest | None = None,
    db: DBSession = Depends(get_db),
) -> Any:
    if req is None or (not req.connection_id and not req.app_client_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "MISSING_CONNECTION_IDENTIFIER: "
                "connection_id or app_client_id is required"
            ),
        )
    if req.connection_id:
        connection = device_service.get_connection(db, req.connection_id)
        if connection is None:
            raise HTTPException(
                status_code=404,
                detail=f"CONNECTION_NOT_FOUND: {req.connection_id}",
            )
        if connection.device_id != device_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"CONNECTION_DEVICE_MISMATCH: {connection.connection_id} "
                    f"does not belong to {device_id}"
                ),
            )
        connection = device_service.disconnect_device(db, connection.connection_id)
    else:
        connection = device_service.disconnect_by_app_client(
            db, device_id, req.app_client_id, user_id=req.user_id
        )
        if connection is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"NO_ACTIVE_CONNECTION: device {device_id} / "
                    f"app_client_id {req.app_client_id}"
                ),
            )

    return DeviceDisconnectResponse(
        connection_id=connection.connection_id,
        device_id=connection.device_id,
        status=connection.status,
        disconnected_at=connection.disconnected_at,
    )
