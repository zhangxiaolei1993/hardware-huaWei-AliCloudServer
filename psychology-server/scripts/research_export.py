#!/usr/bin/env python3
"""P1-A 科研数据统一抽取与导出（只读脚本）。

设计约束（2026-11-19 批准版）：
- 直接以 ``file:...?mode=ro`` 只读打开 SQLite，不经过 API、不写库、不改表；
- 按显式列白名单取数，绝不 ``SELECT *``；事件 ``payload`` 再按事件类型白名单裁剪；
- 原始数据（raw）与分析结果（derived）分目录存放；
- CSV 与 JSONL 同时输出；时间统一 UTC ISO8601，并保留 ``relative_seconds``；
- 派生时间复用 ``app.services.export_service`` 的锚点与换算函数，不另写一套；
- 生成质量报告、数据字典、manifest（sha256/行数/脚本版本/库指纹）。

用法见 ``--help`` 或 ``docs/architecture/P1-A科研数据导出使用说明.md``。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 允许从仓库根目录直接运行：python scripts/research_export.py
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.models.session import SessionModel  # noqa: E402
from app.schemas.event import ALLOWED_PAYLOAD_KEYS  # noqa: E402
from app.services.export_service import (  # noqa: E402
    derive_event_timestamp,
    derived_time_info,
)

SCRIPT_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 列白名单：只导出这里列出的列。新增列必须显式加入并在数据字典中说明。
# 结构：table -> (group, [(column, description), ...])
# group: raw / derived / meta
# ---------------------------------------------------------------------------

_COLUMNS: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {
    "sessions": (
        "meta",
        [
            ("session_id", "服务器会话号 S1（uuid4 hex，多模态关联主键）"),
            ("subject_ref", "受试者脱敏标识 HMAC[:16]，科研关联用"),
            ("deprecated_identity_used", "是否因缺少 external_user_id 回退 user_id 计算"),
            ("session_type", "会话类型（emotion/physiological/...）"),
            ("source_system", "会话来源（standalone/xin_tan_tan/custom）"),
            ("external_session_id", "外部系统会话号 N1（原样保存）"),
            ("client_request_id", "创建幂等键"),
            ("device_id", "主设备 ID"),
            ("anchor_utc_at", "时间锚点 UTC"),
            ("anchor_monotonic_ms", "锚点单调时钟毫秒"),
            ("anchor_source", "锚点来源（server/device）"),
            ("anchor_uncertainty_ms", "锚点不确定度毫秒"),
            ("late_upload_deadline", "迟到上传截止 UTC"),
            ("ended_reason", "结束原因"),
            ("status", "会话状态（created/running/completed/interrupted/aborted）"),
            ("started_at", "开始时间 UTC"),
            ("ended_at", "结束时间 UTC"),
            ("created_at", "创建时间 UTC"),
            ("updated_at", "更新时间 UTC"),
        ],
    ),
    "session_devices": (
        "meta",
        [
            ("session_id", "会话号 S1"),
            ("device_id", "设备 ID"),
            ("role", "角色（primary/auxiliary）"),
            ("status", "绑定状态（bound/unbound）"),
            ("bound_at", "绑定时间 UTC"),
            ("unbound_at", "解绑时间 UTC"),
            ("created_at", "创建时间 UTC"),
            ("updated_at", "更新时间 UTC"),
        ],
    ),
    "upload_batches": (
        "meta",
        [
            ("upload_id", "批次 ID"),
            ("session_id", "会话号 S1"),
            ("device_id", "设备 ID"),
            ("app_client_id", "App 安装实例 ID"),
            ("client_request_id", "批次幂等键"),
            ("batch_type", "批次类型（event/emotion/raw/result）"),
            ("status", "批次状态（open/received/verified/failed）"),
            ("item_count", "条目数"),
            ("total_bytes", "总字节数"),
            ("sha256", "批次摘要"),
            ("error_code", "错误码"),
            ("received_at", "服务端接收时间 UTC"),
            ("created_at", "创建时间 UTC"),
            ("updated_at", "更新时间 UTC"),
        ],
    ),
    "timeline_events": (
        "raw",
        [
            ("event_id", "客户端事件 ID（32 hex）"),
            ("session_id", "会话号 S1"),
            ("device_id", "设备 ID（纯量表事件为空）"),
            ("stream_id", "数据流标识"),
            ("event_type", "事件类型（13 类白名单）"),
            ("source", "来源（device/app/server）"),
            ("occurred_at", "事件发生时间 UTC"),
            ("relative_seconds", "相对锚点秒数（保留负值）"),
            ("device_timestamp", "设备本地时间戳（字符串，原样）"),
            ("monotonic_ms", "设备单调时钟毫秒"),
            ("clock_offset_ms", "设备时钟偏移毫秒"),
            ("time_uncertainty_ms", "时间不确定度毫秒"),
            ("server_received_at", "服务端接收时间 UTC"),
            ("task_id", "任务 ID"),
            ("questionnaire_id", "量表 ID"),
            ("questionnaire_version", "量表版本"),
            ("question_id", "题目 ID"),
            ("question_index", "题目序号"),
            ("duration_ms", "事件持续毫秒"),
            ("payload", "受控载荷（按事件类型白名单裁剪）"),
            ("schema_version", "事件 Schema 版本"),
            ("protocol_version", "协议版本"),
            ("quality", "记录质量（device/estimated/missing/legacy）"),
            ("quality_info", "质量明细（含 flags）"),
            ("client_request_id", "批次幂等键"),
            ("created_at", "入库时间 UTC"),
        ],
    ),
    "emotion_records": (
        "raw",
        [
            ("session_id", "会话号 S1"),
            ("device_id", "设备 ID"),
            ("stream_id", "数据流标识"),
            ("relative_seconds", "相对锚点秒数"),
            ("expression", "表情类别（8 类白名单）"),
            ("confidence", "识别置信度 0~1"),
            ("created_at", "服务端入库时间 UTC"),
            ("device_timestamp", "设备本地时间戳（P0 客户端未填）"),
            ("monotonic_ms", "设备单调时钟毫秒（P0 客户端未填）"),
            ("clock_offset_ms", "设备时钟偏移毫秒"),
            ("frame_index", "帧序号（P0 客户端未填）"),
            ("quality", "记录质量"),
            ("quality_info", "质量明细（含 flags）"),
            ("protocol_version", "协议版本"),
        ],
    ),
    "emotion_results": (
        "derived",
        [
            ("session_id", "会话号 S1"),
            ("client_request_id", "计算批次幂等键"),
            ("valid_expression_seconds", "有效表情累计秒数"),
            ("dominant_expression", "主导表情"),
            ("average_confidence", "平均置信度"),
            ("face_coverage_percent", "人脸覆盖率百分比（服务器按帧重算）"),
            ("result_json", "完整结果明细（expressions/quality/session_start/end）"),
            ("created_at", "结果创建时间 UTC"),
            ("updated_at", "结果更新时间 UTC"),
        ],
    ),
    "emotion_result_versions": (
        "derived",
        [
            ("session_id", "会话号 S1"),
            ("result_id", "对应 emotion_results.id"),
            ("version", "会话内版本号（从 1 单调递增）"),
            ("algorithm_version", "算法口径版本"),
            ("status", "版本状态（generated/superseded）"),
            ("trigger_source", "触发来源（first_import/recompute/manual_recompute/backfill）"),
            ("source_record_count", "生成时源记录数"),
            ("source_sha256", "生成时源数据 sha256（可回溯当时的原始记录）"),
            ("result_json", "该版本完整结果快照"),
            ("generated_at", "版本生成时间 UTC"),
            ("created_at", "入库时间 UTC"),
        ],
    ),
    "external_result_snapshots": (
        "derived",
        [
            ("snapshot_id", "快照 ID"),
            ("session_id", "会话号 S1"),
            ("source_system", "来源系统"),
            ("source_result_id", "来源结果 ID"),
            ("questionnaire_code", "量表编码"),
            ("questionnaire_version", "量表版本"),
            ("scale_algorithm_version", "量表算法版本"),
            ("result_version", "结果版本号（从 1 递增）"),
            ("supersedes_snapshot_id", "被取代的快照 ID"),
            ("status", "结果状态"),
            ("total_score", "总分（聚合，白名单）"),
            ("scoring_mode", "计分模式"),
            ("state_update_status", "状态更新结果"),
            ("scale_name", "量表名称"),
            ("scale_question_count", "题目数"),
            ("dimensions", "维度分（聚合，raw_map_v1）"),
            ("radar_norm", "雷达归一化（radar_norm_v1）"),
            ("risk_level", "风险等级"),
            ("level_code", "等级编码"),
            ("recommendation_code", "建议编码"),
            ("ai_profile_score", "AI 画像分"),
            ("summary_code", "摘要编码"),
            ("trust_level", "信任级别"),
            ("signature", "来源签名"),
            ("signature_algorithm", "签名算法"),
            ("source_payload_sha256", "来源载荷摘要"),
            ("idempotency_key", "幂等键 res:{S1}:{code}:v{n}"),
            ("received_at", "接收时间 UTC"),
            ("created_at", "创建时间 UTC"),
        ],
    ),
}

#: 默认不导出的会话列（隐私）。--include-external-ids 才输出。
_SENSITIVE_SESSION_COLUMNS = ("user_id", "external_user_id")

#: 原始表追加的派生时间列（只出现在导出物，不回写）
_DERIVED_TIME_COLUMNS = [
    ("event_timestamp", "派生事件时间 UTC = 锚点 + relative_seconds"),
    ("event_timestamp_estimated", "派生时间是否估算（恒 true）"),
    ("derived_time_source", "派生来源（server_anchor）"),
    ("anchor_uncertainty_ms", "锚点不确定度毫秒"),
]

#: 输出中禁止出现的字段名（答案原文/逐题分/转写文本）
BANNED_OUTPUT_KEYS = {
    "answer_value",
    "answer_text",
    "selected_option_ids",
    "score_delta",
    "user_name",
    "transcript",
    "speech_text",
    "raw_answer",
    "per_question_score",
    "item_score",
    "question_score",
}

_DATA_TYPES = {
    "timeline": ["timeline_events"],
    "emotion": ["emotion_records"],
    "results": ["emotion_results", "emotion_result_versions"],
    "snapshots": ["external_result_snapshots"],
    "batches": ["upload_batches"],
}
_DEFAULT_DATA_TYPES = list(_DATA_TYPES.keys())

_JSON_COLUMNS = {
    "payload",
    "quality_info",
    "result_json",
    "dimensions",
    "radar_norm",
}

_DT_COLUMNS = {
    "occurred_at",
    "server_received_at",
    "created_at",
    "updated_at",
    "anchor_utc_at",
    "late_upload_deadline",
    "started_at",
    "ended_at",
    "bound_at",
    "unbound_at",
    "received_at",
    "generated_at",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _to_iso(value: Any) -> Optional[str]:
    """SQLite 文本/ datetime → UTC ISO8601（毫秒 + Z）。naive 视为 UTC。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return text
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_dt(value: Any) -> Optional[datetime]:
    iso = _to_iso(value)
    if not iso or not iso.endswith("Z"):
        return None
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _db_fingerprint(db_path: Path) -> Dict[str, Any]:
    stat = db_path.stat()
    return {
        "path": str(db_path),
        "size": stat.st_size,
        "sha256": _sha256_file(db_path),
    }


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_names(conn: sqlite3.Connection) -> set:
    return {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type='table'"
        )
    }


def _sanitize_payload(
    event_type: str, payload: Any
) -> Tuple[Optional[Dict[str, Any]], Dict[str, int]]:
    """按事件类型的 payload 白名单裁剪；返回 (安全载荷, 被丢弃键计数)。"""
    dropped: Dict[str, int] = {}
    if not isinstance(payload, dict):
        if payload is not None:
            dropped["<non_dict>"] = 1
        return None, dropped
    allowed = ALLOWED_PAYLOAD_KEYS.get(event_type, set())
    safe: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in allowed and key not in BANNED_OUTPUT_KEYS:
            safe[key] = value
        else:
            dropped[key] = dropped.get(key, 0) + 1
    return (safe or None), dropped


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def _select_columns(table: str, include_external_ids: bool) -> List[Tuple[str, str]]:
    cols = list(_COLUMNS[table][1])
    if table == "sessions" and include_external_ids:
        insert_at = 2
        cols[insert_at:insert_at] = [
            ("user_id", "用户 ID（--include-external-ids 才导出）"),
            ("external_user_id", "外部受试者 ID（--include-external-ids 才导出）"),
        ]
    return cols


def _fetch_session_ids(
    conn: sqlite3.Connection,
    sessions: Sequence[str],
    export_all: bool,
    from_utc: Optional[str],
    to_utc: Optional[str],
) -> List[str]:
    if export_all:
        sql = "select session_id from sessions"
        params: List[Any] = []
    else:
        placeholders = ",".join("?" for _ in sessions)
        sql = f"select session_id from sessions where session_id in ({placeholders})"
        params = list(sessions)
    if from_utc:
        sql += (" and " if " where " in sql else " where ") + (
            "coalesce(started_at, created_at) >= ?"
        )
        params.append(_naive_utc_text(from_utc))
    if to_utc:
        sql += " and coalesce(started_at, created_at) <= ?"
        params.append(_naive_utc_text(to_utc))
    sql += " order by session_id"
    return [row["session_id"] for row in conn.execute(sql, params)]


def _naive_utc_text(value: str) -> str:
    """ISO8601（可带 Z/偏移）→ 'YYYY-MM-DD HH:MM:SS'，与库内格式比较。"""
    dt = _parse_dt(value)
    if dt is None:
        return value
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fetch_rows(
    conn: sqlite3.Connection,
    table: str,
    session_id: str,
    columns: List[Tuple[str, str]],
    from_seconds: Optional[float],
    to_seconds: Optional[float],
) -> List[sqlite3.Row]:
    names = ", ".join(f'"{name}"' for name, _ in columns)
    where = ["session_id = ?"]
    params: List[Any] = [session_id]
    if table in ("timeline_events", "emotion_records"):
        if from_seconds is not None:
            where.append("relative_seconds >= ?")
            params.append(float(from_seconds))
        if to_seconds is not None:
            where.append("relative_seconds <= ?")
            params.append(float(to_seconds))
        order = "relative_seconds asc, rowid asc"
    elif table == "session_devices":
        order = "role asc, device_id asc"
    elif table == "upload_batches":
        order = "received_at asc, upload_id asc"
    elif table == "external_result_snapshots":
        order = "result_version asc, snapshot_id asc"
    else:
        order = "rowid asc"
    return list(
        conn.execute(
            f'select {names} from "{table}" where {" and ".join(where)} order by {order}',
            params,
        )
    )


# ---------------------------------------------------------------------------
# 行规范化
# ---------------------------------------------------------------------------


def _build_session_model(row: sqlite3.Row) -> SessionModel:
    model = SessionModel()
    model.session_id = row["session_id"]
    model.anchor_utc_at = _parse_dt(row["anchor_utc_at"])
    model.anchor_uncertainty_ms = row["anchor_uncertainty_ms"]
    model.anchor_source = row["anchor_source"]
    model.started_at = _parse_dt(row["started_at"])
    model.ended_at = _parse_dt(row["ended_at"])
    model.created_at = _parse_dt(row["created_at"]) or datetime(1970, 1, 1)
    return model


def _normalize_row(
    table: str,
    row: sqlite3.Row,
    session_model: SessionModel,
    meta: Dict[str, Any],
    dropped: Dict[str, int],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name in row.keys():
        value = row[name]
        if table == "timeline_events" and name == "payload":
            value, drop = _sanitize_payload(
                row["event_type"], _maybe_json(value)
            )
            for key, count in drop.items():
                dropped[key] = dropped.get(key, 0) + count
        if name in _DT_COLUMNS:
            result[name] = _to_iso(value)
        elif name in _JSON_COLUMNS:
            result[name] = value if value is None else _maybe_json(value)
        else:
            result[name] = value
    if table in ("timeline_events", "emotion_records"):
        relative = row["relative_seconds"]
        result["event_timestamp"] = derive_event_timestamp(session_model, relative)
        result["event_timestamp_estimated"] = True
        result["derived_time_source"] = meta["derived_time"]["source"]
        result["anchor_uncertainty_ms"] = meta["derived_time"]["anchor_uncertainty_ms"]
    return result


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


# ---------------------------------------------------------------------------
# 写出
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: List[Dict[str, Any]], columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in columns})


def _write_jsonl(path: Path, rows: List[Dict[str, Any]], columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            ordered = {key: row.get(key) for key in columns}
            fh.write(_json_text(ordered) + "\n")


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return _json_text(value)
    return value


def _write_table(
    base: Path,
    table: str,
    rows: List[Dict[str, Any]],
    columns: List[str],
    fmt: str,
) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    if fmt in ("both", "csv"):
        path = base / f"{table}.csv"
        _write_csv(path, rows, columns)
        files.append({"path": str(path), "rows": len(rows), "sha256": _sha256_file(path)})
    if fmt in ("both", "jsonl"):
        path = base / f"{table}.jsonl"
        _write_jsonl(path, rows, columns)
        files.append({"path": str(path), "rows": len(rows), "sha256": _sha256_file(path)})
    return files


# ---------------------------------------------------------------------------
# 质量报告
# ---------------------------------------------------------------------------


def _quality_report(
    table_rows: Dict[str, List[Dict[str, Any]]],
    session_row: Dict[str, Any],
    anomalies: Dict[str, int],
    dropped: Dict[str, int],
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "session_id": session_row.get("session_id"),
        "tables": {},
        "quality_distribution": {},
        "quality_info_flags": {},
        "time_range": {},
        "anomalies": anomalies,
        "payload_keys_dropped": dropped,
    }
    for table, rows in table_rows.items():
        columns = list(_COLUMNS[table][1])
        names = [name for name, _ in columns]
        filled = {name: 0 for name in names}
        for row in rows:
            for name in names:
                value = row.get(name)
                if value is not None and value != "" and value != []:
                    filled[name] += 1
        report["tables"][table] = {
            "rows": len(rows),
            "fill_rates": {
                name: (round(filled[name] / len(rows), 4) if rows else 0.0)
                for name in names
            },
        }
        if "quality" in names:
            dist: Dict[str, int] = {}
            for row in rows:
                key = str(row.get("quality"))
                dist[key] = dist.get(key, 0) + 1
            report["quality_distribution"][table] = dist
        if "quality_info" in names:
            flags: Dict[str, int] = {}
            for row in rows:
                info = row.get("quality_info")
                if isinstance(info, dict):
                    for flag in info.get("flags") or []:
                        flags[str(flag)] = flags.get(str(flag), 0) + 1
            report["quality_info_flags"][table] = flags
        if "relative_seconds" in names and rows:
            values = [float(row["relative_seconds"]) for row in rows]
            report["time_range"][table] = {
                "relative_seconds_min": min(values),
                "relative_seconds_max": max(values),
            }
    report["time_range"]["session"] = {
        "started_at": session_row.get("started_at"),
        "ended_at": session_row.get("ended_at"),
        "anchor_utc_at": session_row.get("anchor_utc_at"),
        "anchor_uncertainty_ms": session_row.get("anchor_uncertainty_ms"),
    }
    return report


def _anomalies(
    table_rows: Dict[str, List[Dict[str, Any]]],
    session_row: Dict[str, Any],
) -> Dict[str, int]:
    events = table_rows.get("timeline_events", [])
    emotions = table_rows.get("emotion_records", [])
    batches = table_rows.get("upload_batches", [])
    results = table_rows.get("emotion_results", [])

    negatives = sum(
        1 for row in events + emotions if float(row.get("relative_seconds") or 0) < 0
    )
    duration = None
    started = _parse_dt(session_row.get("started_at"))
    ended = _parse_dt(session_row.get("ended_at"))
    if started and ended:
        duration = (ended - started).total_seconds()
    outside = 0
    if duration is not None:
        for row in events + emotions:
            rel = row.get("relative_seconds")
            if rel is not None and float(rel) > duration:
                outside += 1

    event_keys = [
        (row.get("session_id"), row.get("source"), row.get("event_id"))
        for row in events
    ]
    duplicate_event_keys = len(event_keys) - len(set(event_keys))
    empty_event_id = sum(1 for row in events if not row.get("event_id"))

    result_sessions = [row.get("session_id") for row in results]
    duplicate_results = len(result_sessions) - len(set(result_sessions))

    batch_keys = [
        (row.get("session_id"), row.get("client_request_id")) for row in batches
    ]
    duplicate_batches = len(batch_keys) - len(set(batch_keys))

    return {
        "negative_relative_seconds": negatives,
        "outside_session_window": outside,
        "duplicate_event_keys": duplicate_event_keys,
        "empty_event_id": empty_event_id,
        "duplicate_emotion_results": duplicate_results,
        "duplicate_upload_batches": duplicate_batches,
    }


# ---------------------------------------------------------------------------
# 数据字典
# ---------------------------------------------------------------------------


def _data_dictionary_markdown(include_external_ids: bool) -> str:
    lines = [
        "# 科研导出数据字典",
        "",
        f"> 由 `scripts/research_export.py` v{SCRIPT_VERSION} 生成。",
        "> 时间字段均为 UTC ISO8601（毫秒 + `Z`）；`relative_seconds` 为相对会话锚点秒数。",
        "> 嵌套 JSON 字段在 CSV 中以紧凑 JSON 字符串保存，在 JSONL 中保持对象。",
        "> `event_timestamp` 为导出派生值（锚点 + `relative_seconds`），不回写数据库。",
        "",
    ]
    for table, (group, columns) in _COLUMNS.items():
        cols = list(columns)
        if table == "sessions" and include_external_ids:
            cols[2:2] = [
                ("user_id", "用户 ID（--include-external-ids）"),
                ("external_user_id", "外部受试者 ID（--include-external-ids）"),
            ]
        if table == "sessions" and not include_external_ids:
            cols = [c for c in cols if c[0] not in _SENSITIVE_SESSION_COLUMNS]
        lines.append(f"## {table}（{group}）")
        lines.append("")
        lines.append("| 列 | 说明 |")
        lines.append("| --- | --- |")
        for name, desc in cols:
            lines.append(f"| `{name}` | {desc} |")
        if table in ("timeline_events", "emotion_records"):
            for name, desc in _DERIVED_TIME_COLUMNS:
                lines.append(f"| `{name}` | {desc}（导出派生） |")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 导出主流程
# ---------------------------------------------------------------------------


def export_session(
    conn: sqlite3.Connection,
    session_id: str,
    out_dir: Path,
    *,
    data_types: Sequence[str],
    fmt: str = "both",
    from_seconds: Optional[float] = None,
    to_seconds: Optional[float] = None,
    include_external_ids: bool = False,
    db_fingerprint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """导出单场会话，返回该会话的 manifest 字典。"""
    session_cols = _select_columns("sessions", include_external_ids)
    session_rows = list(
        conn.execute(
            f'select {", ".join(c for c, _ in session_cols)} from sessions where session_id = ?',
            (session_id,),
        )
    )
    if not session_rows:
        raise ValueError(f"session not found: {session_id}")
    session_raw = session_rows[0]
    session_model = _build_session_model(session_raw)
    derived_meta = derived_time_info(session_model)
    meta = {
        "derived_time": derived_meta,
        "anchor_uncertainty_ms": derived_meta["anchor_uncertainty_ms"],
    }

    tables = ["sessions", "session_devices"] + [
        table for dtype in data_types for table in _DATA_TYPES[dtype]
    ]
    tables = list(dict.fromkeys(tables))
    # 兼容未执行 P1-D 迁移的旧库副本：缺表则跳过，并在根 manifest 的
    # missing_tables 中体现，不中断整次导出。
    existing_tables = _table_names(conn)
    tables = [table for table in tables if table in existing_tables]

    exported: Dict[str, List[Dict[str, Any]]] = {}
    dropped: Dict[str, int] = {}
    session_meta_row: Dict[str, Any] = {}

    for table in tables:
        columns = _select_columns(table, include_external_ids)
        if table == "sessions":
            rows = session_rows
        else:
            rows = _fetch_rows(
                conn,
                table,
                session_id,
                _COLUMNS[table][1],
                from_seconds,
                to_seconds,
            )
        normalized = [
            _normalize_row(
                table,
                row,
                session_model,
                meta,
                dropped,
            )
            for row in rows
        ]
        if table == "sessions":
            session_meta_row = normalized[0]
        exported[table] = normalized

    session_root = out_dir / "sessions" / session_id
    files: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for table in tables:
        group = _COLUMNS[table][0]
        columns = [name for name, _ in _select_columns(table, include_external_ids)]
        if table in ("timeline_events", "emotion_records"):
            columns += [name for name, _ in _DERIVED_TIME_COLUMNS]
        rows = exported[table]
        counts[table] = len(rows)
        group_dir = session_root / ("meta" if group == "meta" else group)
        written = _write_table(group_dir, table, rows, columns, fmt)
        for item in written:
            item["path"] = str(Path(item["path"]).relative_to(out_dir))
        files.extend(written)

    quality = _quality_report(
        exported,
        session_meta_row,
        _anomalies(exported, session_meta_row),
        dropped,
    )
    quality_path = session_root / "quality_report.json"
    quality_path.write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files.append(
        {
            "path": str(quality_path.relative_to(out_dir)),
            "rows": 1,
            "sha256": _sha256_file(quality_path),
        }
    )

    manifest = {
        "script_version": SCRIPT_VERSION,
        "generated_at": _to_iso(datetime.now(timezone.utc)),
        "db": db_fingerprint or {},
        "session_id": session_id,
        "filters": {
            "from_seconds": from_seconds,
            "to_seconds": to_seconds,
            "data_types": list(data_types),
            "format": fmt,
            "include_external_ids": include_external_ids,
        },
        "counts": counts,
        "files": sorted(files, key=lambda item: item["path"]),
    }
    manifest_path = session_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def run_export(
    db_path: Path,
    out_dir: Path,
    *,
    sessions: Sequence[str] = (),
    export_all: bool = False,
    from_utc: Optional[str] = None,
    to_utc: Optional[str] = None,
    from_seconds: Optional[float] = None,
    to_seconds: Optional[float] = None,
    data_types: Sequence[str] = _DEFAULT_DATA_TYPES,
    fmt: str = "both",
    include_external_ids: bool = False,
    make_zip: Optional[bool] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """批量导出入口；返回根 manifest。"""
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    if not export_all and not sessions:
        raise ValueError("必须指定 --all、--session 或 --sessions-file 之一")
    if out_dir.exists() and any(out_dir.iterdir()) and not overwrite:
        raise FileExistsError(
            f"output dir not empty: {out_dir}（默认不覆盖，使用 --overwrite）"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = _db_fingerprint(db_path)
    conn = _connect_readonly(db_path)
    try:
        existing = _table_names(conn)
        missing = [
            table for table in _COLUMNS if table not in existing
        ]
        session_ids = _fetch_session_ids(
            conn, sessions, export_all, from_utc, to_utc
        )
        if not session_ids:
            raise ValueError("no sessions matched the filters")
        manifests = []
        for session_id in session_ids:
            manifests.append(
                export_session(
                    conn,
                    session_id,
                    out_dir,
                    data_types=data_types,
                    fmt=fmt,
                    from_seconds=from_seconds,
                    to_seconds=to_seconds,
                    include_external_ids=include_external_ids,
                    db_fingerprint=fingerprint,
                )
            )
        # 根级汇总
        index_path = out_dir / "sessions_index.csv"
        _write_csv(
            index_path,
            [
                {
                    "session_id": item["session_id"],
                    "timeline_events": item["counts"].get("timeline_events", 0),
                    "emotion_records": item["counts"].get("emotion_records", 0),
                    "emotion_results": item["counts"].get("emotion_results", 0),
                    "external_result_snapshots": item["counts"].get(
                        "external_result_snapshots", 0
                    ),
                    "upload_batches": item["counts"].get("upload_batches", 0),
                }
                for item in manifests
            ],
            [
                "session_id",
                "timeline_events",
                "emotion_records",
                "emotion_results",
                "external_result_snapshots",
                "upload_batches",
            ],
        )
        dictionary_path = out_dir / "data_dictionary.md"
        dictionary_path.write_text(
            _data_dictionary_markdown(include_external_ids), encoding="utf-8"
        )
        global_quality = {
            "sessions": {
                item["session_id"]: json.loads(
                    (out_dir / "sessions" / item["session_id"] / "quality_report.json")
                    .read_text(encoding="utf-8")
                )
                for item in manifests
            },
            "totals": {
                "sessions": len(manifests),
                **{
                    table: sum(item["counts"].get(table, 0) for item in manifests)
                    for table in _COLUMNS
                },
            },
        }
        quality_path = out_dir / "quality_report.json"
        quality_path.write_text(
            json.dumps(global_quality, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        root_manifest_path = out_dir / "manifest.json"
        root_files = []
        for path in sorted(out_dir.rglob("*")):
            if path.is_file() and path != root_manifest_path:
                root_files.append(
                    {
                        "path": str(path.relative_to(out_dir)),
                        "sha256": _sha256_file(path),
                    }
                )
        root_manifest = {
            "script_version": SCRIPT_VERSION,
            "generated_at": _to_iso(datetime.now(timezone.utc)),
            "db": fingerprint,
            "sessions": [item["session_id"] for item in manifests],
            "missing_tables": missing,
            "filters": {
                "from_utc": from_utc,
                "to_utc": to_utc,
                "from_seconds": from_seconds,
                "to_seconds": to_seconds,
                "data_types": list(data_types),
                "format": fmt,
                "include_external_ids": include_external_ids,
            },
            "total_counts": global_quality["totals"],
            "files": root_files,
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(root_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        conn.close()

    should_zip = make_zip if make_zip is not None else len(session_ids) > 1
    if should_zip:
        zip_base = out_dir.parent / out_dir.name
        shutil.make_archive(str(zip_base), "zip", root_dir=out_dir.parent, base_dir=out_dir.name)
        root_manifest["zip"] = str(zip_base.with_suffix(".zip"))
    return root_manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_export.py",
        description="P1-A 科研数据只读导出：按会话/时间/数据类型筛选，输出 CSV 与 JSONL、质量报告、数据字典与 manifest。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python scripts/research_export.py --db psychology.db --session <S1>\n"
            "  python scripts/research_export.py --db /path/copy.db --all --out exports/research/batch1\n"
            "  python scripts/research_export.py --db copy.db --all --from-utc 2026-11-01T00:00:00Z --data-type timeline --data-type emotion --zip\n"
        ),
    )
    parser.add_argument("--db", default="psychology.db", help="SQLite 路径（默认 ./psychology.db，只读打开）")
    parser.add_argument("--session", action="append", default=[], metavar="S1", help="会话号，可重复")
    parser.add_argument("--sessions-file", help="会话号文件（每行一个）")
    parser.add_argument("--all", action="store_true", help="导出全部会话")
    parser.add_argument("--from-utc", help="会话起始时间下界（ISO8601，按 started_at/created_at）")
    parser.add_argument("--to-utc", help="会话起始时间上界（ISO8601）")
    parser.add_argument("--from", dest="from_seconds", type=float, help="行级 relative_seconds 下界")
    parser.add_argument("--to", dest="to_seconds", type=float, help="行级 relative_seconds 上界")
    parser.add_argument(
        "--data-type",
        action="append",
        choices=list(_DATA_TYPES),
        help="数据类型，可重复（默认全部）：timeline/emotion/results/snapshots/batches",
    )
    parser.add_argument(
        "--format",
        choices=["both", "csv", "jsonl"],
        default="both",
        help="输出格式（默认 both）",
    )
    parser.add_argument("--out", help="输出目录（默认 exports/research/research_export_<UTC 时间>）")
    parser.add_argument("--zip", action="store_true", help="强制打包 zip（多会话默认打包）")
    parser.add_argument("--overwrite", action="store_true", help="允许写入非空输出目录（默认拒绝）")
    parser.add_argument(
        "--include-external-ids",
        action="store_true",
        help="导出 user_id/external_user_id（默认不导出）",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出结果路径")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    sessions = list(args.session)
    if args.sessions_file:
        sessions.extend(
            line.strip()
            for line in Path(args.sessions_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if not (args.all or sessions):
        parser.error("必须指定 --all、--session 或 --sessions-file 之一")

    data_types = args.data_type or _DEFAULT_DATA_TYPES
    out_dir = (
        Path(args.out)
        if args.out
        else Path("exports")
        / "research"
        / f"research_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )

    manifest = run_export(
        Path(args.db),
        out_dir,
        sessions=sessions,
        export_all=args.all,
        from_utc=args.from_utc,
        to_utc=args.to_utc,
        from_seconds=args.from_seconds,
        to_seconds=args.to_seconds,
        data_types=data_types,
        fmt=args.format,
        include_external_ids=args.include_external_ids,
        make_zip=True if args.zip else None,
        overwrite=args.overwrite,
    )
    if args.quiet:
        print(out_dir)
    else:
        print(f"exported sessions: {len(manifest['sessions'])}")
        print(f"output: {out_dir}")
        if manifest.get("zip"):
            print(f"zip: {manifest['zip']}")
        print(f"counts: {manifest['total_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
