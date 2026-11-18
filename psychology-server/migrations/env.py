"""Alembic 环境：连接串从应用配置读取，模型元数据用于 autogenerate 校验。"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 保证可导入 app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.db.database import Base  # noqa: E402
from app.models import (  # noqa: F401,E402
    device,
    device_audit_event,
    device_connection,
    device_emotion_status,
    emotion_record,
    emotion_result,
    external_result_snapshot,
    session,
    session_device,
    timeline_event,
    upload_batch,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 优先级：显式传入的 sqlalchemy.url（测试/脚本）> 应用配置
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
