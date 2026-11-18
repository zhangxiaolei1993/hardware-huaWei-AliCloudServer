"""P0-9 / M7：为历史 sessions 分批回填 subject_ref（默认 dry-run）。

规则：
- 优先 external_user_id；缺失时回退 user_id，并置 deprecated_identity_used=1；
- subject_ref = HMAC_SHA256(RESEARCH_SALT, source_system + ':' + identity)[:16]；
- 只处理 subject_ref IS NULL 的行，可重复执行。

用法：
    python scripts/backfill_subject_ref.py --dry-run
    python scripts/backfill_subject_ref.py --batch-size 500
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import SessionLocal  # noqa: E402
from app.models.session import SessionModel  # noqa: E402
from app.services.session_service import subject_ref_for  # noqa: E402


def run(batch_size: int, dry_run: bool) -> int:
    db = SessionLocal()
    total = 0
    fallback = 0
    try:
        last_id = 0
        while True:
            rows = (
                db.query(SessionModel)
                .filter(
                    SessionModel.subject_ref.is_(None),
                    SessionModel.id > last_id,
                )
                .order_by(SessionModel.id.asc())
                .limit(batch_size)
                .all()
            )
            if not rows:
                break
            for session in rows:
                identity = session.external_user_id or session.user_id
                if not identity:
                    continue
                ref = subject_ref_for(session.source_system, identity)
                used_fallback = session.external_user_id is None
                if used_fallback:
                    fallback += 1
                total += 1
                if dry_run:
                    print(
                        f"[dry-run] {session.session_id} -> {ref} "
                        f"(fallback={used_fallback})"
                    )
                else:
                    session.subject_ref = ref
                    session.deprecated_identity_used = used_fallback
            last_id = rows[-1].id
            if not dry_run:
                db.commit()
    finally:
        db.close()

    action = "would backfill" if dry_run else "backfilled"
    print(f"{action} {total} sessions (fallback user_id: {fallback})")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 sessions.subject_ref")
    parser.add_argument("--dry-run", action="store_true", help="只统计与打印，不写库")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    run(args.batch_size, args.dry_run)
