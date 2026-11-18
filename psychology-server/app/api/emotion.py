"""表情识别业务 API（当前唯一业务）。

上传：Atlas 测评结束后一次性批量上传原始 timeline，服务器重算结果。
查询：Flutter 通过 result 接口获取最终结果。
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession

from app.core.security import get_current_device, require_api_key
from app.db.database import get_db
from app.models.device import Device
from app.schemas.emotion import (
    DeviceEmotionStatusRequest,
    DeviceEmotionStatusResponse,
    EmotionResultData,
    EmotionResultResponse,
    EmotionUploadRequest,
    EmotionUploadResponse,
)
from app.services import compat, emotion_service, session_service

router = APIRouter(prefix="/emotion", tags=["emotion"])


async def _request_bytes(request: Request) -> int:
    """实际接收的请求体字节数（wire size），用于 upload_batches.total_bytes 对账。"""
    return len(await request.body())


@router.post(
    "/sessions/{session_id}/data",
    response_model=EmotionUploadResponse,
    summary="上传表情识别数据",
    description=(
        "按批次批量上传原始 timeline（支持一个会话多次上传、累加计算）。\n\n"
        "- `client_request_id`：客户端生成的唯一 ID，`(session_id, client_request_id)` 唯一，"
        "网络重试幂等、不会重复插入\n"
        "- 多批次：不同 `client_request_id` 的原始记录累加，服务器按全部记录重算并更新同一结果行\n"
        "- expression 仅允许 8 类：neutral/happiness/surprise/sadness/anger/disgust/fear/contempt\n"
        "- confidence 必须在 0~1；timeline 不允许为空\n"
        "- 会话 `aborted` → 409；`completed`/`interrupted` 仅在 late_upload_deadline 前接受\n\n"
        "服务器接收后重新计算时长、百分比、主导表情、平均置信度与人脸覆盖率。"
    ),
)
def upload_emotion_data(
    session_id: str,
    req: EmotionUploadRequest,
    response: Response,
    db: DBSession = Depends(get_db),
    device: Device = Depends(get_current_device),
    x_protocol_version: Optional[str] = Header(
        default=None, alias="X-Protocol-Version"
    ),
    raw_bytes: int = Depends(_request_bytes),
) -> Any:
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    # 多设备会话（P0-6）：主设备或已绑定辅助设备均可上传
    if not session_service.device_bound_to_session(db, session, device.device_id):
        raise HTTPException(
            status_code=403, detail="DEVICE_NOT_BOUND: device is not bound to session"
        )
    if session.session_type != "emotion":
        raise HTTPException(status_code=400, detail="session is not an emotion session")

    try:
        result, duplicate, compatibility = emotion_service.upload_emotion_data(
            db,
            session,
            client_request_id=req.client_request_id,
            session_meta=req.session_meta,
            timeline=req.timeline,
            device_id=device.device_id,
            protocol_header=x_protocol_version,
            stream_id=req.stream_id,
            device_timestamp=req.device_timestamp,
            monotonic_ms=req.monotonic_ms,
            clock_offset_ms=req.clock_offset_ms,
            protocol_version=req.protocol_version,
            total_bytes=raw_bytes,
        )
    except emotion_service.EmotionBusinessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    # P0-14：兼容接收响应头（旧固件忽略即可）
    for name, value in compat.compatibility_headers(
        compatibility["protocol_version"],
        compatibility["flags"],
        compatibility["inferred"],
    ).items():
        response.headers[name] = value

    return EmotionUploadResponse(
        session_id=session.session_id,
        session_status=session.status,
        duplicate=duplicate,
        result=EmotionResultData(
            valid_expression_seconds=result.valid_expression_seconds,
            dominant_expression=result.dominant_expression,
            average_confidence=result.average_confidence,
            face_coverage_percent=result.face_coverage_percent,
            result_json=result.result_json,
            created_at=result.created_at,
        ),
    )


@router.get(
    "/sessions/{session_id}/result",
    response_model=EmotionResultResponse,
    summary="获取表情识别结果",
    description=(
        "返回该会话由服务器计算的最终表情识别结果（Flutter 端使用，需 `X-Api-Key`）。"
        "无结果时返回 404。"
    ),
    dependencies=[Depends(require_api_key)],
)
def get_emotion_result(session_id: str, db: DBSession = Depends(get_db)) -> Any:
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    result = emotion_service.get_result(db, session_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"no emotion result for session {session_id}"
        )
    return EmotionResultResponse(
        session_id=session_id,
        result=EmotionResultData(
            valid_expression_seconds=result.valid_expression_seconds,
            dominant_expression=result.dominant_expression,
            average_confidence=result.average_confidence,
            face_coverage_percent=result.face_coverage_percent,
            result_json=result.result_json,
            created_at=result.created_at,
        ),
    )


@router.put(
    "/devices/{device_id}/status",
    response_model=DeviceEmotionStatusResponse,
    summary="上报设备实时表情状态",
    description=(
        "Atlas 在采集过程中约**每 3 秒**上报一次当前状态（需设备认证头）。\n\n"
        "- 服务器对同一设备只保留最新一行（upsert），不保存历史\n"
        "- `expression_detected=true` 时 `current_expression` 必填且为 8 类之一\n"
        "- `expression_detected=false` 时表情与置信度应为空\n"
        "- confidence 非空时必须在 0~1\n\n"
        "该接口与心跳相互独立，不影响 online/offline 判定，也不影响 Session 与批量上传。"
    ),
)
def put_device_status(
    device_id: str,
    req: DeviceEmotionStatusRequest,
    db: DBSession = Depends(get_db),
    device: Device = Depends(get_current_device),
) -> Any:
    if device.device_id != device_id:
        raise HTTPException(
            status_code=403, detail="device token does not match device_id"
        )
    status = emotion_service.upsert_device_status(db, device_id, req)
    return DeviceEmotionStatusResponse(
        device_id=device_id,
        face_detected=status.face_detected,
        expression_detected=status.expression_detected,
        current_expression=status.current_expression,
        confidence=status.confidence,
        updated_at=status.updated_at,
        stale=False,
    )


@router.get(
    "/devices/{device_id}/status",
    response_model=DeviceEmotionStatusResponse,
    summary="查询设备实时表情状态",
    description=(
        "返回该设备最新一次上报的实时表情状态（**Flutter 端使用，不需要设备 token**）。\n\n"
        "- 设备从未上报 → 404\n"
        "- `stale=true` 表示距上次上报已超过 10 秒，实时信号可能中断，"
        "App 应提示而不是停留在最后一个表情。\n\n"
        "建议 Flutter 每 3~5 秒轮询一次。"
    ),
)
def get_device_status(device_id: str, db: DBSession = Depends(get_db)) -> Any:
    status = emotion_service.get_device_status(db, device_id)
    if status is None:
        raise HTTPException(
            status_code=404, detail=f"no emotion status for device {device_id}"
        )
    return DeviceEmotionStatusResponse(
        device_id=device_id,
        face_detected=status.face_detected,
        expression_detected=status.expression_detected,
        current_expression=status.current_expression,
        confidence=status.confidence,
        updated_at=status.updated_at,
        stale=emotion_service.is_status_stale(status),
    )
