"""科研导出服务（v1.4 §12.5 Q-5/Q-6/Q-7、§8.5 派生时间）。

硬规则（AC-35）：
- `event_timestamp` 只在导出物里由 `anchor_utc_at + relative_seconds` 派生，标 `estimated`；
- `quality_info.derived_time` 只出现在导出物中，**绝不回写数据库**；
- 数据库里的 `quality` / `quality_info` 一次写入后不再变更。
"""
import csv
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.db.database import utcnow
from app.models.device import Device
from app.models.emotion_record import EmotionRecord
from app.models.emotion_result import EmotionResult
from app.models.external_result_snapshot import ExternalResultSnapshot
from app.models.session import SessionModel
from app.models.session_device import SessionDevice
from app.models.timeline_event import TimelineEvent

PROTOCOL_VERSION = "1.0"
DEFAULT_STREAM_ID = "facial_expression"
DEFAULT_MODALITY = "facial_expression"

_EXPORT_ID_RE = re.compile(r"^exp_[0-9a-f]{32}$")

_CSV_COLUMNS = (
    "relative_seconds",
    "event_timestamp",
    "event_timestamp_estimated",
    "expression",
    "confidence",
    "stream_id",
    "device_id",
    "quality",
    "derived_time_source",
    "anchor_uncertainty_ms",
)


class ExportError(Exception):
    """导出业务异常：由 API 层转换为 HTTP 错误。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _iso(dt: Optional[datetime]) -> Optional[str]:
    """naive UTC → ISO8601 带 Z（毫秒精度）。"""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _anchor(session: SessionModel) -> Tuple[datetime, int, str]:
    """返回 (锚点, 不确定度ms, 来源)。P0 锚点缺省为服务器创建时刻。"""
    anchor = session.anchor_utc_at or session.started_at or session.created_at
    uncertainty = session.anchor_uncertainty_ms or get_settings().anchor_uncertainty_ms
    source = session.anchor_source or "server"
    return anchor, int(uncertainty), source


def derived_time_info(session: SessionModel) -> Dict[str, Any]:
    """导出物中的派生时间说明（不落库）。"""
    _, uncertainty, _ = _anchor(session)
    return {
        "source": "server_anchor",
        "method": "anchor_plus_relative",
        "anchor_uncertainty_ms": uncertainty,
        "estimated": True,
        "note": "仅导出派生，不回写；不改本条 quality",
    }


def derive_event_timestamp(session: SessionModel, relative_seconds: float) -> str:
    anchor, _, _ = _anchor(session)
    return _iso(anchor + timedelta(seconds=float(relative_seconds)))


def _export_dir() -> str:
    path = get_settings().export_dir
    os.makedirs(path, exist_ok=True)
    return path


def _records(
    db: DBSession,
    session_id: str,
    from_seconds: Optional[float],
    to_seconds: Optional[float],
) -> List[EmotionRecord]:
    query = db.query(EmotionRecord).filter(EmotionRecord.session_id == session_id)
    if from_seconds is not None:
        query = query.filter(EmotionRecord.relative_seconds >= from_seconds)
    if to_seconds is not None:
        query = query.filter(EmotionRecord.relative_seconds <= to_seconds)
    return query.order_by(EmotionRecord.relative_seconds.asc()).all()


def _sha256_records(records: List[EmotionRecord]) -> str:
    """对关键字段做规范化序列化后取 sha256，供 manifest 完整性校验。"""
    digest = hashlib.sha256()
    for r in records:
        digest.update(
            "|".join(
                (
                    r.session_id or "",
                    f"{r.relative_seconds:.6f}",
                    r.expression or "",
                    f"{r.confidence:.6f}",
                    r.quality or "",
                )
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _streams(records: List[EmotionRecord]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for r in records:
        stream_id = r.stream_id or DEFAULT_STREAM_ID
        item = grouped.setdefault(
            stream_id,
            {
                "stream_id": stream_id,
                "modality": DEFAULT_MODALITY,
                "record_count": 0,
                "schema_version": r.protocol_version or PROTOCOL_VERSION,
            },
        )
        item["record_count"] += 1
    return list(grouped.values())


def _devices(db: DBSession, session: SessionModel) -> List[Dict[str, Any]]:
    bindings = (
        db.query(SessionDevice)
        .filter(SessionDevice.session_id == session.session_id)
        .all()
    )
    if not bindings:
        device = (
            db.query(Device)
            .filter(Device.device_id == session.device_id)
            .first()
        )
        return [
            {
                "device_id": session.device_id,
                "role": "primary",
                "model": device.model if device else None,
            }
        ]
    result = []
    for binding in bindings:
        device = (
            db.query(Device).filter(Device.device_id == binding.device_id).first()
        )
        result.append(
            {
                "device_id": binding.device_id,
                "role": binding.role,
                "model": device.model if device else None,
            }
        )
    return result


def _duration_seconds(session: SessionModel) -> Optional[float]:
    start = session.started_at or session.created_at
    end = session.ended_at or utcnow()
    if start is None:
        return None
    return round((end - start).total_seconds(), 3)


def build_manifest(db: DBSession, session: SessionModel) -> Dict[str, Any]:
    """构建会话数据清单（Q-5）。派生时间只声明语义，不写库。"""
    records = _records(db, session.session_id, None, None)
    anchor, uncertainty, anchor_source = _anchor(session)

    counts = {
        "timeline_events": db.query(TimelineEvent)
        .filter(TimelineEvent.session_id == session.session_id)
        .count(),
        "emotion_records": len(records),
        "emotion_results": db.query(EmotionResult)
        .filter(EmotionResult.session_id == session.session_id)
        .count(),
        "external_results": db.query(ExternalResultSnapshot)
        .filter(ExternalResultSnapshot.session_id == session.session_id)
        .count(),
        "raw_files": 0,
    }
    quality_counts: Dict[str, int] = {}
    for r in records:
        key = r.quality or "legacy"
        quality_counts[key] = quality_counts.get(key, 0) + 1

    return {
        "session_id": session.session_id,
        "source_system": session.source_system,
        "external_session_id": session.external_session_id,
        "session_type": session.session_type,
        "status": session.status,
        "anchor": {
            "utc_at": _iso(anchor),
            "source": anchor_source,
            "uncertainty_ms": uncertainty,
        },
        "duration_seconds": _duration_seconds(session),
        "devices": _devices(db, session),
        "streams": _streams(records),
        "counts": counts,
        "quality_counts": quality_counts,
        "derived_time": {
            "event_timestamp": "derived",
            "estimated": True,
            "method": "anchor_plus_relative",
            "anchor_uncertainty_ms": uncertainty,
            "note": "派生值只出现在导出物中，不落库、不改记录 quality",
        },
        "integrity": {
            "emotion_records_sha256": _sha256_records(records),
            "raw_files_verified": 0,
            "raw_files_missing": 0,
        },
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": _iso(utcnow()),
    }


def _export_row(session: SessionModel, record: EmotionRecord) -> Dict[str, Any]:
    info = dict(record.quality_info or {})
    info["derived_time"] = derived_time_info(session)
    return {
        "relative_seconds": record.relative_seconds,
        "event_timestamp": derive_event_timestamp(session, record.relative_seconds),
        "event_timestamp_estimated": True,
        "expression": record.expression,
        "confidence": record.confidence,
        "stream_id": record.stream_id or DEFAULT_STREAM_ID,
        "device_id": record.device_id or session.device_id,
        # 原样保留库内质量与标记，导出不得提升
        "quality": record.quality,
        "quality_info": info,
    }


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(_CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            derived = (row.get("quality_info") or {}).get("derived_time") or {}
            writer.writerow(
                {
                    "relative_seconds": row["relative_seconds"],
                    "event_timestamp": row["event_timestamp"],
                    "event_timestamp_estimated": row["event_timestamp_estimated"],
                    "expression": row["expression"],
                    "confidence": row["confidence"],
                    "stream_id": row["stream_id"],
                    "device_id": row["device_id"],
                    "quality": row["quality"],
                    "derived_time_source": derived.get("source"),
                    "anchor_uncertainty_ms": derived.get("anchor_uncertainty_ms"),
                }
            )


def _write_json(path: str, session: SessionModel, rows: List[Dict[str, Any]]) -> None:
    payload = {
        "session_id": session.session_id,
        "source_system": session.source_system,
        "external_session_id": session.external_session_id,
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": _iso(utcnow()),
        "time_semantics": {
            "event_timestamp": "derived(anchor_plus_relative)",
            "estimated": True,
            "note": "派生时间不落库、不改记录 quality（AC-35）",
        },
        "record_count": len(rows),
        "records": rows,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def create_export(db: DBSession, session: SessionModel, req: Any) -> Dict[str, Any]:
    """同步生成导出文件并落 sidecar 元数据（Q-6）。"""
    fmt = getattr(req, "format", "json")
    if fmt not in ("csv", "json"):
        raise ExportError(422, f"unsupported format: {fmt}")

    records = _records(db, session.session_id, req.from_seconds, req.to_seconds)
    rows = [_export_row(session, r) for r in records]

    export_id = f"exp_{uuid.uuid4().hex}"
    file_name = f"{export_id}.{fmt}"
    file_path = os.path.join(_export_dir(), file_name)

    if fmt == "csv":
        _write_csv(file_path, rows)
    else:
        _write_json(file_path, session, rows)

    meta = {
        "export_id": export_id,
        "session_id": session.session_id,
        "status": "completed",
        "format": fmt,
        "record_count": len(rows),
        "file_name": file_name,
        "download_url": f"/api/v1/exports/{export_id}?download=true",
        "manifest_url": f"/api/v1/sessions/{session.session_id}/manifest",
        "created_at": _iso(utcnow()),
        "filters": {
            "from": req.from_seconds,
            "to": req.to_seconds,
            "modalities": req.modalities,
        },
        "derived_time": derived_time_info(session),
        "note": "event_timestamp 为派生估算值；库内 quality 未被修改",
    }
    with open(os.path.join(_export_dir(), f"{export_id}.meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def get_export(db: DBSession, export_id: str) -> Dict[str, Any]:
    """读取导出元数据（Q-7）；文件缺失视为已失效。"""
    if not _EXPORT_ID_RE.match(export_id or ""):
        raise ExportError(404, f"export {export_id} not found")
    base = _export_dir()
    meta_path = os.path.join(base, f"{export_id}.meta.json")
    if not os.path.exists(meta_path):
        raise ExportError(404, f"export {export_id} not found")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    file_path = os.path.join(base, meta["file_name"])
    if not os.path.exists(file_path):
        raise ExportError(410, f"export {export_id} file no longer exists")
    meta["_file_path"] = file_path
    return meta
