"""科研域服务：项目 / 实验组 / 被试 / 会话绑定 / 测评 / 逐题作答 / AI 对话。

设计口径（第三轮已拍板）：
- 不做账号与权限：接口统一由 ``X-Api-Key`` 鉴权；项目级权限校验留空，
  详见 ``TODO(PERMISSION)``。
- 姓名、电话允许上云。
- 一个项目可有多个实验组，组名为用户自定义；按组筛选时按 ``group_id``
  或组名精确匹配。
- 逐题作答单独存 ``research_assessment_items``。
- AI 对话正文存本服务。
- 无 S1 的离线历史可生成 ``data_origin='legacy_import'`` 的合成会话。
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.db.database import utcnow
from app.models.external_result_snapshot import ExternalResultSnapshot
from app.models.research import (
    ResearchAssessment,
    ResearchAssessmentItem,
    ResearchConversation,
    ResearchConversationMessage,
    ResearchGroup,
    ResearchParticipant,
    ResearchProject,
)
from app.models.session import SessionModel

BJ_TZ = timezone(timedelta(hours=8))
MAX_PAGE_SIZE = 200


class ResearchError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def to_utc(value: Optional[datetime]) -> Optional[datetime]:
    """请求时间：带时区按原值换算；无时区按 ``+08:00`` 解释。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=BJ_TZ)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def iso_bj(value: Optional[datetime]) -> Optional[str]:
    """响应时间统一 ``+08:00``；数据库存 naive UTC。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BJ_TZ).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paginate(query, page: int, page_size: int) -> Tuple[List[Any], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    total = query.order_by(None).count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def _page_meta(page: int, page_size: int, total: int) -> Dict[str, int]:
    return {
        "page": max(page, 1),
        "page_size": min(max(page_size, 1), MAX_PAGE_SIZE),
        "total": total,
    }


# ---------------------------------------------------------------------------
# 项目
# ---------------------------------------------------------------------------
def get_project(db: DBSession, project_id: str) -> ResearchProject:
    project = (
        db.query(ResearchProject)
        .filter(ResearchProject.project_id == project_id)
        .first()
    )
    if project is None:
        raise ResearchError(404, f"project {project_id} not found")
    return project


def project_dict(project: ResearchProject) -> Dict[str, Any]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "code": project.code,
        "description": project.description,
        "status": project.status,
        "created_at": iso_bj(project.created_at),
        "updated_at": iso_bj(project.updated_at),
    }


def create_project(
    db: DBSession, name: str, code: Optional[str], description: Optional[str]
) -> ResearchProject:
    if code:
        existing = (
            db.query(ResearchProject).filter(ResearchProject.code == code).first()
        )
        if existing is not None:
            raise ResearchError(409, f"project code {code} already exists")
    now = utcnow()
    project = ResearchProject(
        project_id=new_id("prj"),
        name=name,
        code=code,
        description=description,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def list_projects(
    db: DBSession, status: Optional[str], page: int, page_size: int
) -> Tuple[List[ResearchProject], int]:
    query = db.query(ResearchProject)
    if status:
        query = query.filter(ResearchProject.status == status)
    query = query.order_by(ResearchProject.id.asc())
    return _paginate(query, page, page_size)


def update_project(db: DBSession, project_id: str, data: Dict[str, Any]) -> ResearchProject:
    project = get_project(db, project_id)
    if data.get("code"):
        conflict = (
            db.query(ResearchProject)
            .filter(
                ResearchProject.code == data["code"],
                ResearchProject.project_id != project_id,
            )
            .first()
        )
        if conflict is not None:
            raise ResearchError(409, f"project code {data['code']} already exists")
    for field in ("name", "code", "description"):
        if field in data and data[field] is not None:
            setattr(project, field, data[field])
    project.updated_at = utcnow()
    db.commit()
    db.refresh(project)
    return project


def set_project_status(
    db: DBSession, project_id: str, status: str
) -> ResearchProject:
    project = get_project(db, project_id)
    project.status = status
    project.updated_at = utcnow()
    db.commit()
    db.refresh(project)
    return project


# ---------------------------------------------------------------------------
# 实验组
# ---------------------------------------------------------------------------
def get_group(db: DBSession, group_id: str) -> ResearchGroup:
    group = db.query(ResearchGroup).filter(ResearchGroup.group_id == group_id).first()
    if group is None:
        raise ResearchError(404, f"group {group_id} not found")
    return group


def group_dict(group: ResearchGroup) -> Dict[str, Any]:
    return {
        "group_id": group.group_id,
        "project_id": group.project_id,
        "name": group.name,
        "group_type": group.group_type,
        "sort_order": group.sort_order,
        "status": group.status,
        "created_at": iso_bj(group.created_at),
        "updated_at": iso_bj(group.updated_at),
    }


def create_group(
    db: DBSession,
    project_id: str,
    name: str,
    group_type: str,
    sort_order: int,
) -> ResearchGroup:
    get_project(db, project_id)
    existing = (
        db.query(ResearchGroup)
        .filter(ResearchGroup.project_id == project_id, ResearchGroup.name == name)
        .first()
    )
    if existing is not None:
        raise ResearchError(
            409, f"group name {name} already exists in project {project_id}"
        )
    now = utcnow()
    group = ResearchGroup(
        group_id=new_id("grp"),
        project_id=project_id,
        name=name,
        group_type=group_type,
        sort_order=sort_order,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def list_groups(
    db: DBSession, project_id: str, status: Optional[str]
) -> List[ResearchGroup]:
    get_project(db, project_id)
    query = db.query(ResearchGroup).filter(ResearchGroup.project_id == project_id)
    if status:
        query = query.filter(ResearchGroup.status == status)
    return query.order_by(ResearchGroup.sort_order.asc(), ResearchGroup.id.asc()).all()


def update_group(db: DBSession, group_id: str, data: Dict[str, Any]) -> ResearchGroup:
    group = get_group(db, group_id)
    if data.get("name") and data["name"] != group.name:
        conflict = (
            db.query(ResearchGroup)
            .filter(
                ResearchGroup.project_id == group.project_id,
                ResearchGroup.name == data["name"],
                ResearchGroup.group_id != group_id,
            )
            .first()
        )
        if conflict is not None:
            raise ResearchError(
                409, f"group name {data['name']} already exists in project {group.project_id}"
            )
    for field in ("name", "group_type", "sort_order"):
        if field in data and data[field] is not None:
            setattr(group, field, data[field])
    group.updated_at = utcnow()
    db.commit()
    db.refresh(group)
    return group


def set_group_status(db: DBSession, group_id: str, status: str) -> ResearchGroup:
    group = get_group(db, group_id)
    group.status = status
    group.updated_at = utcnow()
    db.commit()
    db.refresh(group)
    return group


# ---------------------------------------------------------------------------
# 被试
# ---------------------------------------------------------------------------
def get_participant(db: DBSession, participant_id: str) -> ResearchParticipant:
    participant = (
        db.query(ResearchParticipant)
        .filter(ResearchParticipant.participant_id == participant_id)
        .first()
    )
    if participant is None:
        raise ResearchError(404, f"participant {participant_id} not found")
    return participant


def _group_names(db: DBSession, participants: List[ResearchParticipant]) -> Dict[str, str]:
    ids = {p.group_id for p in participants if p.group_id}
    if not ids:
        return {}
    rows = db.query(ResearchGroup).filter(ResearchGroup.group_id.in_(ids)).all()
    return {g.group_id: g.name for g in rows}


def participant_dict(
    participant: ResearchParticipant, group_name: Optional[str] = None
) -> Dict[str, Any]:
    return {
        "participant_id": participant.participant_id,
        "project_id": participant.project_id,
        "group_id": participant.group_id,
        "group_name": group_name,
        "participant_code": participant.participant_code,
        "name": participant.name,
        "phone": participant.phone,
        "gender": participant.gender,
        "age": participant.age,
        "school": participant.school,
        "grade": participant.grade,
        "status": participant.status,
        "source_local_id": participant.source_local_id,
        "row_version": participant.row_version,
        "archived_at": iso_bj(participant.archived_at),
        "created_at": iso_bj(participant.created_at),
        "updated_at": iso_bj(participant.updated_at),
    }


def participants_dict(db: DBSession, participants: List[ResearchParticipant]) -> List[Dict[str, Any]]:
    names = _group_names(db, participants)
    return [participant_dict(p, names.get(p.group_id or "")) for p in participants]


PARTICIPANT_FIELDS = (
    "name",
    "phone",
    "gender",
    "age",
    "school",
    "grade",
    "group_id",
)


def _validate_group(db: DBSession, project_id: str, group_id: Optional[str]) -> Optional[ResearchGroup]:
    if not group_id:
        return None
    group = get_group(db, group_id)
    if group.project_id != project_id:
        raise ResearchError(422, "GROUP_PROJECT_MISMATCH: group belongs to another project")
    return group


def create_participant(db: DBSession, data: Dict[str, Any]) -> Tuple[ResearchParticipant, bool]:
    """按 ``project_id + participant_code`` 幂等；重复请求返回既有记录。"""
    project = get_project(db, data["project_id"])
    if project.status != "active":
        raise ResearchError(409, f"project {project.project_id} is archived")
    _validate_group(db, data["project_id"], data.get("group_id"))
    existing = (
        db.query(ResearchParticipant)
        .filter(
            ResearchParticipant.project_id == data["project_id"],
            ResearchParticipant.participant_code == data["participant_code"],
        )
        .first()
    )
    if existing is not None:
        return existing, False
    if data.get("source_local_id"):
        local_conflict = (
            db.query(ResearchParticipant)
            .filter(
                ResearchParticipant.project_id == data["project_id"],
                ResearchParticipant.source_local_id == data["source_local_id"],
            )
            .first()
        )
        if local_conflict is not None:
            return local_conflict, False
    now = utcnow()
    participant = ResearchParticipant(
        participant_id=new_id("pt"),
        project_id=data["project_id"],
        participant_code=data["participant_code"],
        group_id=data.get("group_id"),
        name=data.get("name"),
        phone=data.get("phone"),
        gender=data.get("gender"),
        age=data.get("age"),
        school=data.get("school"),
        grade=data.get("grade"),
        status="active",
        source_local_id=data.get("source_local_id"),
        client_request_id=data.get("client_request_id"),
        row_version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(participant)
    try:
        db.commit()
    except Exception:
        db.rollback()
        existing = (
            db.query(ResearchParticipant)
            .filter(
                ResearchParticipant.project_id == data["project_id"],
                ResearchParticipant.participant_code == data["participant_code"],
            )
            .first()
        )
        if existing is not None:
            return existing, False
        raise
    db.refresh(participant)
    return participant, True


def update_participant(
    db: DBSession, participant_id: str, data: Dict[str, Any]
) -> ResearchParticipant:
    participant = get_participant(db, participant_id)
    expected = data["row_version"]
    if expected != participant.row_version:
        raise ResearchError(
            409,
            "ROW_VERSION_CONFLICT: expected "
            f"{participant.row_version}, got {expected}",
        )
    if data.get("participant_code") and data["participant_code"] != participant.participant_code:
        conflict = (
            db.query(ResearchParticipant)
            .filter(
                ResearchParticipant.project_id == participant.project_id,
                ResearchParticipant.participant_code == data["participant_code"],
                ResearchParticipant.participant_id != participant_id,
            )
            .first()
        )
        if conflict is not None:
            raise ResearchError(
                409, f"participant_code {data['participant_code']} already exists"
            )
        participant.participant_code = data["participant_code"]
    if "group_id" in data:
        if data["group_id"]:
            _validate_group(db, participant.project_id, data["group_id"])
        participant.group_id = data["group_id"]
    for field in PARTICIPANT_FIELDS:
        if field == "group_id":
            continue
        if field in data and data[field] is not None:
            setattr(participant, field, data[field])
    if data.get("status") == "archived" and participant.status != "archived":
        participant.status = "archived"
        participant.archived_at = utcnow()
    elif data.get("status") == "active" and participant.status != "active":
        participant.status = "active"
        participant.archived_at = None
    participant.row_version += 1
    participant.updated_at = utcnow()
    db.commit()
    db.refresh(participant)
    return participant


def set_participant_archived(
    db: DBSession, participant_id: str, archived: bool
) -> ResearchParticipant:
    participant = get_participant(db, participant_id)
    participant.status = "archived" if archived else "active"
    participant.archived_at = utcnow() if archived else None
    participant.row_version += 1
    participant.updated_at = utcnow()
    db.commit()
    db.refresh(participant)
    return participant


def list_participants(
    db: DBSession,
    project_id: str,
    group_id: Optional[str],
    group_name: Optional[str],
    participant_code: Optional[str],
    status: Optional[str],
    updated_since: Optional[datetime],
    page: int,
    page_size: int,
) -> Tuple[List[ResearchParticipant], int]:
    get_project(db, project_id)
    query = db.query(ResearchParticipant).filter(
        ResearchParticipant.project_id == project_id
    )
    if group_id:
        query = query.filter(ResearchParticipant.group_id == group_id)
    elif group_name:
        # 用户自定义组名，精确匹配
        group = (
            db.query(ResearchGroup)
            .filter(
                ResearchGroup.project_id == project_id,
                ResearchGroup.name == group_name,
            )
            .first()
        )
        if group is None:
            return [], 0
        query = query.filter(ResearchParticipant.group_id == group.group_id)
    if participant_code:
        query = query.filter(
            ResearchParticipant.participant_code == participant_code
        )
    if status:
        query = query.filter(ResearchParticipant.status == status)
    if updated_since is not None:
        query = query.filter(
            ResearchParticipant.updated_at >= to_utc(updated_since)
        )
    query = query.order_by(ResearchParticipant.id.asc())
    return _paginate(query, page, page_size)


def batch_sync_participants(
    db: DBSession, project_id: str, items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    get_project(db, project_id)
    created = 0
    updated = 0
    skipped = 0
    results: List[Dict[str, Any]] = []
    for item in items:
        code = item["participant_code"]
        existing = (
            db.query(ResearchParticipant)
            .filter(
                ResearchParticipant.project_id == project_id,
                ResearchParticipant.participant_code == code,
            )
            .first()
        )
        if item.get("deleted"):
            if existing is None:
                skipped += 1
                results.append({"participant_code": code, "action": "skipped"})
                continue
            existing.status = "archived"
            existing.archived_at = utcnow()
            existing.row_version += 1
            existing.updated_at = utcnow()
            db.commit()
            updated += 1
            results.append(
                {
                    "participant_code": code,
                    "participant_id": existing.participant_id,
                    "action": "archived",
                    "row_version": existing.row_version,
                }
            )
            continue
        if existing is None:
            payload = dict(item)
            payload["project_id"] = project_id
            participant, was_created = create_participant(db, payload)
            created += 1 if was_created else 0
            if not was_created:
                skipped += 1
            results.append(
                {
                    "participant_code": code,
                    "participant_id": participant.participant_id,
                    "action": "created" if was_created else "skipped",
                    "row_version": participant.row_version,
                }
            )
            continue
        incoming_version = item.get("row_version")
        if incoming_version is not None and incoming_version < existing.row_version:
            skipped += 1
            results.append(
                {
                    "participant_code": code,
                    "participant_id": existing.participant_id,
                    "action": "skipped_stale",
                    "row_version": existing.row_version,
                }
            )
            continue
        if item.get("group_id"):
            _validate_group(db, project_id, item["group_id"])
        changed = False
        for field in PARTICIPANT_FIELDS:
            if item.get(field) is not None and item[field] != getattr(existing, field):
                changed = True
                break
        if item.get("source_local_id") and not existing.source_local_id:
            changed = True
        if not changed:
            skipped += 1
            results.append(
                {
                    "participant_code": code,
                    "participant_id": existing.participant_id,
                    "action": "unchanged",
                    "row_version": existing.row_version,
                }
            )
            continue
        for field in PARTICIPANT_FIELDS:
            if item.get(field) is not None:
                setattr(existing, field, item[field])
        if item.get("source_local_id") and not existing.source_local_id:
            existing.source_local_id = item["source_local_id"]
        existing.row_version += 1
        existing.updated_at = utcnow()
        db.commit()
        updated += 1
        results.append(
            {
                "participant_code": code,
                "participant_id": existing.participant_id,
                "action": "updated",
                "row_version": existing.row_version,
            }
        )
    return {
        "project_id": project_id,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "results": results,
    }


# ---------------------------------------------------------------------------
# 会话绑定被试 / 列表
# ---------------------------------------------------------------------------
def get_session_or_error(db: DBSession, session_id: str) -> SessionModel:
    session = (
        db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
    )
    if session is None:
        raise ResearchError(404, f"session {session_id} not found")
    return session


def session_dict(session: SessionModel) -> Dict[str, Any]:
    return {
        "session_id": session.session_id,
        "device_id": session.device_id,
        "participant_id": session.participant_id,
        "project_id": session.project_id,
        "group_id": session.group_id,
        "data_origin": session.data_origin,
        "session_type": session.session_type,
        "source_system": session.source_system,
        "external_session_id": session.external_session_id,
        "status": session.status,
        "ended_reason": session.ended_reason,
        "started_at": iso_bj(session.started_at),
        "ended_at": iso_bj(session.ended_at),
        "created_at": iso_bj(session.created_at),
        "updated_at": iso_bj(session.updated_at),
    }


def assign_participant(
    db: DBSession, session_id: str, participant_id: str, backfill: bool = True
) -> Dict[str, Any]:
    session = get_session_or_error(db, session_id)
    participant = get_participant(db, participant_id)
    if participant.status != "active":
        raise ResearchError(409, "participant is archived")
    session.participant_id = participant.participant_id
    session.project_id = participant.project_id
    session.group_id = participant.group_id
    session.updated_at = utcnow()
    backfilled = 0
    if backfill:
        snapshots = (
            db.query(ExternalResultSnapshot)
            .filter(ExternalResultSnapshot.session_id == session.session_id)
            .all()
        )
        for snapshot in snapshots:
            if ensure_assessment_for_snapshot(db, session, snapshot) is not None:
                backfilled += 1
    db.commit()
    db.refresh(session)
    result = session_dict(session)
    result["backfilled_assessments"] = backfilled
    return result


def list_sessions(
    db: DBSession,
    project_id: Optional[str],
    participant_id: Optional[str],
    group_id: Optional[str],
    page: int,
    page_size: int,
) -> Tuple[List[SessionModel], int]:
    query = db.query(SessionModel)
    if project_id:
        query = query.filter(SessionModel.project_id == project_id)
    if participant_id:
        query = query.filter(SessionModel.participant_id == participant_id)
    if group_id:
        query = query.filter(SessionModel.group_id == group_id)
    query = query.order_by(SessionModel.id.desc())
    return _paginate(query, page, page_size)


# ---------------------------------------------------------------------------
# 测评
# ---------------------------------------------------------------------------
def ensure_assessment_for_snapshot(
    db: DBSession, session: SessionModel, snapshot: ExternalResultSnapshot
) -> Optional[ResearchAssessment]:
    """结果快照落库后，保证「会话 × 量表」存在唯一测评记录。

    会话未绑定被试时返回 None（保持旧链路行为不变）。
    """
    if not session.participant_id or not session.project_id:
        return None
    now = utcnow()
    assessment = (
        db.query(ResearchAssessment)
        .filter(
            ResearchAssessment.session_id == session.session_id,
            ResearchAssessment.questionnaire_code == snapshot.questionnaire_code,
        )
        .first()
    )
    if assessment is None:
        assessment = ResearchAssessment(
            assessment_id=new_id("asm"),
            project_id=session.project_id,
            participant_id=session.participant_id,
            group_id=session.group_id,
            session_id=session.session_id,
            questionnaire_code=snapshot.questionnaire_code,
            questionnaire_version=snapshot.questionnaire_version,
            scale_name=snapshot.scale_name,
            assessment_type="scale",
            assessment_status=snapshot.status,
            current_snapshot_id=snapshot.snapshot_id,
            result_version=snapshot.result_version,
            total_score=snapshot.total_score,
            reported_at=snapshot.received_at,
            source="live",
            idempotency_key=f"asm:{session.session_id}:{snapshot.questionnaire_code}",
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(assessment)
    else:
        current_version = assessment.result_version or 0
        if snapshot.result_version >= current_version:
            assessment.current_snapshot_id = snapshot.snapshot_id
            assessment.result_version = snapshot.result_version
            assessment.total_score = snapshot.total_score
            assessment.assessment_status = snapshot.status
            assessment.reported_at = snapshot.received_at
            assessment.questionnaire_version = (
                snapshot.questionnaire_version or assessment.questionnaire_version
            )
            assessment.scale_name = snapshot.scale_name or assessment.scale_name
            assessment.updated_at = now
    snapshot.assessment_id = assessment.assessment_id
    snapshot.participant_id = session.participant_id
    db.flush()
    return assessment


def _assessment_dict(
    assessment: ResearchAssessment,
    snapshot: Optional[ExternalResultSnapshot],
    participant: Optional[ResearchParticipant],
    group_name: Optional[str],
    item_count: Optional[int] = None,
) -> Dict[str, Any]:
    payload = {
        "assessment_id": assessment.assessment_id,
        "project_id": assessment.project_id,
        "participant_id": assessment.participant_id,
        "participant_code": participant.participant_code if participant else None,
        "participant_name": participant.name if participant else None,
        "group_id": assessment.group_id,
        "group_name": group_name,
        "session_id": assessment.session_id,
        "questionnaire_code": assessment.questionnaire_code,
        "questionnaire_version": assessment.questionnaire_version,
        "scale_name": assessment.scale_name,
        "assessment_type": assessment.assessment_type,
        "assessment_status": assessment.assessment_status,
        "current_snapshot_id": assessment.current_snapshot_id,
        "result_version": assessment.result_version,
        "total_score": assessment.total_score,
        "risk_level": snapshot.risk_level if snapshot else None,
        "level_code": snapshot.level_code if snapshot else None,
        "reported_at": iso_bj(assessment.reported_at),
        "source": assessment.source,
        "created_at": iso_bj(assessment.created_at),
        "updated_at": iso_bj(assessment.updated_at),
    }
    if item_count is not None:
        payload["item_count"] = item_count
    return payload


def _assessment_context(
    db: DBSession, assessments: List[ResearchAssessment]
) -> Tuple[Dict[str, ExternalResultSnapshot], Dict[str, ResearchParticipant], Dict[str, str]]:
    snapshot_ids = {a.current_snapshot_id for a in assessments if a.current_snapshot_id}
    participant_ids = {a.participant_id for a in assessments}
    group_ids = {a.group_id for a in assessments if a.group_id}
    snapshots = {
        s.snapshot_id: s
        for s in db.query(ExternalResultSnapshot)
        .filter(ExternalResultSnapshot.snapshot_id.in_(snapshot_ids))
        .all()
    } if snapshot_ids else {}
    participants = {
        p.participant_id: p
        for p in db.query(ResearchParticipant)
        .filter(ResearchParticipant.participant_id.in_(participant_ids))
        .all()
    } if participant_ids else {}
    groups = {
        g.group_id: g.name
        for g in db.query(ResearchGroup)
        .filter(ResearchGroup.group_id.in_(group_ids))
        .all()
    } if group_ids else {}
    return snapshots, participants, groups


def assessments_payload(
    db: DBSession, assessments: List[ResearchAssessment]
) -> List[Dict[str, Any]]:
    snapshots, participants, groups = _assessment_context(db, assessments)
    return [
        _assessment_dict(
            a,
            snapshots.get(a.current_snapshot_id or ""),
            participants.get(a.participant_id),
            groups.get(a.group_id or ""),
        )
        for a in assessments
    ]


def list_assessments(
    db: DBSession,
    project_id: Optional[str],
    participant_id: Optional[str],
    session_id: Optional[str],
    questionnaire_code: Optional[str],
    status: Optional[str],
    mode: str,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    page: int,
    page_size: int,
) -> Tuple[List[ResearchAssessment], int]:
    if mode == "latest_per_scale" and not participant_id:
        raise ResearchError(
            422, "LATEST_PER_SCALE_REQUIRES_PARTICIPANT: participant_id is required"
        )
    query = db.query(ResearchAssessment)
    if project_id:
        query = query.filter(ResearchAssessment.project_id == project_id)
    if participant_id:
        query = query.filter(ResearchAssessment.participant_id == participant_id)
    if session_id:
        query = query.filter(ResearchAssessment.session_id == session_id)
    if questionnaire_code:
        query = query.filter(
            ResearchAssessment.questionnaire_code == questionnaire_code
        )
    if status:
        query = query.filter(ResearchAssessment.assessment_status == status)
    if date_from:
        query = query.filter(ResearchAssessment.reported_at >= to_utc(date_from))
    if date_to:
        query = query.filter(ResearchAssessment.reported_at <= to_utc(date_to))
    query = query.order_by(
        ResearchAssessment.reported_at.desc(),
        ResearchAssessment.id.desc(),
    )
    if mode != "latest_per_scale":
        return _paginate(query, page, page_size)
    # 每被试每量表只保留最新一次；在应用层去重后分页
    rows = query.all()
    seen = set()
    latest: List[ResearchAssessment] = []
    for row in rows:
        key = (row.participant_id, row.questionnaire_code)
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    start = (page - 1) * page_size
    return latest[start : start + page_size], len(latest)


def get_assessment(db: DBSession, assessment_id: str) -> ResearchAssessment:
    assessment = (
        db.query(ResearchAssessment)
        .filter(ResearchAssessment.assessment_id == assessment_id)
        .first()
    )
    if assessment is None:
        raise ResearchError(404, f"assessment {assessment_id} not found")
    return assessment


def assessment_detail(db: DBSession, assessment_id: str) -> Dict[str, Any]:
    assessment = get_assessment(db, assessment_id)
    snapshots, participants, groups = _assessment_context(db, [assessment])
    item_count = (
        db.query(func.count(ResearchAssessmentItem.id))
        .filter(ResearchAssessmentItem.assessment_id == assessment_id)
        .scalar()
    )
    payload = _assessment_dict(
        assessment,
        snapshots.get(assessment.current_snapshot_id or ""),
        participants.get(assessment.participant_id),
        groups.get(assessment.group_id or ""),
        item_count=item_count or 0,
    )
    snapshot = snapshots.get(assessment.current_snapshot_id or "")
    payload["snapshot"] = _snapshot_dict(snapshot) if snapshot else None
    payload["conversation_count"] = (
        db.query(func.count(ResearchConversation.id))
        .filter(ResearchConversation.assessment_id == assessment_id)
        .scalar()
        or 0
    )
    return payload


def _snapshot_dict(snapshot: ExternalResultSnapshot) -> Dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "result_version": snapshot.result_version,
        "status": snapshot.status,
        "total_score": snapshot.total_score,
        "scale_name": snapshot.scale_name,
        "questionnaire_version": snapshot.questionnaire_version,
        "dimensions": snapshot.dimensions,
        "radar_norm": snapshot.radar_norm,
        "risk_level": snapshot.risk_level,
        "level_code": snapshot.level_code,
        "trust_level": snapshot.trust_level,
        "source_payload_sha256": snapshot.source_payload_sha256,
        "received_at": iso_bj(snapshot.received_at),
    }


def session_assessments(db: DBSession, session_id: str) -> List[Dict[str, Any]]:
    get_session_or_error(db, session_id)
    rows = (
        db.query(ResearchAssessment)
        .filter(ResearchAssessment.session_id == session_id)
        .order_by(ResearchAssessment.id.asc())
        .all()
    )
    return assessments_payload(db, rows)


# ---------------------------------------------------------------------------
# 逐题作答
# ---------------------------------------------------------------------------
def _item_dict(item: ResearchAssessmentItem) -> Dict[str, Any]:
    return {
        "item_id": item.item_id,
        "assessment_id": item.assessment_id,
        "result_version": item.result_version,
        "question_id": item.question_id,
        "question_index": item.question_index,
        "question_type": item.question_type,
        "answer_value": item.answer_value,
        "score": item.score,
        "max_score": item.max_score,
        "response_ms": item.response_ms,
        "answered_at": iso_bj(item.answered_at),
        "source": item.source,
        "updated_at": iso_bj(item.updated_at),
    }


def upsert_assessment_items(
    db: DBSession,
    session_id: str,
    questionnaire_code: str,
    result_version: int,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    assessment = (
        db.query(ResearchAssessment)
        .filter(
            ResearchAssessment.session_id == session_id,
            ResearchAssessment.questionnaire_code == questionnaire_code,
        )
        .first()
    )
    if assessment is None:
        raise ResearchError(
            404,
            "ASSESSMENT_NOT_FOUND: sync the aggregate result first "
            f"(session={session_id}, questionnaire={questionnaire_code})",
        )
    now = utcnow()
    written = 0
    for item in items:
        existing = (
            db.query(ResearchAssessmentItem)
            .filter(
                ResearchAssessmentItem.assessment_id == assessment.assessment_id,
                ResearchAssessmentItem.result_version == result_version,
                ResearchAssessmentItem.question_id == item["question_id"],
            )
            .first()
        )
        answered_at = to_utc(item.get("answered_at"))
        if existing is None:
            existing = ResearchAssessmentItem(
                item_id=new_id("itm"),
                assessment_id=assessment.assessment_id,
                result_version=result_version,
                question_id=item["question_id"],
                question_index=item["question_index"],
                question_type=item.get("question_type"),
                answer_value=item.get("answer_value"),
                score=item.get("score"),
                max_score=item.get("max_score"),
                response_ms=item.get("response_ms"),
                answered_at=answered_at,
                source="live",
                created_at=now,
                updated_at=now,
            )
            db.add(existing)
        else:
            existing.question_index = item["question_index"]
            existing.question_type = item.get("question_type")
            existing.answer_value = item.get("answer_value")
            existing.score = item.get("score")
            existing.max_score = item.get("max_score")
            existing.response_ms = item.get("response_ms")
            existing.answered_at = answered_at
            existing.updated_at = now
        written += 1
    db.commit()
    rows = (
        db.query(ResearchAssessmentItem)
        .filter(
            ResearchAssessmentItem.assessment_id == assessment.assessment_id,
            ResearchAssessmentItem.result_version == result_version,
        )
        .order_by(ResearchAssessmentItem.question_index.asc())
        .all()
    )
    return {
        "assessment_id": assessment.assessment_id,
        "session_id": session_id,
        "questionnaire_code": questionnaire_code,
        "result_version": result_version,
        "written": written,
        "total": len(rows),
        "items": [_item_dict(row) for row in rows],
    }


def read_assessment_items(
    db: DBSession, assessment_id: str, result_version: Optional[int]
) -> Dict[str, Any]:
    get_assessment(db, assessment_id)
    query = db.query(ResearchAssessmentItem).filter(
        ResearchAssessmentItem.assessment_id == assessment_id
    )
    if result_version is not None:
        query = query.filter(ResearchAssessmentItem.result_version == result_version)
    rows = query.order_by(
        ResearchAssessmentItem.result_version.desc(),
        ResearchAssessmentItem.question_index.asc(),
    ).all()
    return {
        "assessment_id": assessment_id,
        "result_version": result_version,
        "total": len(rows),
        "items": [_item_dict(row) for row in rows],
    }


# ---------------------------------------------------------------------------
# AI 对话
# ---------------------------------------------------------------------------
def get_conversation(db: DBSession, conversation_id: str) -> ResearchConversation:
    conversation = (
        db.query(ResearchConversation)
        .filter(ResearchConversation.conversation_id == conversation_id)
        .first()
    )
    if conversation is None:
        raise ResearchError(404, f"conversation {conversation_id} not found")
    return conversation


def conversation_dict(
    conversation: ResearchConversation, include_messages: bool = False,
    messages: Optional[List[ResearchConversationMessage]] = None,
) -> Dict[str, Any]:
    payload = {
        "conversation_id": conversation.conversation_id,
        "project_id": conversation.project_id,
        "participant_id": conversation.participant_id,
        "session_id": conversation.session_id,
        "assessment_id": conversation.assessment_id,
        "external_conversation_id": conversation.external_conversation_id,
        "source": conversation.source,
        "message_count": conversation.message_count,
        "started_at": iso_bj(conversation.started_at),
        "ended_at": iso_bj(conversation.ended_at),
        "content_sha256": conversation.content_sha256,
        "created_at": iso_bj(conversation.created_at),
        "updated_at": iso_bj(conversation.updated_at),
    }
    if include_messages:
        payload["messages"] = [_message_dict(m) for m in (messages or [])]
    return payload


def _message_dict(message: ResearchConversationMessage) -> Dict[str, Any]:
    return {
        "message_id": message.message_id,
        "conversation_id": message.conversation_id,
        "seq": message.seq,
        "role": message.role,
        "content": message.content,
        "content_type": message.content_type,
        "attachment_ref": message.attachment_ref,
        "model": message.model,
        "token_count": message.token_count,
        "content_sha256": message.content_sha256,
        "occurred_at": iso_bj(message.occurred_at),
    }


def create_conversation(db: DBSession, data: Dict[str, Any]) -> Tuple[ResearchConversation, bool]:
    session = get_session_or_error(db, data["session_id"])
    if not session.project_id:
        raise ResearchError(
            409, "SESSION_NOT_BOUND: assign the session to a participant first"
        )
    participant_id = data.get("participant_id") or session.participant_id
    if participant_id:
        participant = get_participant(db, participant_id)
        if participant.project_id != session.project_id:
            raise ResearchError(422, "PARTICIPANT_PROJECT_MISMATCH")
    assessment_id = data.get("assessment_id")
    if assessment_id:
        assessment = get_assessment(db, assessment_id)
        if assessment.session_id != session.session_id:
            raise ResearchError(
                422, "ASSESSMENT_SESSION_MISMATCH: assessment belongs to another session"
            )
    external_id = data.get("external_conversation_id")
    if external_id:
        existing = (
            db.query(ResearchConversation)
            .filter(
                ResearchConversation.source == data["source"],
                ResearchConversation.external_conversation_id == external_id,
            )
            .first()
        )
        if existing is not None:
            return existing, False
    now = utcnow()
    conversation = ResearchConversation(
        conversation_id=new_id("cv"),
        project_id=session.project_id,
        participant_id=participant_id,
        session_id=session.session_id,
        assessment_id=assessment_id,
        external_conversation_id=external_id,
        source=data["source"],
        message_count=0,
        started_at=to_utc(data.get("started_at")),
        ended_at=to_utc(data.get("ended_at")),
        created_at=now,
        updated_at=now,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation, True


def upload_messages(
    db: DBSession, conversation_id: str, messages: List[Dict[str, Any]]
) -> Dict[str, Any]:
    conversation = get_conversation(db, conversation_id)
    now = utcnow()
    inserted = 0
    skipped = 0
    for message in messages:
        existing = (
            db.query(ResearchConversationMessage)
            .filter(
                ResearchConversationMessage.conversation_id == conversation_id,
                ResearchConversationMessage.seq == message["seq"],
            )
            .first()
        )
        if existing is not None:
            if existing.message_id == message["message_id"]:
                skipped += 1
                continue
            raise ResearchError(
                409, f"SEQ_CONFLICT: seq {message['seq']} already exists"
            )
        db.add(
            ResearchConversationMessage(
                message_id=message["message_id"],
                conversation_id=conversation_id,
                seq=message["seq"],
                role=message["role"],
                content=message["content"],
                content_type=message.get("content_type") or "text",
                attachment_ref=message.get("attachment_ref"),
                model=message.get("model"),
                token_count=message.get("token_count"),
                content_sha256=sha256_text(message["content"]),
                occurred_at=to_utc(message["occurred_at"]) or now,
                created_at=now,
            )
        )
        inserted += 1
    db.flush()
    rows = (
        db.query(ResearchConversationMessage)
        .filter(ResearchConversationMessage.conversation_id == conversation_id)
        .order_by(ResearchConversationMessage.seq.asc())
        .all()
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row.seq}\x1f{row.role}\x1f{row.content}\x1e".encode("utf-8"))
    conversation.message_count = len(rows)
    conversation.content_sha256 = digest.hexdigest()
    if conversation.started_at is None and rows:
        conversation.started_at = rows[0].occurred_at
    if rows:
        conversation.ended_at = rows[-1].occurred_at
    conversation.updated_at = now
    db.commit()
    db.refresh(conversation)
    return {
        "conversation_id": conversation_id,
        "inserted": inserted,
        "skipped": skipped,
        "message_count": conversation.message_count,
        "content_sha256": conversation.content_sha256,
    }


def list_conversations(
    db: DBSession,
    project_id: Optional[str],
    participant_id: Optional[str],
    session_id: Optional[str],
    assessment_id: Optional[str],
    page: int,
    page_size: int,
) -> Tuple[List[ResearchConversation], int]:
    query = db.query(ResearchConversation)
    if project_id:
        query = query.filter(ResearchConversation.project_id == project_id)
    if participant_id:
        query = query.filter(ResearchConversation.participant_id == participant_id)
    if session_id:
        query = query.filter(ResearchConversation.session_id == session_id)
    if assessment_id:
        query = query.filter(ResearchConversation.assessment_id == assessment_id)
    query = query.order_by(ResearchConversation.id.desc())
    return _paginate(query, page, page_size)


def read_messages(
    db: DBSession, conversation_id: str, after_seq: int
) -> List[ResearchConversationMessage]:
    get_conversation(db, conversation_id)
    return (
        db.query(ResearchConversationMessage)
        .filter(
            ResearchConversationMessage.conversation_id == conversation_id,
            ResearchConversationMessage.seq > after_seq,
        )
        .order_by(ResearchConversationMessage.seq.asc())
        .all()
    )


def link_assessment(
    db: DBSession, conversation_id: str, assessment_id: str
) -> ResearchConversation:
    conversation = get_conversation(db, conversation_id)
    assessment = get_assessment(db, assessment_id)
    if assessment.session_id != conversation.session_id:
        raise ResearchError(
            422, "ASSESSMENT_SESSION_MISMATCH: assessment belongs to another session"
        )
    conversation.assessment_id = assessment.assessment_id
    conversation.updated_at = utcnow()
    db.commit()
    db.refresh(conversation)
    return conversation


# ---------------------------------------------------------------------------
# 历史迁移（仅运维用途）
# ---------------------------------------------------------------------------
_LEGACY_PAYLOAD_KEYS = {
    "status",
    "total_score",
    "scoring_mode",
    "scale_name",
    "questionnaire_version",
    "scale_algorithm_version",
    "dimensions",
    "radar_norm",
    "risk_level",
    "level_code",
    "recommendation_code",
    "ai_profile_score",
    "summary_code",
}


def _legacy_snapshot_payload(
    item: Dict[str, Any], reported_at: datetime
) -> Dict[str, Any]:
    raw = item.get("result_payload") or {}
    payload = {key: raw[key] for key in _LEGACY_PAYLOAD_KEYS if key in raw}
    payload.setdefault("status", "complete")
    if item.get("total_score") is not None:
        payload["total_score"] = item["total_score"]
    payload["reported_at"] = iso_bj(reported_at)
    return payload


def _legacy_payload_sha(payload: Dict[str, Any]) -> str:
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _resolve_or_create_legacy_group(
    db: DBSession, project_id: str, group_id: Optional[str], group_name: Optional[str]
) -> Optional[str]:
    if group_id:
        group = get_group(db, group_id)
        if group.project_id != project_id:
            raise ResearchError(422, "GROUP_PROJECT_MISMATCH")
        return group.group_id
    if not group_name:
        return None
    group = (
        db.query(ResearchGroup)
        .filter(ResearchGroup.project_id == project_id, ResearchGroup.name == group_name)
        .first()
    )
    if group is None:
        group = create_group(db, project_id, group_name, "other", 0)
    return group.group_id


def _create_synthetic_session(
    db: DBSession,
    project_id: str,
    participant: ResearchParticipant,
    reported_at: datetime,
) -> SessionModel:
    now = utcnow()
    session = SessionModel(
        # 合成会话使用独立前缀，避免与设备真实 session_id 混淆
        session_id="legacy_" + uuid.uuid4().hex[:20],
        device_id="legacy_import",
        session_type="assessment",
        source_system="standalone",
        participant_id=participant.participant_id,
        project_id=project_id,
        group_id=participant.group_id,
        data_origin="legacy_import",
        anchor_utc_at=reported_at,
        anchor_source="server",
        anchor_uncertainty_ms=0,
        status="completed",
        started_at=reported_at,
        ended_at=reported_at,
        ended_reason="legacy import",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    return session


def legacy_import(db: DBSession, project_id: str, req: Dict[str, Any]) -> Dict[str, Any]:
    get_project(db, project_id)
    dry_run = req.get("dry_run", True)
    participants = req.get("participants") or []
    assessments = req.get("assessments") or []
    stats = {
        "dry_run": dry_run,
        "participants_created": 0,
        "participants_reused": 0,
        "sessions_created": 0,
        "assessments_created": 0,
        "assessments_skipped": 0,
        "errors": [],
    }
    local_to_participant: Dict[str, ResearchParticipant] = {}
    for item in participants:
        existing = (
            db.query(ResearchParticipant)
            .filter(
                ResearchParticipant.project_id == project_id,
                ResearchParticipant.participant_code == item["participant_code"],
            )
            .first()
        )
        if existing is not None:
            stats["participants_reused"] += 1
            local_to_participant[item["source_local_id"]] = existing
            continue
        stats["participants_created"] += 1
        if dry_run:
            # 不落库的临时对象，仅用于下游台账预测
            local_to_participant[item["source_local_id"]] = ResearchParticipant(
                participant_id="(pending)",
                project_id=project_id,
                participant_code=item["participant_code"],
                status="active",
                row_version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            continue
        group_id = _resolve_or_create_legacy_group(
            db, project_id, item.get("group_id"), item.get("group_name")
        )
        participant = ResearchParticipant(
            participant_id=new_id("pt"),
            project_id=project_id,
            participant_code=item["participant_code"],
            group_id=group_id,
            name=item.get("name"),
            phone=item.get("phone"),
            gender=item.get("gender"),
            age=item.get("age"),
            school=item.get("school"),
            grade=item.get("grade"),
            status="active",
            source_local_id=item["source_local_id"],
            row_version=1,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(participant)
        db.flush()
        local_to_participant[item["source_local_id"]] = participant

    for item in assessments:
        local_id = item["source_local_id"]
        participant = local_to_participant.get(local_id)
        if participant is None:
            participant = (
                db.query(ResearchParticipant)
                .filter(
                    ResearchParticipant.project_id == project_id,
                    ResearchParticipant.source_local_id == local_id,
                )
                .first()
            )
        if participant is None:
            stats["errors"].append(
                {
                    "source_local_id": local_id,
                    "code": "PARTICIPANT_NOT_FOUND",
                    "message": "participant must be provided in the same request",
                }
            )
            continue
        reported_at = to_utc(item["reported_at"])
        payload = _legacy_snapshot_payload(item, reported_at)
        payload_sha = _legacy_payload_sha(payload)
        idempotency_key = "legacy:" + sha256_text(
            f"{project_id}|{participant.participant_id}|"
            f"{item['questionnaire_code']}|{reported_at.isoformat()}|{payload_sha}"
        )[:120]
        existing_assessment = (
            db.query(ResearchAssessment)
            .filter(ResearchAssessment.idempotency_key == idempotency_key)
            .first()
        )
        if existing_assessment is not None:
            stats["assessments_skipped"] += 1
            continue
        if dry_run:
            stats["assessments_created"] += 1
            continue

        session: Optional[SessionModel] = None
        if item.get("s1_session_id"):
            session = (
                db.query(SessionModel)
                .filter(SessionModel.session_id == item["s1_session_id"])
                .first()
            )
            if session is not None:
                if session.participant_id and session.participant_id != participant.participant_id:
                    stats["errors"].append(
                        {
                            "source_local_id": local_id,
                            "code": "SESSION_PARTICIPANT_CONFLICT",
                            "message": "session already bound to another participant",
                        }
                    )
                    continue
                session.participant_id = participant.participant_id
                session.project_id = project_id
                session.group_id = participant.group_id
                session.updated_at = utcnow()
        if session is None:
            session = _create_synthetic_session(
                db, project_id, participant, reported_at
            )
            stats["sessions_created"] += 1

        now = utcnow()
        snapshot = ExternalResultSnapshot(
            snapshot_id="rs_" + uuid.uuid4().hex,
            session_id=session.session_id,
            source_system="legacy_import",
            questionnaire_code=item["questionnaire_code"],
            questionnaire_version=payload.get("questionnaire_version"),
            scale_algorithm_version=payload.get("scale_algorithm_version"),
            result_version=1,
            status=payload.get("status", "complete"),
            total_score=payload.get("total_score"),
            scoring_mode=payload.get("scoring_mode"),
            scale_name=payload.get("scale_name"),
            dimensions=payload.get("dimensions"),
            radar_norm=payload.get("radar_norm"),
            risk_level=payload.get("risk_level"),
            level_code=payload.get("level_code"),
            recommendation_code=payload.get("recommendation_code"),
            ai_profile_score=payload.get("ai_profile_score"),
            summary_code=payload.get("summary_code"),
            trust_level="client_relayed",
            source_payload_sha256=payload_sha,
            idempotency_key=f"res:{session.session_id}:{item['questionnaire_code']}:v1",
            received_at=reported_at,
            created_at=now,
        )
        db.add(snapshot)
        db.flush()
        assessment = ResearchAssessment(
            assessment_id=new_id("asm"),
            project_id=project_id,
            participant_id=participant.participant_id,
            group_id=participant.group_id,
            session_id=session.session_id,
            questionnaire_code=item["questionnaire_code"],
            questionnaire_version=payload.get("questionnaire_version"),
            scale_name=payload.get("scale_name"),
            assessment_type="scale",
            assessment_status=payload.get("status", "complete"),
            current_snapshot_id=snapshot.snapshot_id,
            result_version=1,
            total_score=payload.get("total_score"),
            reported_at=reported_at,
            source="legacy_import",
            source_local_key=local_id,
            idempotency_key=idempotency_key,
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(assessment)
        db.flush()
        snapshot.assessment_id = assessment.assessment_id
        snapshot.participant_id = participant.participant_id
        stats["assessments_created"] += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats
