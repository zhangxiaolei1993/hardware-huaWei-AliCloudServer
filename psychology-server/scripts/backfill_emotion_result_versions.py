"""P1-D 历史结果版本回填（幂等，默认 dry-run）。

为 ``emotion_results`` 中已有的当前结果补一条 ``emotion_result_versions``
首版记录，使版本追溯从历史数据开始连续：

- 幂等键：``(session_id, source_sha256, algorithm_version)`` 已存在则跳过；
- 源数据按 ``relative_seconds, id`` 升序重建后计算 sha256（与在线重算一致）；
- 只写新表，不改任何 P0 表；回填前后对 P0 表内容做行数与 MD5 校验；
- 默认 dry-run，只有显式 ``--apply`` 才落库。

用法::

    python scripts/backfill_emotion_result_versions.py --db path/to/psychology.db
    python scripts/backfill_emotion_result_versions.py --db copy.db --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas.emotion import TimelineItem  # noqa: E402
from app.services.emotion_service import (  # noqa: E402
    EMOTION_ALGORITHM_VERSION,
    _timeline_sha256,
)

VERSION_TABLE = "emotion_result_versions"
#: 回填前后必须保持不变的 P0 表
P0_TABLES = (
    "devices",
    "sessions",
    "session_devices",
    "emotion_records",
    "emotion_results",
    "timeline_events",
    "external_result_snapshots",
    "upload_batches",
    "device_connections",
    "device_emotion_status",
    "device_audit_events",
)
TRIGGER_BACKFILL = "backfill"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type='table'")
    return {row[0] for row in rows}


def _p0_fingerprint(conn: sqlite3.Connection) -> Dict[str, Any]:
    """P0 表行数 + 内容 MD5（按 rowid 排序，跨表汇总）。"""
    tables = _table_names(conn)
    counts: Dict[str, int] = {}
    digest = hashlib.md5()
    for table in P0_TABLES:
        if table not in tables:
            continue
        rows = conn.execute(f'select * from "{table}" order by rowid').fetchall()
        counts[table] = len(rows)
        digest.update(table.encode("utf-8"))
        for row in rows:
            digest.update(
                json.dumps(list(row), ensure_ascii=False, default=str).encode("utf-8")
            )
    return {"counts": counts, "md5": digest.hexdigest()}


def _source_fingerprint(
    conn: sqlite3.Connection, session_id: str
) -> Tuple[int, str]:
    rows = conn.execute(
        "select relative_seconds, expression, confidence from emotion_records "
        "where session_id = ? order by relative_seconds asc, id asc",
        (session_id,),
    ).fetchall()
    items = [
        TimelineItem(
            relative_seconds=row[0],
            expression=row[1],
            confidence=row[2],
        )
        for row in rows
    ]
    return len(items), _timeline_sha256(items)


def _as_db_time(value: Any) -> str:
    """沿用库内 naive UTC 文本格式；异常时退回当前时间。"""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def backfill(
    conn: sqlite3.Connection,
    *,
    algorithm_version: str = EMOTION_ALGORITHM_VERSION,
    apply: bool = False,
) -> Dict[str, Any]:
    """执行（或演练）回填，返回统计结果。"""
    tables = _table_names(conn)
    if VERSION_TABLE not in tables:
        raise RuntimeError(
            f"{VERSION_TABLE} 不存在，请先执行 `alembic upgrade head`"
        )

    before = _p0_fingerprint(conn)
    results = conn.execute(
        "select id, session_id, result_json, created_at, updated_at "
        "from emotion_results order by id"
    ).fetchall()

    planned: List[Dict[str, Any]] = []
    skipped = 0
    for row in results:
        result_id, session_id, result_json, created_at, updated_at = row
        record_count, sha256 = _source_fingerprint(conn, session_id)

        duplicate = conn.execute(
            "select 1 from emotion_result_versions "
            "where session_id = ? and source_sha256 = ? and algorithm_version = ?",
            (session_id, sha256, algorithm_version),
        ).fetchone()
        if duplicate is not None:
            skipped += 1
            continue

        max_version = conn.execute(
            "select coalesce(max(version), 0) from emotion_result_versions "
            "where session_id = ?",
            (session_id,),
        ).fetchone()[0]
        planned.append(
            {
                "result_id": result_id,
                "session_id": session_id,
                "version": int(max_version) + 1,
                "record_count": record_count,
                "sha256": sha256,
                "result_json": result_json,
                "generated_at": _as_db_time(updated_at or created_at),
            }
        )

    if apply and planned:
        with conn:  # 单事务：任一条失败整体回滚
            for item in planned:
                conn.execute(
                    "update emotion_result_versions set status = 'superseded' "
                    "where session_id = ? and status = 'generated'",
                    (item["session_id"],),
                )
                conn.execute(
                    "insert into emotion_result_versions ("
                    "session_id, result_id, version, algorithm_version, status, "
                    "trigger_source, source_record_count, source_sha256, "
                    "result_json, generated_at, created_at"
                    ") values (?, ?, ?, ?, 'generated', ?, ?, ?, ?, ?, ?)",
                    (
                        item["session_id"],
                        item["result_id"],
                        item["version"],
                        algorithm_version,
                        TRIGGER_BACKFILL,
                        item["record_count"],
                        item["sha256"],
                        item["result_json"],
                        item["generated_at"],
                        item["generated_at"],
                    ),
                )

    after = _p0_fingerprint(conn)
    return {
        "applied": bool(apply),
        "results": len(results),
        "planned": planned,
        "skipped": skipped,
        "p0_before": before,
        "p0_after": after,
        "p0_unchanged": before == after,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="回填 emotion_result_versions（幂等，默认 dry-run）"
    )
    parser.add_argument("--db", required=True, help="SQLite 数据库路径（建议先拷贝副本）")
    parser.add_argument(
        "--algorithm-version",
        default=EMOTION_ALGORITHM_VERSION,
        help=f"算法版本标识（默认 {EMOTION_ALGORITHM_VERSION}）",
    )
    parser.add_argument(
        "--apply", action="store_true", help="实际写入；不加则只演练不落库"
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[FATAL] database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        report = backfill(
            conn,
            algorithm_version=args.algorithm_version,
            apply=args.apply,
        )
    except RuntimeError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.apply:
            conn.rollback()

    mode = "APPLY" if report["applied"] else "DRY-RUN"
    print(
        f"[{mode}] results={report['results']} "
        f"planned={len(report['planned'])} skipped={report['skipped']} "
        f"p0_unchanged={report['p0_unchanged']}"
    )
    for item in report["planned"]:
        print(
            f"  + {item['session_id']} v{item['version']} "
            f"records={item['record_count']} sha256={item['sha256'][:12]}"
        )
    before_md5 = report["p0_before"]["md5"]
    after_md5 = report["p0_after"]["md5"]
    print(f"  P0 md5: {before_md5[:12]} -> {after_md5[:12]}")
    if not report["p0_unchanged"]:
        print("[ERROR] P0 表内容发生变化，请检查", file=sys.stderr)
        return 2
    if not args.apply:
        print("  （dry-run，未写入；确认无误后加 --apply）")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
