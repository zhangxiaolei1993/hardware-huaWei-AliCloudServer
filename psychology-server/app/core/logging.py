"""日志配置：控制台 + 文件 + 脱敏（AC-27）。"""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

from app.core.config import get_settings

_CONFIGURED = False

# 敏感内容脱敏（AC-27）：认证头、答案/转写原文等不得进入日志
_REDACTIONS = (
    (re.compile(r"(X-Device-Token\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(X-App-Key\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(X-Api-Key\s*[:=]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(Authorization\s*[:=]\s*(?:Bearer\s+)?)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r'("?(?:device_token|api_key|app_key|query_api_key|research_salt)"?\s*:\s*")[^"]+'),
     r"\1***"),
    (re.compile(r'"?(?:answer_text|answer_value|transcript)"?\s*:\s*(?:"[^"]*"|[-0-9.]+)'),
     '"***"'),
)


class RedactionFilter(logging.Filter):
    """在写入 handler 前对消息做正则脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern, replacement in _REDACTIONS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    log_dir = os.path.dirname(settings.log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    redaction = RedactionFilter()

    root = logging.getLogger()
    root.setLevel(level)
    root.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redaction)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        settings.log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction)
    root.addHandler(file_handler)

    # 降低第三方库噪音
    for name in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
