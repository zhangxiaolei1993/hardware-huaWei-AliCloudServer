"""启动自检：数据库迁移版本必须等于代码中的 alembic head。

防止「代码已更新、数据库未迁移」的漂移：lifespan 在 `init_db()` 之前调用
``assert_migration_head()``，版本不一致时抛错，服务拒绝启动。

也可作为部署前检查单独执行（单次只读查询，不改库）：

    python -m app.db.migration_check
"""
import sys
from pathlib import Path
from typing import Optional

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger

logger = get_logger(__name__)

# app/db/migration_check.py -> 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MigrationVersionError(RuntimeError):
    """数据库版本与代码 head 不一致（或无法判定）。"""


def expected_head() -> str:
    """从 migrations/ 目录解析当前 head revision。"""
    cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if not head:
        raise MigrationVersionError("migrations/ 下找不到 head revision，无法校验")
    return head


def current_revision(engine: Engine) -> Optional[str]:
    """读取库中的 alembic_version；表不存在或为空返回 None。"""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    except SQLAlchemyError:
        return None
    return row[0] if row else None


def assert_migration_head(engine: Engine) -> str:
    """断言库版本 == 代码 head，不满足则抛 MigrationVersionError。"""
    head = expected_head()
    current = current_revision(engine)
    if current != head:
        raise MigrationVersionError(
            "数据库迁移版本与代码不一致："
            f"库={current or '(无 alembic_version 表或为空)'}，代码 head={head}。"
            "请先执行 `alembic upgrade head`（必要时按 §20.3.1 先 stamp 基线）再启动。"
        )
    logger.info("migration check ok: %s", current)
    return current


def main() -> int:
    from app.db.database import engine

    try:
        assert_migration_head(engine)
    except MigrationVersionError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] alembic_version = {current_revision(engine)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
