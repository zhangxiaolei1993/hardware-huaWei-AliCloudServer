"""全局配置：从 .env 读取，禁止把密钥写死在代码里。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "psychology-server"
    env: str = "dev"
    host: str = "0.0.0.0"
    port: int = 8000

    # 数据库：SQLite（第一阶段默认）；有 MySQL 时切换 DATABASE_URL 即可
    database_url: str = "sqlite:///./psychology.db"

    secret_key: str = "change-me-to-a-random-string"

    log_level: str = "INFO"
    log_file: str = "logs/app.log"

    # 心跳超时（秒），超过则视为 offline
    device_offline_after_seconds: int = 30

    # 实时表情状态过期阈值（秒），超过则查询结果标记 stale=true
    emotion_status_stale_seconds: int = 10

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
