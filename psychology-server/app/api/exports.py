"""科研导出 API（v1.4 §12.5 Q-5 manifest / Q-6 创建导出 / Q-7 查询下载）。

鉴权统一为 `X-Api-Key`（P0-17）。导出物中的 `event_timestamp` 为**派生值**
（`anchor + relative_seconds`），一律标 `estimated`，且绝不回写记录 `quality`（AC-35）。
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from app.core.security import require_api_key
from app.db.database import get_db
from app.schemas.export import ExportCreateRequest, ExportCreateResponse
from app.services import export_service, session_service

router = APIRouter(tags=["exports"])

_MEDIA_TYPES = {"csv": "text/csv", "json": "application/json"}


@router.get(
    "/sessions/{session_id}/manifest",
    summary="获取会话数据清单（导出前置）",
    description=(
        "返回会话元数据、锚点、设备、数据流与各类数据条数、完整性校验值（需 `X-Api-Key`）。\n\n"
        "派生时间语义：`derived_time.estimated=true`，锚点不确定度见 `anchor_uncertainty_ms`；"
        "派生值不落库、不改记录 `quality`（AC-35）。"
    ),
    dependencies=[Depends(require_api_key)],
)
def get_manifest(session_id: str, db: DBSession = Depends(get_db)) -> Any:
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return export_service.build_manifest(db, session)


@router.post(
    "/sessions/{session_id}/exports",
    response_model=ExportCreateResponse,
    status_code=201,
    summary="创建科研导出",
    description=(
        "按时间窗导出表情原始序列（`csv` / `json`），需 `X-Api-Key`。\n\n"
        "- 导出物含派生 `event_timestamp`（标 `estimated`）与 `quality_info.derived_time`\n"
        "- 数据库中的 `quality` / `quality_info` 不会被改写（AC-35）\n"
        "- 默认导出全部质量等级，`quality` 原样保留在列中供筛选"
    ),
    dependencies=[Depends(require_api_key)],
)
def create_export(
    session_id: str,
    req: ExportCreateRequest,
    db: DBSession = Depends(get_db),
) -> Any:
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    try:
        meta = export_service.create_export(db, session, req)
    except export_service.ExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return ExportCreateResponse(**meta)


@router.get(
    "/exports/{export_id}",
    summary="查询导出状态/下载",
    description=(
        "返回导出任务元数据；`?download=true` 直接下载数据文件。需 `X-Api-Key`。"
    ),
    dependencies=[Depends(require_api_key)],
)
def get_export(
    export_id: str,
    download: bool = Query(default=False),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        meta = export_service.get_export(db, export_id)
    except export_service.ExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if download:
        return FileResponse(
            path=meta["_file_path"],
            filename=meta["file_name"],
            media_type=_MEDIA_TYPES.get(meta["format"], "application/octet-stream"),
        )
    meta.pop("_file_path", None)
    return meta
