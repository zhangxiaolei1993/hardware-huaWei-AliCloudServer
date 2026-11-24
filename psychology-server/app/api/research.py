"""科研版云端 API（第三阶段）。

所有接口统一使用 ``X-Api-Key`` 鉴权（``require_api_key``）。
账号与项目级权限本轮不做：``TODO(PERMISSION)`` 见各写接口，后续接入
主站凭证 / 科研账号时在此处补校验，不改变路径与响应结构。
"""
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Path as PathParam,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession

from app.core.security import client_ip, require_api_key
from app.db.database import get_db, utcnow
from app.schemas.research import (
    AssessmentItemsUpsertRequest,
    AssignParticipantRequest,
    ConversationCreate,
    ConversationMessagesUploadRequest,
    DownloadJobCreate,
    GroupCreate,
    GroupUpdate,
    LegacyImportRequest,
    LinkAssessmentRequest,
    ParticipantBatchSyncRequest,
    ParticipantCreate,
    ParticipantUpdate,
    ProjectCreate,
    ProjectUpdate,
)
from app.services import research_download_service, research_service
from app.services.research_service import ResearchError, to_utc

router = APIRouter(
    prefix="/research",
    tags=["research"],
    dependencies=[Depends(require_api_key)],
)


def _fail(exc: ResearchError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _participant_payload(db: DBSession, participant) -> dict:
    group_name = None
    if participant.group_id:
        try:
            group_name = research_service.get_group(db, participant.group_id).name
        except ResearchError:
            group_name = None
    return research_service.participant_dict(participant, group_name)


def _page(page: int, page_size: int, total: int) -> dict:
    return research_service._page_meta(page, page_size, total)


# ---------------------------------------------------------------------------
# 项目
# ---------------------------------------------------------------------------
@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    summary="创建科研项目",
)
def create_project(req: ProjectCreate, db: DBSession = Depends(get_db)) -> Any:
    # TODO(PERMISSION): 接入账号后校验调用者是否为项目创建者/管理员
    try:
        project = research_service.create_project(
            db, req.name, req.code, req.description
        )
    except ResearchError as exc:
        _fail(exc)
    return research_service.project_dict(project)


@router.get("/projects", summary="项目列表")
def list_projects(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    rows, total = research_service.list_projects(db, status_filter, page, page_size)
    return {
        **_page(page, page_size, total),
        "items": [research_service.project_dict(row) for row in rows],
    }


@router.get("/projects/{project_id}", summary="项目详情")
def get_project(
    project_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        project = research_service.get_project(db, project_id)
    except ResearchError as exc:
        _fail(exc)
    return research_service.project_dict(project)


@router.patch("/projects/{project_id}", summary="更新项目")
def update_project(
    req: ProjectUpdate,
    project_id: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        project = research_service.update_project(
            db, project_id, req.model_dump(exclude_unset=True)
        )
    except ResearchError as exc:
        _fail(exc)
    return research_service.project_dict(project)


@router.post("/projects/{project_id}/archive", summary="归档项目")
def archive_project(
    project_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        project = research_service.set_project_status(db, project_id, "archived")
    except ResearchError as exc:
        _fail(exc)
    return research_service.project_dict(project)


@router.post("/projects/{project_id}/restore", summary="恢复项目")
def restore_project(
    project_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        project = research_service.set_project_status(db, project_id, "active")
    except ResearchError as exc:
        _fail(exc)
    return research_service.project_dict(project)


# ---------------------------------------------------------------------------
# 实验组（用户自定义组名）
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/groups", summary="项目下的实验组")
def list_groups(
    project_id: str = PathParam(...),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        groups = research_service.list_groups(db, project_id, status_filter)
    except ResearchError as exc:
        _fail(exc)
    return {"items": [research_service.group_dict(g) for g in groups]}


@router.post(
    "/projects/{project_id}/groups",
    status_code=status.HTTP_201_CREATED,
    summary="创建实验组",
)
def create_group(
    req: GroupCreate,
    project_id: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        # TODO(PERMISSION): 校验调用者是否有该项目写权限
        group = research_service.create_group(
            db, project_id, req.name, req.group_type, req.sort_order
        )
    except ResearchError as exc:
        _fail(exc)
    return research_service.group_dict(group)


@router.patch("/groups/{group_id}", summary="更新实验组")
def update_group(
    req: GroupUpdate,
    group_id: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        group = research_service.update_group(
            db, group_id, req.model_dump(exclude_unset=True)
        )
    except ResearchError as exc:
        _fail(exc)
    return research_service.group_dict(group)


@router.post("/groups/{group_id}/archive", summary="归档实验组")
def archive_group(
    group_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        group = research_service.set_group_status(db, group_id, "archived")
    except ResearchError as exc:
        _fail(exc)
    return research_service.group_dict(group)


@router.post("/groups/{group_id}/restore", summary="恢复实验组")
def restore_group(
    group_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        group = research_service.set_group_status(db, group_id, "active")
    except ResearchError as exc:
        _fail(exc)
    return research_service.group_dict(group)


# ---------------------------------------------------------------------------
# 被试
# ---------------------------------------------------------------------------
@router.get("/participants", summary="被试列表（按项目/组/编号）")
def list_participants(
    project_id: str = Query(..., min_length=1, max_length=40),
    group_id: Optional[str] = Query(None),
    group_name: Optional[str] = Query(None, description="用户自定义组名，精确匹配"),
    participant_code: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    updated_since: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        rows, total = research_service.list_participants(
            db,
            project_id,
            group_id,
            group_name,
            participant_code,
            status_filter,
            updated_since,
            page,
            page_size,
        )
    except ResearchError as exc:
        _fail(exc)
    return {
        **_page(page, page_size, total),
        "items": research_service.participants_dict(db, rows),
    }


@router.post(
    "/participants",
    status_code=status.HTTP_201_CREATED,
    summary="创建被试（project_id + participant_code 幂等）",
)
def create_participant(
    req: ParticipantCreate, db: DBSession = Depends(get_db)
) -> Any:
    # TODO(PERMISSION): 校验调用者是否有该项目写权限
    try:
        participant, created = research_service.create_participant(
            db, req.model_dump()
        )
    except ResearchError as exc:
        _fail(exc)
    payload = _participant_payload(db, participant)
    payload["created"] = created
    return payload


@router.post("/participants/batch-sync", summary="批量同步被试（幂等）")
def batch_sync_participants(
    req: ParticipantBatchSyncRequest, db: DBSession = Depends(get_db)
) -> Any:
    try:
        return research_service.batch_sync_participants(
            db, req.project_id, [item.model_dump() for item in req.items]
        )
    except ResearchError as exc:
        _fail(exc)


@router.get("/participants/{participant_id}", summary="被试详情")
def get_participant(
    participant_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        participant = research_service.get_participant(db, participant_id)
    except ResearchError as exc:
        _fail(exc)
    return _participant_payload(db, participant)


@router.patch("/participants/{participant_id}", summary="更新被试（乐观锁）")
def update_participant(
    req: ParticipantUpdate,
    participant_id: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        # TODO(PERMISSION): 校验调用者是否有该项目写权限
        participant = research_service.update_participant(
            db, participant_id, req.model_dump(exclude_unset=False)
        )
    except ResearchError as exc:
        _fail(exc)
    return _participant_payload(db, participant)


@router.post("/participants/{participant_id}/archive", summary="归档被试")
def archive_participant(
    participant_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        participant = research_service.set_participant_archived(db, participant_id, True)
    except ResearchError as exc:
        _fail(exc)
    return _participant_payload(db, participant)


@router.post("/participants/{participant_id}/restore", summary="恢复被试")
def restore_participant(
    participant_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        participant = research_service.set_participant_archived(
            db, participant_id, False
        )
    except ResearchError as exc:
        _fail(exc)
    return _participant_payload(db, participant)


@router.get(
    "/participants/{participant_id}/assessments",
    summary="被试测评列表（可按量表取最新一次）",
)
def participant_assessments(
    participant_id: str = PathParam(...),
    mode: str = Query("all", pattern="^(all|latest_per_scale)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        research_service.get_participant(db, participant_id)
        rows, total = research_service.list_assessments(
            db,
            None,
            participant_id,
            None,
            None,
            None,
            mode,
            None,
            None,
            page,
            page_size,
        )
    except ResearchError as exc:
        _fail(exc)
    return {
        **_page(page, page_size, total),
        "items": research_service.assessments_payload(db, rows),
    }


@router.get("/participants/{participant_id}/sessions", summary="被试会话列表")
def participant_sessions(
    participant_id: str = PathParam(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        research_service.get_participant(db, participant_id)
        rows, total = research_service.list_sessions(
            db, None, participant_id, None, page, page_size
        )
    except ResearchError as exc:
        _fail(exc)
    return {
        **_page(page, page_size, total),
        "items": [research_service.session_dict(row) for row in rows],
    }


@router.get(
    "/participants/{participant_id}/conversations", summary="被试对话列表"
)
def participant_conversations(
    participant_id: str = PathParam(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        research_service.get_participant(db, participant_id)
        rows, total = research_service.list_conversations(
            db, None, participant_id, None, None, page, page_size
        )
    except ResearchError as exc:
        _fail(exc)
    return {
        **_page(page, page_size, total),
        "items": [research_service.conversation_dict(row) for row in rows],
    }


# ---------------------------------------------------------------------------
# 会话绑定 / 会话视角
# ---------------------------------------------------------------------------
@router.post(
    "/experiment-sessions/{session_id}/assign-participant",
    summary="把会话挂到被试（S1 → participant）",
)
def assign_participant(
    req: AssignParticipantRequest,
    session_id: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        # TODO(PERMISSION): 校验调用者是否有该项目写权限
        return research_service.assign_participant(
            db, session_id, req.participant_id, req.backfill_assessments
        )
    except ResearchError as exc:
        _fail(exc)


@router.get("/experiment-sessions", summary="科研会话列表")
def list_experiment_sessions(
    project_id: Optional[str] = Query(None),
    participant_id: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    rows, total = research_service.list_sessions(
        db, project_id, participant_id, group_id, page, page_size
    )
    return {
        **_page(page, page_size, total),
        "items": [research_service.session_dict(row) for row in rows],
    }


@router.get("/experiment-sessions/{session_id}", summary="会话详情（含科研关联）")
def get_experiment_session(
    session_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        session = research_service.get_session_or_error(db, session_id)
    except ResearchError as exc:
        _fail(exc)
    return research_service.session_dict(session)


@router.get(
    "/experiment-sessions/{session_id}/results",
    summary="按会话读取测评结果与逐题作答",
)
def session_results(
    session_id: str = PathParam(...),
    include_items: bool = Query(True),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        assessments = research_service.session_assessments(db, session_id)
    except ResearchError as exc:
        _fail(exc)
    if include_items:
        for assessment in assessments:
            items = research_service.read_assessment_items(
                db, assessment["assessment_id"], None
            )
            assessment["items"] = items["items"]
    return {"session_id": session_id, "items": assessments}


# ---------------------------------------------------------------------------
# 测评
# ---------------------------------------------------------------------------
@router.get("/assessments", summary="测评列表")
def list_assessments(
    project_id: Optional[str] = Query(None),
    participant_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    questionnaire_code: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    mode: str = Query("all", pattern="^(all|latest_per_scale)$"),
    date_from: Optional[datetime] = Query(None, alias="from"),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        rows, total = research_service.list_assessments(
            db,
            project_id,
            participant_id,
            session_id,
            questionnaire_code,
            status_filter,
            mode,
            date_from,
            date_to,
            page,
            page_size,
        )
    except ResearchError as exc:
        _fail(exc)
    return {
        **_page(page, page_size, total),
        "items": research_service.assessments_payload(db, rows),
    }


@router.get("/assessments/{assessment_id}", summary="测评详情")
def get_assessment(
    assessment_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        return research_service.assessment_detail(db, assessment_id)
    except ResearchError as exc:
        _fail(exc)


@router.get("/assessments/{assessment_id}/items", summary="读取逐题作答")
def get_assessment_items(
    assessment_id: str = PathParam(...),
    result_version: Optional[int] = Query(None, ge=1),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        return research_service.read_assessment_items(
            db, assessment_id, result_version
        )
    except ResearchError as exc:
        _fail(exc)


@router.put(
    "/experiment-sessions/{session_id}/assessments/{questionnaire_code}/items",
    summary="上传/更新逐题作答",
)
def upsert_assessment_items(
    req: AssessmentItemsUpsertRequest,
    session_id: str = PathParam(...),
    questionnaire_code: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        # TODO(PERMISSION): 校验调用者是否有该测评所属项目的写权限
        return research_service.upsert_assessment_items(
            db,
            session_id,
            questionnaire_code,
            req.result_version,
            [item.model_dump() for item in req.items],
        )
    except ResearchError as exc:
        _fail(exc)


# ---------------------------------------------------------------------------
# AI 对话
# ---------------------------------------------------------------------------
@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    summary="创建对话",
)
def create_conversation(
    req: ConversationCreate, db: DBSession = Depends(get_db)
) -> Any:
    try:
        conversation, created = research_service.create_conversation(
            db, req.model_dump()
        )
    except ResearchError as exc:
        _fail(exc)
    payload = research_service.conversation_dict(conversation)
    payload["created"] = created
    return payload


@router.get("/conversations", summary="对话列表")
def list_conversations(
    project_id: Optional[str] = Query(None),
    participant_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    assessment_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    rows, total = research_service.list_conversations(
        db,
        project_id,
        participant_id,
        session_id,
        assessment_id,
        page,
        page_size,
    )
    return {
        **_page(page, page_size, total),
        "items": [research_service.conversation_dict(row) for row in rows],
    }


@router.get("/conversations/{conversation_id}", summary="对话详情")
def get_conversation(
    conversation_id: str = PathParam(...),
    include_messages: bool = Query(False),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        conversation = research_service.get_conversation(db, conversation_id)
    except ResearchError as exc:
        _fail(exc)
    messages = (
        research_service.read_messages(db, conversation_id, 0)
        if include_messages
        else None
    )
    return research_service.conversation_dict(
        conversation, include_messages=include_messages, messages=messages
    )


@router.get(
    "/conversations/{conversation_id}/messages", summary="按序号增量读取消息"
)
def get_messages(
    conversation_id: str = PathParam(...),
    after_seq: int = Query(0, ge=0),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        rows = research_service.read_messages(db, conversation_id, after_seq)
    except ResearchError as exc:
        _fail(exc)
    return {
        "conversation_id": conversation_id,
        "after_seq": after_seq,
        "total": len(rows),
        "items": [research_service._message_dict(row) for row in rows],
    }


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    summary="批量上传对话消息（按 seq 幂等）",
)
def upload_messages(
    req: ConversationMessagesUploadRequest,
    conversation_id: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        # TODO(PERMISSION): 校验调用者是否有该对话所属项目的写权限
        return research_service.upload_messages(
            db,
            conversation_id,
            [
                item.model_dump()
                for item in req.messages
            ],
        )
    except ResearchError as exc:
        _fail(exc)


@router.post(
    "/conversations/{conversation_id}/link-assessment",
    summary="把对话关联到某个测评",
)
def link_conversation_assessment(
    req: LinkAssessmentRequest,
    conversation_id: str = PathParam(...),
    db: DBSession = Depends(get_db),
) -> Any:
    try:
        conversation = research_service.link_assessment(
            db, conversation_id, req.assessment_id
        )
    except ResearchError as exc:
        _fail(exc)
    return research_service.conversation_dict(conversation)


# ---------------------------------------------------------------------------
# 下载中心（异步任务）
# ---------------------------------------------------------------------------
@router.post(
    "/download-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="创建下载任务（异步打包）",
)
def create_download_job(
    req: DownloadJobCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: DBSession = Depends(get_db),
) -> Any:
    assessment_scope = req.assessment_scope.model_dump()
    assessment_scope["from_"] = to_utc(assessment_scope.get("from_"))
    assessment_scope["to"] = to_utc(assessment_scope.get("to"))
    filters = {
        "project_id": req.project_id,
        "scope": req.scope.model_dump(),
        "assessment_scope": assessment_scope,
        "data_types": req.data_types,
        "format": req.format,
        "include_archived": req.include_archived,
    }
    try:
        # TODO(PERMISSION): 校验调用者是否有该项目下载权限
        job = research_download_service.create_job(
            db, req.project_id, filters, requested_ip=client_ip(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    background_tasks.add_task(research_download_service.run_job, job.export_id)
    return research_download_service.job_dict(job)


@router.get("/download-jobs", summary="下载任务列表")
def list_download_jobs(
    project_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DBSession = Depends(get_db),
) -> Any:
    from app.models.research import ResearchExportJob

    query = db.query(ResearchExportJob)
    if project_id:
        query = query.filter(ResearchExportJob.project_id == project_id)
    if status_filter:
        query = query.filter(ResearchExportJob.status == status_filter)
    rows, total = research_service._paginate(
        query.order_by(ResearchExportJob.id.desc()), page, page_size
    )
    return {
        **_page(page, page_size, total),
        "items": [research_download_service.job_dict(row) for row in rows],
    }


@router.get("/download-jobs/{export_id}", summary="下载任务状态")
def get_download_job(
    export_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    from app.models.research import ResearchExportJob

    job = (
        db.query(ResearchExportJob)
        .filter(ResearchExportJob.export_id == export_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail=f"export job {export_id} not found")
    return research_download_service.job_dict(job)


@router.get("/download-jobs/{export_id}/download", summary="下载数据包")
def download_job_file(
    export_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        path = research_download_service.download_file(db, export_id)
    except ValueError as exc:
        message = str(exc)
        code = 404 if "not found" in message else 409
        if "EXPIRED" in message:
            code = 410
        if "MISSING" in message:
            code = 410
        raise HTTPException(status_code=code, detail=message) from exc
    return FileResponse(
        path=str(path),
        media_type="application/zip",
        filename=Path(path).name,
    )


@router.delete("/download-jobs/{export_id}", summary="取消任务 / 删除产物")
def delete_download_job(
    export_id: str = PathParam(...), db: DBSession = Depends(get_db)
) -> Any:
    try:
        job = research_download_service.cancel_or_delete(db, export_id)
    except ValueError as exc:
        message = str(exc)
        code = 404 if "not found" in message else 409
        raise HTTPException(status_code=code, detail=message) from exc
    return research_download_service.job_dict(job)


# ---------------------------------------------------------------------------
# 历史迁移（仅运维用途）
# ---------------------------------------------------------------------------
@router.post(
    "/import/legacy",
    summary="历史离线数据迁移（默认 dry_run）",
    description=(
        "仅运维用途：把科研版本机历史被试与离线测评导入云端。\n\n"
        "- `dry_run=true`（默认）只返回台账，不写库；\n"
        "- 无 S1 的历史测评会生成 `data_origin=legacy_import` 的合成会话；\n"
        "- 同一导入内容重复提交按幂等键跳过。\n\n"
        "本接口不删除、不修改本机数据。"
    ),
)
def import_legacy(
    req: LegacyImportRequest, db: DBSession = Depends(get_db)
) -> Any:
    try:
        # TODO(PERMISSION): 迁移接口仅运维使用；后续接入账号后校验管理员身份
        return research_service.legacy_import(
            db, req.project_id, req.model_dump()
        )
    except ResearchError as exc:
        _fail(exc)
