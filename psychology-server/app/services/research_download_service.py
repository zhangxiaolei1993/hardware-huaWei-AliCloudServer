"""科研下载中心：异步任务 + 数据包构建。

包内时间统一 UTC（ISO8601，后缀 ``Z``）；API 响应时间为 ``+08:00``。
字段全部来自真实表，未采集字段只在 ``data_dictionary.csv`` 中标注
``available=false``，不编造数值。
"""
import csv
import hashlib
import json
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.db.database import SessionLocal, utcnow
from app.models.device import Device
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.research import (
    ResearchAssessment,
    ResearchAssessmentItem,
    ResearchConversation,
    ResearchConversationMessage,
    ResearchExportJob,
    ResearchGroup,
    ResearchParticipant,
    ResearchProject,
)
from app.models.session import SessionModel
from app.services.research_service import iso_bj

SCHEMA_VERSION = "research-package-v1"
DATA_DICTIONARY_VERSION = "v1"
MAX_ROWS = 5_000_000
MAX_BYTES = 2 * 1024 * 1024 * 1024
JOB_TTL_DAYS = 7

DATA_TYPES = (
    "participants",
    "sessions",
    "assessments",
    "assessment_items",
    "expression_summary",
    "expression_timeline",
    "conversation",
    "devices",
)

#: 每个数据集的实际输出字段；order 即 CSV 列顺序
FIELDS: Dict[str, List[str]] = {
    "participants": [
        "participant_id",
        "project_id",
        "group_id",
        "group_name",
        "participant_code",
        "name",
        "phone",
        "gender",
        "age",
        "school",
        "grade",
        "status",
        "source_local_id",
        "row_version",
        "archived_at_utc",
        "created_at_utc",
        "updated_at_utc",
    ],
    "sessions": [
        "session_id",
        "project_id",
        "participant_id",
        "group_id",
        "group_name",
        "device_id",
        "data_origin",
        "session_type",
        "source_system",
        "external_session_id",
        "status",
        "ended_reason",
        "started_at_utc",
        "ended_at_utc",
        "created_at_utc",
        "updated_at_utc",
    ],
    "assessments": [
        "assessment_id",
        "project_id",
        "participant_id",
        "participant_code",
        "group_id",
        "group_name",
        "session_id",
        "questionnaire_code",
        "questionnaire_version",
        "scale_name",
        "assessment_type",
        "assessment_status",
        "current_snapshot_id",
        "result_version",
        "total_score",
        "risk_level",
        "level_code",
        "reported_at_utc",
        "source",
        "created_at_utc",
        "updated_at_utc",
    ],
    "assessment_items": [
        "assessment_id",
        "participant_id",
        "participant_code",
        "session_id",
        "questionnaire_code",
        "result_version",
        "question_id",
        "question_index",
        "question_type",
        "answer_value",
        "score",
        "max_score",
        "response_ms",
        "answered_at_utc",
        "source",
    ],
    "expression_summary": [
        "session_id",
        "participant_id",
        "participant_code",
        "dominant_expression",
        "valid_expression_seconds",
        "average_confidence",
        "face_coverage_percent",
        "created_at_utc",
        "updated_at_utc",
        "result_json",
    ],
    "expression_timeline": [
        "session_id",
        "participant_id",
        "participant_code",
        "device_id",
        "stream_id",
        "relative_seconds",
        "expression",
        "confidence",
        "quality",
        "device_timestamp",
        "monotonic_ms",
        "frame_index",
        "created_at_utc",
    ],
    "conversation": [
        "conversation_id",
        "project_id",
        "participant_id",
        "participant_code",
        "session_id",
        "assessment_id",
        "external_conversation_id",
        "source",
        "seq",
        "role",
        "content",
        "content_type",
        "model",
        "token_count",
        "occurred_at_utc",
    ],
    "devices": [
        "device_id",
        "device_name",
        "device_type",
        "manufacturer",
        "model",
        "firmware_version",
    ],
}

#: 数据字典：字段来源与说明。available=false 表示云端确实没有该字段。
DICTIONARY: Dict[str, List[Tuple[str, str, str, str, bool, str]]] = {
    "participants": [
        ("participant_id", "string", "被试云 ID", "research_participants.participant_id", True, ""),
        ("project_id", "string", "项目 ID", "research_participants.project_id", True, ""),
        ("group_id", "string", "组 ID", "research_participants.group_id", True, ""),
        ("group_name", "string", "用户自定义组名", "research_groups.name", True, ""),
        ("participant_code", "string", "项目内被试编号", "research_participants.participant_code", True, ""),
        ("name", "string", "姓名", "research_participants.name", True, "用户已确认上云"),
        ("phone", "string", "电话", "research_participants.phone", True, "用户已确认上云"),
        ("gender", "string", "性别", "research_participants.gender", True, ""),
        ("age", "integer", "年龄", "research_participants.age", True, ""),
        ("school", "string", "学校", "research_participants.school", True, ""),
        ("grade", "string", "年级", "research_participants.grade", True, ""),
        ("status", "string", "状态", "research_participants.status", True, ""),
        ("source_local_id", "string", "本机历史 ID", "research_participants.source_local_id", True, ""),
        ("row_version", "integer", "行版本（乐观锁）", "research_participants.row_version", True, ""),
        ("archived_at_utc", "datetime", "归档时间", "research_participants.archived_at", True, ""),
        ("created_at_utc", "datetime", "创建时间", "research_participants.created_at", True, ""),
        ("updated_at_utc", "datetime", "更新时间", "research_participants.updated_at", True, ""),
        ("consent_signed", "boolean", "知情同意签署", "-", False, "null: 云端未采集"),
    ],
    "sessions": [
        ("session_id", "string", "会话号 S1", "sessions.session_id", True, ""),
        ("project_id", "string", "项目 ID", "sessions.project_id", True, ""),
        ("participant_id", "string", "被试 ID", "sessions.participant_id", True, ""),
        ("group_id", "string", "施测时组 ID", "sessions.group_id", True, ""),
        ("group_name", "string", "施测时组名", "research_groups.name", True, ""),
        ("device_id", "string", "设备 ID", "sessions.device_id", True, ""),
        ("data_origin", "string", "live/legacy_import", "sessions.data_origin", True, ""),
        ("session_type", "string", "会话类型", "sessions.session_type", True, ""),
        ("source_system", "string", "来源系统", "sessions.source_system", True, ""),
        ("external_session_id", "string", "外部会话号 N1", "sessions.external_session_id", True, ""),
        ("status", "string", "会话状态", "sessions.status", True, "interrupted 输入已映射 aborted"),
        ("ended_reason", "string", "结束原因", "sessions.ended_reason", True, ""),
        ("started_at_utc", "datetime", "开始时间", "sessions.started_at", True, ""),
        ("ended_at_utc", "datetime", "结束时间", "sessions.ended_at", True, ""),
        ("created_at_utc", "datetime", "创建时间", "sessions.created_at", True, ""),
        ("updated_at_utc", "datetime", "更新时间", "sessions.updated_at", True, ""),
        ("external_user_id", "string", "外部受试者 ID", "sessions.external_user_id", False, "null: 按隐私口径不导出"),
    ],
    "assessments": [
        ("assessment_id", "string", "测评 ID", "research_assessments.assessment_id", True, ""),
        ("project_id", "string", "项目 ID", "research_assessments.project_id", True, ""),
        ("participant_id", "string", "被试 ID", "research_assessments.participant_id", True, ""),
        ("participant_code", "string", "被试编号", "research_participants.participant_code", True, ""),
        ("group_id", "string", "施测时组 ID", "research_assessments.group_id", True, ""),
        ("group_name", "string", "施测时组名", "research_groups.name", True, ""),
        ("session_id", "string", "会话号 S1", "research_assessments.session_id", True, ""),
        ("questionnaire_code", "string", "量表编码", "research_assessments.questionnaire_code", True, ""),
        ("questionnaire_version", "string", "量表版本", "research_assessments.questionnaire_version", True, ""),
        ("scale_name", "string", "量表名称", "research_assessments.scale_name", True, ""),
        ("assessment_type", "string", "测评类型", "research_assessments.assessment_type", True, ""),
        ("assessment_status", "string", "测评状态", "research_assessments.assessment_status", True, ""),
        ("current_snapshot_id", "string", "当前结果快照", "research_assessments.current_snapshot_id", True, ""),
        ("result_version", "integer", "结果版本", "research_assessments.result_version", True, ""),
        ("total_score", "number", "总分", "research_assessments.total_score", True, ""),
        ("risk_level", "string", "风险等级", "external_result_snapshots.risk_level", True, ""),
        ("level_code", "string", "等级编码", "external_result_snapshots.level_code", True, ""),
        ("reported_at_utc", "datetime", "报告时间", "research_assessments.reported_at", True, ""),
        ("source", "string", "live/legacy_import", "research_assessments.source", True, ""),
        ("created_at_utc", "datetime", "创建时间", "research_assessments.created_at", True, ""),
        ("updated_at_utc", "datetime", "更新时间", "research_assessments.updated_at", True, ""),
        ("attempt_no", "integer", "同会话同量表第几次施测", "-", False, "null: 当前未采集"),
    ],
    "assessment_items": [
        ("assessment_id", "string", "测评 ID", "research_assessment_items.assessment_id", True, ""),
        ("participant_id", "string", "被试 ID", "research_assessments.participant_id", True, ""),
        ("participant_code", "string", "被试编号", "research_participants.participant_code", True, ""),
        ("session_id", "string", "会话号 S1", "research_assessments.session_id", True, ""),
        ("questionnaire_code", "string", "量表编码", "research_assessments.questionnaire_code", True, ""),
        ("result_version", "integer", "结果版本", "research_assessment_items.result_version", True, ""),
        ("question_id", "string", "题目 ID", "research_assessment_items.question_id", True, ""),
        ("question_index", "integer", "题目序号", "research_assessment_items.question_index", True, ""),
        ("question_type", "string", "题型", "research_assessment_items.question_type", True, ""),
        ("answer_value", "json", "作答值", "research_assessment_items.answer_value", True, ""),
        ("score", "number", "题目得分", "research_assessment_items.score", True, ""),
        ("max_score", "number", "题目满分", "research_assessment_items.max_score", True, ""),
        ("response_ms", "integer", "作答耗时毫秒", "research_assessment_items.response_ms", True, ""),
        ("answered_at_utc", "datetime", "作答时间", "research_assessment_items.answered_at", True, ""),
        ("source", "string", "数据来源", "research_assessment_items.source", True, ""),
    ],
    "expression_summary": [
        ("session_id", "string", "会话号 S1", "emotion_results.session_id", True, ""),
        ("participant_id", "string", "被试 ID", "sessions.participant_id", True, ""),
        ("participant_code", "string", "被试编号", "research_participants.participant_code", True, ""),
        ("dominant_expression", "string", "主导表情", "emotion_results.dominant_expression", True, ""),
        ("valid_expression_seconds", "number", "有效表情时长(秒)", "emotion_results.valid_expression_seconds", True, ""),
        ("average_confidence", "number", "平均置信度", "emotion_results.average_confidence", True, ""),
        ("face_coverage_percent", "number", "人脸覆盖率(%)", "emotion_results.face_coverage_percent", True, ""),
        ("created_at_utc", "datetime", "创建时间", "emotion_results.created_at", True, ""),
        ("updated_at_utc", "datetime", "更新时间", "emotion_results.updated_at", True, ""),
        ("result_json", "json", "服务器重算结果明细", "emotion_results.result_json", True, ""),
    ],
    "expression_timeline": [
        ("session_id", "string", "会话号 S1", "emotion_records.session_id", True, ""),
        ("participant_id", "string", "被试 ID", "sessions.participant_id", True, ""),
        ("participant_code", "string", "被试编号", "research_participants.participant_code", True, ""),
        ("device_id", "string", "设备 ID", "emotion_records.device_id", True, ""),
        ("stream_id", "string", "流 ID", "emotion_records.stream_id", True, ""),
        ("relative_seconds", "number", "相对秒", "emotion_records.relative_seconds", True, ""),
        ("expression", "string", "表情", "emotion_records.expression", True, ""),
        ("confidence", "number", "置信度", "emotion_records.confidence", True, ""),
        ("quality", "string", "质量标记", "emotion_records.quality", True, ""),
        ("device_timestamp", "string", "设备时间戳", "emotion_records.device_timestamp", True, ""),
        ("monotonic_ms", "integer", "设备单调时钟", "emotion_records.monotonic_ms", True, ""),
        ("frame_index", "integer", "帧序号", "emotion_records.frame_index", True, ""),
        ("created_at_utc", "datetime", "服务器接收时间", "emotion_records.created_at", True, ""),
    ],
    "conversation": [
        ("conversation_id", "string", "对话 ID", "research_conversations.conversation_id", True, ""),
        ("project_id", "string", "项目 ID", "research_conversations.project_id", True, ""),
        ("participant_id", "string", "被试 ID", "research_conversations.participant_id", True, ""),
        ("participant_code", "string", "被试编号", "research_participants.participant_code", True, ""),
        ("session_id", "string", "会话号 S1", "research_conversations.session_id", True, ""),
        ("assessment_id", "string", "关联测评 ID", "research_conversations.assessment_id", True, "可为空"),
        ("external_conversation_id", "string", "外部对话 ID", "research_conversations.external_conversation_id", True, ""),
        ("source", "string", "对话来源", "research_conversations.source", True, ""),
        ("seq", "integer", "消息序号", "research_conversation_messages.seq", True, ""),
        ("role", "string", "角色", "research_conversation_messages.role", True, ""),
        ("content", "text", "消息正文", "research_conversation_messages.content", True, ""),
        ("content_type", "string", "内容类型", "research_conversation_messages.content_type", True, ""),
        ("model", "string", "模型", "research_conversation_messages.model", True, ""),
        ("token_count", "integer", "token 数", "research_conversation_messages.token_count", True, ""),
        ("occurred_at_utc", "datetime", "消息时间", "research_conversation_messages.occurred_at", True, ""),
    ],
    "devices": [
        ("device_id", "string", "设备 ID", "devices.device_id", True, ""),
        ("device_name", "string", "设备名称", "devices.device_name", True, ""),
        ("device_type", "string", "设备类型", "devices.device_type", True, ""),
        ("manufacturer", "string", "厂商", "devices.manufacturer", True, ""),
        ("model", "string", "型号", "devices.model", True, ""),
        ("firmware_version", "string", "固件版本", "devices.firmware_version", True, ""),
    ],
}

_CSV_SETS = {
    "participants",
    "sessions",
    "assessments",
    "assessment_items",
    "expression_summary",
    "expression_timeline",
    "conversation",
    "devices",
}


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc_iso(value)
    return value


def job_dict(job: ResearchExportJob) -> Dict[str, Any]:
    return {
        "export_id": job.export_id,
        "project_id": job.project_id,
        "status": job.status,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "counts": job.counts_json,
        "file_name": job.file_name,
        "file_size": job.file_size,
        "sha256": job.sha256,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "filters": job.filters_json,
        "created_at": iso_bj(job.created_at),
        "started_at": iso_bj(job.started_at),
        "finished_at": iso_bj(job.finished_at),
        "expires_at": iso_bj(job.expires_at),
    }


def _export_root() -> Path:
    root = Path(get_settings().export_dir) / "research_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_job(
    db: DBSession,
    project_id: str,
    filters: Dict[str, Any],
    requested_ip: Optional[str] = None,
) -> ResearchExportJob:
    project = (
        db.query(ResearchProject)
        .filter(ResearchProject.project_id == project_id)
        .first()
    )
    if project is None:
        raise ValueError(f"project {project_id} not found")
    data_types = filters.get("data_types") or list(DATA_TYPES)
    unknown = [t for t in data_types if t not in DATA_TYPES]
    if unknown:
        raise ValueError(f"unknown data_types: {unknown}")
    now = utcnow()
    job = ResearchExportJob(
        export_id=new_id("rex"),
        project_id=project_id,
        requested_by=None,  # 本轮无账号体系；权限校验留空 TODO(PERMISSION)
        requested_ip=requested_ip,
        filters_json=filters,
        status="queued",
        progress_current=0,
        progress_total=0,
        created_at=now,
        expires_at=now + timedelta(days=JOB_TTL_DAYS),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def cancel_or_delete(db: DBSession, export_id: str) -> ResearchExportJob:
    job = (
        db.query(ResearchExportJob)
        .filter(ResearchExportJob.export_id == export_id)
        .first()
    )
    if job is None:
        raise ValueError(f"export job {export_id} not found")
    if job.status == "running":
        raise ValueError("RUNNING_JOB_CANNOT_CANCEL: wait for completion")
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = utcnow()
    elif job.status in ("succeeded", "failed", "expired"):
        if job.file_path:
            Path(job.file_path).unlink(missing_ok=True)
        job.status = "expired"
        job.file_path = None
        job.finished_at = job.finished_at or utcnow()
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# 数据选择
# ---------------------------------------------------------------------------
def _resolve_group_ids(
    db: DBSession, project_id: str, group_ids: Sequence[str], group_names: Sequence[str]
) -> List[str]:
    ids = list(group_ids)
    if group_names:
        rows = (
            db.query(ResearchGroup)
            .filter(
                ResearchGroup.project_id == project_id,
                ResearchGroup.name.in_(list(group_names)),
            )
            .all()
        )
        ids.extend(r.group_id for r in rows)
    return sorted(set(ids))


def _select_participants(
    db: DBSession, project_id: str, filters: Dict[str, Any]
) -> List[ResearchParticipant]:
    query = db.query(ResearchParticipant).filter(
        ResearchParticipant.project_id == project_id
    )
    if not filters.get("include_archived"):
        query = query.filter(ResearchParticipant.status == "active")
    scope = filters.get("scope") or {}
    mode = scope.get("mode", "all")
    if mode == "group":
        ids = _resolve_group_ids(
            db,
            project_id,
            scope.get("group_ids") or [],
            scope.get("group_names") or [],
        )
        if not ids:
            return []
        query = query.filter(ResearchParticipant.group_id.in_(ids))
    elif mode == "participants":
        ids = scope.get("participant_ids") or []
        if not ids:
            return []
        query = query.filter(ResearchParticipant.participant_id.in_(ids))
    return query.order_by(ResearchParticipant.id.asc()).all()


def _select_sessions(
    db: DBSession, project_id: str, participant_ids: Sequence[str]
) -> List[SessionModel]:
    if not participant_ids:
        return []
    return (
        db.query(SessionModel)
        .filter(
            SessionModel.project_id == project_id,
            SessionModel.participant_id.in_(list(participant_ids)),
        )
        .order_by(SessionModel.id.asc())
        .all()
    )


def _select_assessments(
    db: DBSession,
    project_id: str,
    participant_ids: Sequence[str],
    assessment_scope: Dict[str, Any],
) -> List[ResearchAssessment]:
    if not participant_ids:
        return []
    query = db.query(ResearchAssessment).filter(
        ResearchAssessment.project_id == project_id,
        ResearchAssessment.participant_id.in_(list(participant_ids)),
    )
    mode = assessment_scope.get("mode", "all")
    if mode == "date_range":
        if assessment_scope.get("from_"):
            query = query.filter(
                ResearchAssessment.reported_at >= assessment_scope["from_"]
            )
        if assessment_scope.get("to"):
            query = query.filter(
                ResearchAssessment.reported_at <= assessment_scope["to"]
            )
    elif mode == "questionnaire":
        codes = assessment_scope.get("questionnaire_codes") or []
        if not codes:
            return []
        query = query.filter(ResearchAssessment.questionnaire_code.in_(codes))
    elif mode == "session":
        ids = assessment_scope.get("session_ids") or []
        if not ids:
            return []
        query = query.filter(ResearchAssessment.session_id.in_(ids))
    rows = query.order_by(
        ResearchAssessment.participant_id.asc(),
        ResearchAssessment.questionnaire_code.asc(),
        ResearchAssessment.reported_at.desc(),
        ResearchAssessment.id.desc(),
    ).all()
    if mode != "latest_per_scale":
        return rows
    seen = set()
    latest: List[ResearchAssessment] = []
    for row in rows:
        key = (row.participant_id, row.questionnaire_code)
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)
    return latest


# ---------------------------------------------------------------------------
# 数据行
# ---------------------------------------------------------------------------
def _participant_rows(
    db: DBSession, participants: List[ResearchParticipant]
) -> List[Dict[str, Any]]:
    group_ids = {p.group_id for p in participants if p.group_id}
    names = {
        g.group_id: g.name
        for g in (
            db.query(ResearchGroup).filter(ResearchGroup.group_id.in_(group_ids)).all()
            if group_ids
            else []
        )
    }
    return [
        {
            "participant_id": p.participant_id,
            "project_id": p.project_id,
            "group_id": p.group_id,
            "group_name": names.get(p.group_id or ""),
            "participant_code": p.participant_code,
            "name": p.name,
            "phone": p.phone,
            "gender": p.gender,
            "age": p.age,
            "school": p.school,
            "grade": p.grade,
            "status": p.status,
            "source_local_id": p.source_local_id,
            "row_version": p.row_version,
            "archived_at_utc": _utc_iso(p.archived_at),
            "created_at_utc": _utc_iso(p.created_at),
            "updated_at_utc": _utc_iso(p.updated_at),
        }
        for p in participants
    ]


def _session_rows(
    db: DBSession,
    sessions: List[SessionModel],
    participants: List[ResearchParticipant],
) -> List[Dict[str, Any]]:
    participant_map = {p.participant_id: p for p in participants}
    group_ids = {s.group_id for s in sessions if s.group_id}
    names = {
        g.group_id: g.name
        for g in (
            db.query(ResearchGroup).filter(ResearchGroup.group_id.in_(group_ids)).all()
            if group_ids
            else []
        )
    }
    rows = []
    for s in sessions:
        participant = participant_map.get(s.participant_id or "")
        rows.append(
            {
                "session_id": s.session_id,
                "project_id": s.project_id,
                "participant_id": s.participant_id,
                "group_id": s.group_id,
                "group_name": names.get(s.group_id or ""),
                "device_id": s.device_id,
                "data_origin": s.data_origin,
                "session_type": s.session_type,
                "source_system": s.source_system,
                "external_session_id": s.external_session_id,
                "status": s.status,
                "ended_reason": s.ended_reason,
                "started_at_utc": _utc_iso(s.started_at),
                "ended_at_utc": _utc_iso(s.ended_at),
                "created_at_utc": _utc_iso(s.created_at),
                "updated_at_utc": _utc_iso(s.updated_at),
                "participant_code": participant.participant_code if participant else None,
            }
        )
    return rows


def _assessment_rows(
    db: DBSession,
    assessments: List[ResearchAssessment],
    participants: List[ResearchParticipant],
) -> List[Dict[str, Any]]:
    from app.models.external_result_snapshot import ExternalResultSnapshot

    participant_map = {p.participant_id: p for p in participants}
    group_ids = {a.group_id for a in assessments if a.group_id}
    names = {
        g.group_id: g.name
        for g in (
            db.query(ResearchGroup).filter(ResearchGroup.group_id.in_(group_ids)).all()
            if group_ids
            else []
        )
    }
    snapshot_ids = {a.current_snapshot_id for a in assessments if a.current_snapshot_id}
    snapshots = {
        s.snapshot_id: s
        for s in (
            db.query(ExternalResultSnapshot)
            .filter(ExternalResultSnapshot.snapshot_id.in_(snapshot_ids))
            .all()
            if snapshot_ids
            else []
        )
    }
    rows = []
    for a in assessments:
        participant = participant_map.get(a.participant_id)
        snapshot = snapshots.get(a.current_snapshot_id or "")
        rows.append(
            {
                "assessment_id": a.assessment_id,
                "project_id": a.project_id,
                "participant_id": a.participant_id,
                "participant_code": participant.participant_code if participant else None,
                "group_id": a.group_id,
                "group_name": names.get(a.group_id or ""),
                "session_id": a.session_id,
                "questionnaire_code": a.questionnaire_code,
                "questionnaire_version": a.questionnaire_version,
                "scale_name": a.scale_name,
                "assessment_type": a.assessment_type,
                "assessment_status": a.assessment_status,
                "current_snapshot_id": a.current_snapshot_id,
                "result_version": a.result_version,
                "total_score": a.total_score,
                "risk_level": snapshot.risk_level if snapshot else None,
                "level_code": snapshot.level_code if snapshot else None,
                "reported_at_utc": _utc_iso(a.reported_at),
                "source": a.source,
                "created_at_utc": _utc_iso(a.created_at),
                "updated_at_utc": _utc_iso(a.updated_at),
            }
        )
    return rows


def _item_rows(
    db: DBSession,
    assessments: List[ResearchAssessment],
    participants: List[ResearchParticipant],
) -> List[Dict[str, Any]]:
    assessment_ids = [a.assessment_id for a in assessments]
    if not assessment_ids:
        return []
    participant_map = {p.participant_id: p for p in participants}
    assessment_map = {a.assessment_id: a for a in assessments}
    items = (
        db.query(ResearchAssessmentItem)
        .filter(ResearchAssessmentItem.assessment_id.in_(assessment_ids))
        .order_by(
            ResearchAssessmentItem.assessment_id.asc(),
            ResearchAssessmentItem.result_version.asc(),
            ResearchAssessmentItem.question_index.asc(),
        )
        .all()
    )
    rows = []
    for item in items:
        assessment = assessment_map.get(item.assessment_id)
        participant = (
            participant_map.get(assessment.participant_id) if assessment else None
        )
        rows.append(
            {
                "assessment_id": item.assessment_id,
                "participant_id": assessment.participant_id if assessment else None,
                "participant_code": participant.participant_code if participant else None,
                "session_id": assessment.session_id if assessment else None,
                "questionnaire_code": (
                    assessment.questionnaire_code if assessment else None
                ),
                "result_version": item.result_version,
                "question_id": item.question_id,
                "question_index": item.question_index,
                "question_type": item.question_type,
                "answer_value": item.answer_value,
                "score": item.score,
                "max_score": item.max_score,
                "response_ms": item.response_ms,
                "answered_at_utc": _utc_iso(item.answered_at),
                "source": item.source,
            }
        )
    return rows


def _expression_summary_rows(
    db: DBSession,
    sessions: List[SessionModel],
    participants: List[ResearchParticipant],
) -> List[Dict[str, Any]]:
    session_ids = [s.session_id for s in sessions]
    if not session_ids:
        return []
    participant_map = {p.participant_id: p for p in participants}
    rows = (
        db.query(EmotionResult)
        .filter(EmotionResult.session_id.in_(session_ids))
        .order_by(EmotionResult.id.asc())
        .all()
    )
    out = []
    for row in rows:
        session = next((s for s in sessions if s.session_id == row.session_id), None)
        participant = participant_map.get(session.participant_id or "")
        out.append(
            {
                "session_id": row.session_id,
                "participant_id": session.participant_id if session else None,
                "participant_code": participant.participant_code if participant else None,
                "dominant_expression": row.dominant_expression,
                "valid_expression_seconds": row.valid_expression_seconds,
                "average_confidence": row.average_confidence,
                "face_coverage_percent": row.face_coverage_percent,
                "created_at_utc": _utc_iso(row.created_at),
                "updated_at_utc": _utc_iso(row.updated_at),
                "result_json": row.result_json,
            }
        )
    return out


def _expression_timeline_rows(
    db: DBSession,
    sessions: List[SessionModel],
    participants: List[ResearchParticipant],
    limit: Optional[int] = None,
) -> Iterable[Dict[str, Any]]:
    session_ids = [s.session_id for s in sessions]
    if not session_ids:
        return []
    participant_map = {p.participant_id: p for p in participants}
    session_map = {s.session_id: s for s in sessions}
    query = (
        db.query(EmotionRecord)
        .filter(EmotionRecord.session_id.in_(session_ids))
        .order_by(EmotionRecord.id.asc())
    )
    if limit:
        query = query.limit(limit)

    def generator() -> Iterable[Dict[str, Any]]:
        for row in query.yield_per(1000):
            session = session_map.get(row.session_id)
            participant = participant_map.get(session.participant_id or "") if session else None
            yield {
                "session_id": row.session_id,
                "participant_id": session.participant_id if session else None,
                "participant_code": participant.participant_code if participant else None,
                "device_id": row.device_id,
                "stream_id": row.stream_id,
                "relative_seconds": row.relative_seconds,
                "expression": row.expression,
                "confidence": row.confidence,
                "quality": row.quality,
                "device_timestamp": row.device_timestamp,
                "monotonic_ms": row.monotonic_ms,
                "frame_index": row.frame_index,
                "created_at_utc": _utc_iso(row.created_at),
            }

    return generator()


def _conversation_rows(
    db: DBSession,
    sessions: List[SessionModel],
    participants: List[ResearchParticipant],
) -> List[Dict[str, Any]]:
    session_ids = [s.session_id for s in sessions]
    if not session_ids:
        return []
    conversations = (
        db.query(ResearchConversation)
        .filter(ResearchConversation.session_id.in_(session_ids))
        .order_by(ResearchConversation.id.asc())
        .all()
    )
    conversation_ids = [c.conversation_id for c in conversations]
    messages = {
        c.conversation_id: []
        for c in conversations
    }
    if conversation_ids:
        for message in (
            db.query(ResearchConversationMessage)
            .filter(
                ResearchConversationMessage.conversation_id.in_(conversation_ids)
            )
            .order_by(
                ResearchConversationMessage.conversation_id.asc(),
                ResearchConversationMessage.seq.asc(),
            )
            .all()
        ):
            messages[message.conversation_id].append(message)
    participant_map = {p.participant_id: p for p in participants}
    rows = []
    for conversation in conversations:
        participant = participant_map.get(conversation.participant_id or "")
        base = {
            "conversation_id": conversation.conversation_id,
            "project_id": conversation.project_id,
            "participant_id": conversation.participant_id,
            "participant_code": participant.participant_code if participant else None,
            "session_id": conversation.session_id,
            "assessment_id": conversation.assessment_id,
            "external_conversation_id": conversation.external_conversation_id,
            "source": conversation.source,
        }
        for message in messages.get(conversation.conversation_id, []):
            row = dict(base)
            row.update(
                {
                    "seq": message.seq,
                    "role": message.role,
                    "content": message.content,
                    "content_type": message.content_type,
                    "model": message.model,
                    "token_count": message.token_count,
                    "occurred_at_utc": _utc_iso(message.occurred_at),
                }
            )
            rows.append(row)
    return rows


def _device_rows(db: DBSession, sessions: List[SessionModel]) -> List[Dict[str, Any]]:
    device_ids = {s.device_id for s in sessions if s.device_id}
    if not device_ids:
        return []
    devices = (
        db.query(Device).filter(Device.device_id.in_(list(device_ids))).all()
    )
    return [
        {
            "device_id": d.device_id,
            "device_name": d.device_name,
            "device_type": d.device_type,
            "manufacturer": d.manufacturer,
            "model": d.model,
            "firmware_version": d.firmware_version,
        }
        for d in devices
    ]


# ---------------------------------------------------------------------------
# 写文件
# ---------------------------------------------------------------------------
def _write_csv(path: Path, fields: List[str], rows: Iterable[Dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            clean = {}
            for field in fields:
                value = row.get(field)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                clean[field] = value
            writer.writerow(clean)
            count += 1
    return count


def _write_json(path: Path, rows: List[Dict[str, Any]]) -> int:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    return len(rows)


def _write_dictionary(
    path: Path, data_types: Sequence[str], format_name: str
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "file",
                "column",
                "type",
                "description",
                "source_table_column",
                "available",
                "note",
            ]
        )
        for data_type in data_types:
            extension = "json" if format_name == "json" else "csv"
            for column, dtype, desc, source, available, note in DICTIONARY[data_type]:
                writer.writerow(
                    [
                        f"{data_type}.{extension}",
                        column,
                        dtype,
                        desc,
                        source,
                        "true" if available else "false",
                        note,
                    ]
                )


def _zip_dir(work_dir: Path, zip_path: Path) -> int:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(work_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(work_dir).as_posix())
    return zip_path.stat().st_size


# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------
def run_job(export_id: str) -> None:
    db = SessionLocal()
    try:
        job = (
            db.query(ResearchExportJob)
            .filter(ResearchExportJob.export_id == export_id)
            .first()
        )
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.started_at = utcnow()
        db.commit()
        try:
            counts, zip_path, file_size, sha = _build_package(db, job)
            job.status = "succeeded"
            job.counts_json = counts
            job.file_path = str(zip_path)
            job.file_name = zip_path.name
            job.file_size = file_size
            job.sha256 = sha
            job.progress_current = sum(counts.values())
            job.progress_total = job.progress_current
            job.finished_at = utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001 - 任务失败必须落库
            db.rollback()
            job = (
                db.query(ResearchExportJob)
                .filter(ResearchExportJob.export_id == export_id)
                .first()
            )
            if job is not None:
                job.status = "failed"
                job.error_code = getattr(exc, "code", "EXPORT_FAILED")
                job.error_message = str(exc)[:500]
                job.finished_at = utcnow()
                db.commit()
    finally:
        db.close()


class ExportTooLarge(Exception):
    code = "EXPORT_TOO_LARGE"


def _count_rows(db: DBSession, filters: Dict[str, Any], data_types: Sequence[str]) -> Dict[str, int]:
    project_id = filters["project_id"]
    participants = _select_participants(db, project_id, filters)
    participant_ids = [p.participant_id for p in participants]
    sessions = _select_sessions(db, project_id, participant_ids)
    session_ids = [s.session_id for s in sessions]
    assessments = _select_assessments(
        db, project_id, participant_ids, filters.get("assessment_scope") or {}
    )
    counts: Dict[str, int] = {}
    if "participants" in data_types:
        counts["participants"] = len(participants)
    if "sessions" in data_types:
        counts["sessions"] = len(sessions)
    if "assessments" in data_types:
        counts["assessments"] = len(assessments)
    if "assessment_items" in data_types:
        assessment_ids = [a.assessment_id for a in assessments]
        counts["assessment_items"] = (
            db.query(func.count(ResearchAssessmentItem.id))
            .filter(ResearchAssessmentItem.assessment_id.in_(assessment_ids))
            .scalar()
            if assessment_ids
            else 0
        ) or 0
    if "expression_summary" in data_types:
        counts["expression_summary"] = (
            db.query(func.count(EmotionResult.id))
            .filter(EmotionResult.session_id.in_(session_ids))
            .scalar()
            if session_ids
            else 0
        ) or 0
    if "expression_timeline" in data_types:
        counts["expression_timeline"] = (
            db.query(func.count(EmotionRecord.id))
            .filter(EmotionRecord.session_id.in_(session_ids))
            .scalar()
            if session_ids
            else 0
        ) or 0
    if "conversation" in data_types:
        conversation_ids = [
            c.conversation_id
            for c in db.query(ResearchConversation.conversation_id)
            .filter(ResearchConversation.session_id.in_(session_ids))
            .all()
        ] if session_ids else []
        counts["conversation"] = (
            db.query(func.count(ResearchConversationMessage.id))
            .filter(
                ResearchConversationMessage.conversation_id.in_(conversation_ids)
            )
            .scalar()
            if conversation_ids
            else 0
        ) or 0
    if "devices" in data_types:
        counts["devices"] = len({s.device_id for s in sessions if s.device_id})
    return counts


def _build_package(
    db: DBSession, job: ResearchExportJob
) -> Tuple[Dict[str, int], Path, int, str]:
    filters = dict(job.filters_json or {})
    filters["project_id"] = job.project_id
    data_types = list(filters.get("data_types") or DATA_TYPES)
    format_name = filters.get("format") or "csv"
    counts = _count_rows(db, filters, data_types)
    total_rows = sum(counts.values())
    if total_rows > MAX_ROWS:
        raise ExportTooLarge(
            f"EXPORT_TOO_LARGE: {total_rows} rows exceeds limit {MAX_ROWS}"
        )

    project = (
        db.query(ResearchProject)
        .filter(ResearchProject.project_id == job.project_id)
        .first()
    )
    participants = _select_participants(db, job.project_id, filters)
    participant_ids = [p.participant_id for p in participants]
    sessions = _select_sessions(db, job.project_id, participant_ids)
    assessments = _select_assessments(
        db,
        job.project_id,
        participant_ids,
        filters.get("assessment_scope") or {},
    )

    work_dir = Path(tempfile.mkdtemp(prefix=f"{job.export_id}_", dir=_export_root()))
    files: List[Dict[str, Any]] = []
    try:
        datasets: Dict[str, Iterable[Dict[str, Any]]] = {}
        if "participants" in data_types:
            datasets["participants"] = _participant_rows(db, participants)
        if "sessions" in data_types:
            datasets["sessions"] = _session_rows(db, sessions, participants)
        if "assessments" in data_types:
            datasets["assessments"] = _assessment_rows(db, assessments, participants)
        if "assessment_items" in data_types:
            datasets["assessment_items"] = _item_rows(db, assessments, participants)
        if "expression_summary" in data_types:
            datasets["expression_summary"] = _expression_summary_rows(
                db, sessions, participants
            )
        if "expression_timeline" in data_types:
            datasets["expression_timeline"] = _expression_timeline_rows(
                db, sessions, participants
            )
        if "conversation" in data_types:
            datasets["conversation"] = _conversation_rows(db, sessions, participants)
        if "devices" in data_types:
            datasets["devices"] = _device_rows(db, sessions)

        for name, rows in datasets.items():
            fields = FIELDS[name]
            if format_name in ("csv", "both"):
                path = work_dir / f"{name}.csv"
                written = _write_csv(path, fields, rows)
                files.append(
                    {
                        "name": path.name,
                        "size": path.stat().st_size,
                        "sha256": _sha256_file(path),
                        "rows": written,
                    }
                )
                if format_name == "both":
                    # CSV 已消费迭代器，json/both 需重新取数
                    rows = _rows_for_json(
                        db, name, filters, participants, sessions, assessments
                    )
            if format_name in ("json", "both"):
                path = work_dir / f"{name}.json"
                written = _write_json(path, list(rows))
                files.append(
                    {
                        "name": path.name,
                        "size": path.stat().st_size,
                        "sha256": _sha256_file(path),
                        "rows": written,
                    }
                )

        _write_dictionary(work_dir / "data_dictionary.csv", data_types, format_name)

        missing_data = [
            {"dataset": name, "column": column, "note": note}
            for name in data_types
            for column, _dtype, _desc, _source, available, note in DICTIONARY[name]
            if not available
        ]
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "data_dictionary_version": DATA_DICTIONARY_VERSION,
            "export_id": job.export_id,
            "project": {
                "project_id": project.project_id if project else job.project_id,
                "name": project.name if project else None,
                "code": project.code if project else None,
            },
            "generated_at_utc": _utc_iso(job.started_at or utcnow()),
            "timezone": "UTC",
            "filters": filters,
            "counts": counts,
            "files": files + [
                {
                    "name": "data_dictionary.csv",
                    "size": (work_dir / "data_dictionary.csv").stat().st_size,
                    "sha256": _sha256_file(work_dir / "data_dictionary.csv"),
                    "rows": sum(
                        len(DICTIONARY[name]) for name in data_types
                    ),
                }
            ],
            "missing_data": missing_data,
            "generator": "psychology-server research package builder",
        }
        metadata_path = work_dir / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        zip_path = _export_root() / f"{job.export_id}.zip"
        file_size = _zip_dir(work_dir, zip_path)
        if file_size > MAX_BYTES:
            zip_path.unlink(missing_ok=True)
            raise ExportTooLarge(
                f"EXPORT_TOO_LARGE: {file_size} bytes exceeds limit {MAX_BYTES}"
            )
        sha = _sha256_file(zip_path)
        return counts, zip_path, file_size, sha
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _rows_for_json(
    db: DBSession,
    name: str,
    filters: Dict[str, Any],
    participants: List[ResearchParticipant],
    sessions: List[SessionModel],
    assessments: List[ResearchAssessment],
) -> List[Dict[str, Any]]:
    if name == "participants":
        return _participant_rows(db, participants)
    if name == "sessions":
        return _session_rows(db, sessions, participants)
    if name == "assessments":
        return _assessment_rows(db, assessments, participants)
    if name == "assessment_items":
        return _item_rows(db, assessments, participants)
    if name == "expression_summary":
        return _expression_summary_rows(db, sessions, participants)
    if name == "expression_timeline":
        return list(_expression_timeline_rows(db, sessions, participants))
    if name == "conversation":
        return _conversation_rows(db, sessions, participants)
    if name == "devices":
        return _device_rows(db, sessions)
    return []


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_file(db: DBSession, export_id: str) -> Path:
    job = (
        db.query(ResearchExportJob)
        .filter(ResearchExportJob.export_id == export_id)
        .first()
    )
    if job is None:
        raise ValueError(f"export job {export_id} not found")
    if job.status != "succeeded":
        raise ValueError(f"EXPORT_NOT_READY: status={job.status}")
    if job.expires_at and job.expires_at < utcnow():
        job.status = "expired"
        db.commit()
        raise ValueError("EXPORT_EXPIRED: download link has expired")
    if not job.file_path or not Path(job.file_path).is_file():
        raise ValueError("EXPORT_FILE_MISSING: package is no longer available")
    return Path(job.file_path)
