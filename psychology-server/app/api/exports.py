"""科研导出 API。

- P0 会话导出（§12.5 Q-5/Q-6/Q-7）：manifest / 创建导出 / 查询下载；
- P1-A 科研导出：复用 ``scripts/research_export.py`` 口径，输出
  CSV/JSONL、raw/derived 分目录、数据字典、质量报告与含 sha256 的 manifest，
  批量打包为 zip。

鉴权统一为 `X-Api-Key`（P0-17）。导出物中的 `event_timestamp` 为**派生值**
（`anchor + relative_seconds`），一律标 `estimated`，且绝不回写记录 `quality`（AC-35）。
"""
import json
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession
from starlette.background import BackgroundTask

from app.core.security import require_api_key
from app.db.database import get_db
from app.schemas.export import ExportCreateRequest, ExportCreateResponse
from app.schemas.research_export import (
    DATA_TYPE_CHOICES,
    DEFAULT_DATA_TYPES,
    ExportFormat,
    ResearchExportRequest,
)
from app.services import export_service, research_export_service, session_service

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


# ---------------------------------------------------------------------------
# P1-A 科研导出（HTTP 封装，复用 scripts/research_export.py）
# ---------------------------------------------------------------------------


def _parse_data_types(raw: Optional[str]) -> List[str]:
    if raw is None or not raw.strip():
        return list(DEFAULT_DATA_TYPES)
    items = [item.strip() for item in raw.split(",") if item.strip()]
    invalid = [item for item in items if item not in DATA_TYPE_CHOICES]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"invalid data_types: {','.join(invalid)}; "
            f"allowed: {','.join(DATA_TYPE_CHOICES)}",
        )
    return items


def _run_research_export(**kwargs: Any) -> "research_export_service.ResearchExportArtifact":
    try:
        return research_export_service.create_research_export(**kwargs)
    except research_export_service.ResearchExportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _research_file_response(
    artifact: "research_export_service.ResearchExportArtifact",
) -> FileResponse:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers = {
        "X-Export-Manifest-Sha256": artifact.manifest_sha256,
        "X-Export-Sessions": str(len(artifact.session_ids)),
        "X-Export-Total-Counts": json.dumps(
            artifact.total_counts, ensure_ascii=False, separators=(",", ":")
        ),
        "X-Export-Missing-Tables": ",".join(artifact.missing_tables) or "none",
    }
    return FileResponse(
        path=artifact.zip_path,
        filename=f"research_export_{stamp}.zip",
        media_type="application/zip",
        headers=headers,
        background=BackgroundTask(artifact.cleanup),
    )


@router.post(
    "/research/exports",
    summary="创建科研数据导出（单/多会话，zip）",
    description=(
        "按会话号或时间范围导出科研数据，直接返回 zip（需 `X-Api-Key`）。\n\n"
        "- 内容与 `scripts/research_export.py` 一致：raw/derived 分目录、CSV 与/或 "
        "JSONL、`data_dictionary.md`、单会话与全局 `quality_report.json`、"
        "含每个文件 sha256 的 `manifest.json`；\n"
        "- `session_ids` 与 `export_all` 二选一；会话时间按 "
        "`coalesce(started_at, created_at)` 过滤；\n"
        "- 响应头附带 `X-Export-Manifest-Sha256`、`X-Export-Sessions`、"
        "`X-Export-Total-Counts`；\n"
        "- 只读打开数据库，不写库、不改 P0 表；默认不导出外部用户 ID 与禁字段。"
    ),
    dependencies=[Depends(require_api_key)],
)
def create_research_export(req: ResearchExportRequest) -> FileResponse:
    artifact = _run_research_export(
        session_ids=req.session_ids,
        export_all=req.export_all,
        from_utc=req.from_utc,
        to_utc=req.to_utc,
        from_seconds=req.from_seconds,
        to_seconds=req.to_seconds,
        data_types=req.data_types,
        fmt=req.format,
    )
    return _research_file_response(artifact)


@router.get(
    "/research/sessions/{session_id}/export",
    summary="导出单场会话科研数据（zip）",
    description=(
        "单会话导出，口径与 `POST /research/exports` 相同（需 `X-Api-Key`）。\n\n"
        "`data_types` 用逗号分隔，默认全部；会话不存在返回 404。"
    ),
    dependencies=[Depends(require_api_key)],
)
def export_research_session(
    session_id: str,
    format: ExportFormat = Query(default="both", description="csv/jsonl/both"),
    from_seconds: Optional[float] = Query(default=None),
    to_seconds: Optional[float] = Query(default=None),
    data_types: Optional[str] = Query(
        default=None, description="逗号分隔：timeline,emotion,results,snapshots,batches"
    ),
) -> FileResponse:
    if (
        from_seconds is not None
        and to_seconds is not None
        and from_seconds > to_seconds
    ):
        raise HTTPException(
            status_code=422, detail="from_seconds 不能大于 to_seconds"
        )
    artifact = _run_research_export(
        session_ids=[session_id],
        export_all=False,
        from_seconds=from_seconds,
        to_seconds=to_seconds,
        data_types=_parse_data_types(data_types),
        fmt=format,
    )
    return _research_file_response(artifact)
