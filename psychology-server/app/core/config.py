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

    # 启动时校验 alembic_version == 代码 head（默认开启）；仅测试/离线工具可关闭
    skip_migration_check: bool = False

    secret_key: str = "change-me-to-a-random-string"

    # 唯一 API 密钥（X-Api-Key，P0-17 / AC-15 / §17.4 I5/I9）；
    # 查询、科研导出与设备管理接口共用；生产必须在 .env 覆盖，不落库、不进日志。
    # X-App-Key 仅为兼容别名，取值与本项相同。
    query_api_key: str = "change-me-query-key"
    # 受试者脱敏标识 HMAC 盐（P0-9 / M7）；生产必须在 .env 覆盖
    research_salt: str = "change-me-research-salt"

    log_level: str = "INFO"
    log_file: str = "logs/app.log"

    # 科研导出文件目录（Q-6/Q-7）；导出物含派生时间，不回写数据库
    export_dir: str = "exports"

    # 心跳超时（秒），超过则视为 offline
    device_offline_after_seconds: int = 30

    # 实时表情状态过期阈值（秒），超过则查询结果标记 stale=true
    emotion_status_stale_seconds: int = 10

    # 僵尸会话：设备心跳消失超过该秒数，未完成会话自动置 interrupted
    session_interrupt_after_seconds: int = 120
    # 后台扫描间隔（秒）
    session_reaper_interval_seconds: int = 60

    # 会话锚点不确定度（毫秒，§8.2 默认 5000）
    anchor_uncertainty_ms: int = 5000
    # 设备轮询间隔（秒），随 active-session 下发
    poll_interval_seconds: int = 3
    # 断网补传窗口（秒，默认 24h），会话创建时计算 late_upload_deadline
    session_late_upload_window_seconds: int = 86400

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
