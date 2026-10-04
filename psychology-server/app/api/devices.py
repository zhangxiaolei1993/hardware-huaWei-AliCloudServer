"""设备 API：注册 / 查询 / 心跳。"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app.core.security import get_current_device
from app.db.database import get_db
from app.models.device import Device
from app.schemas.device import (
    DeviceConnectRequest,
    DeviceConnectResponse,
    DeviceDisconnectRequest,
    DeviceDisconnectResponse,
    DeviceOut,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    HeartbeatRequest,
    HeartbeatResponse,
)
from app.services import device_service

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "/register",
    response_model=DeviceRegisterResponse,
    summary="注册设备",
    description=(
        "注册一台设备并返回 **device_token**，设备需保存 token 用于后续认证。\n\n"
        "同一 `device_id` 重复注册为幂等操作：不会新建记录，返回原 token。"
    ),
)
def register_device(req: DeviceRegisterRequest, db: DBSession = Depends(get_db)) -> Any:
    device, created = device_service.register_device(db, req)
    return DeviceRegisterResponse(
        device_id=device.device_id,
        device_token=device.device_token,
        created=created,
        status=device_service.effective_status(device),
        last_seen=device.last_seen,
    )


@router.get(
    "/{device_id}",
    response_model=DeviceOut,
    summary="查询设备",
    description="按 device_id 查询设备信息。在线状态在查询时惰性判断（超过 30 秒无心跳判为 offline）。",
)
def get_device(device_id: str, db: DBSession = Depends(get_db)) -> Any:
    device = device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")
    # 惰性刷新在线状态
    device.status = device_service.effective_status(device)
    return device


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
    description="手机退出设备页时调用，关闭本次连接。connection_id 不存在 → 404；重复调用幂等。",
)
def disconnect_device(
    device_id: str,
    req: DeviceDisconnectRequest,
    db: DBSession = Depends(get_db),
) -> Any:
    connection = device_service.disconnect_device(db, req.connection_id)
    if connection is None:
        raise HTTPException(
            status_code=404, detail=f"connection {req.connection_id} not found"
        )
    if connection.device_id != device_id:
        raise HTTPException(status_code=403, detail="connection belongs to another device")
    return DeviceDisconnectResponse(
        connection_id=connection.connection_id,
        device_id=connection.device_id,
        status=connection.status,
        disconnected_at=connection.disconnected_at,
    )
