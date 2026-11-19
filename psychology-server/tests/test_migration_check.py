"""启动迁移版本断言测试。"""
import pytest
from sqlalchemy import create_engine, text

from app.db.migration_check import (
    MigrationVersionError,
    assert_migration_head,
    current_revision,
    expected_head,
)


def _engine_with_version(tmp_path, version):
    engine = create_engine(f"sqlite:///{tmp_path / 'check.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        if version is not None:
            conn.execute(
                text("INSERT INTO alembic_version VALUES (:v)"), {"v": version}
            )
    return engine


def test_head_matches(tmp_path):
    head = expected_head()
    engine = _engine_with_version(tmp_path, head)
    assert assert_migration_head(engine) == head


def test_version_mismatch_raises(tmp_path):
    engine = _engine_with_version(tmp_path, "0001_baseline")
    with pytest.raises(MigrationVersionError) as exc:
        assert_migration_head(engine)
    assert "alembic upgrade head" in str(exc.value)
    assert current_revision(engine) == "0001_baseline"


def test_missing_table_raises(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with pytest.raises(MigrationVersionError):
        assert_migration_head(engine)
    assert current_revision(engine) is None
