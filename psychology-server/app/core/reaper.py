"""僵尸会话后台回收线程。

每 interval 秒扫描一次：设备心跳消失超过阈值的未完成会话置 interrupted。
不依赖外部请求，无需 Redis / cron；随 FastAPI 生命周期启停。
"""
import threading
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.services import session_service

logger = get_logger(__name__)


class SessionReaper:
    """守护线程：周期性回收僵尸会话。"""

    def __init__(self, run_immediately: bool = True) -> None:
        settings = get_settings()
        self._interval = settings.session_reaper_interval_seconds
        self._stop = threading.Event()
        self._run_immediately = run_immediately
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="session-reaper", daemon=True
        )
        self._thread.start()
        logger.info(
            "session reaper started: interval=%ds interrupt_after=%ds",
            self._interval,
            get_settings().session_interrupt_after_seconds,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("session reaper stopped")

    def _loop(self) -> None:
        if self._run_immediately:
            self._sweep()
        while not self._stop.wait(self._interval):
            self._sweep()

    @staticmethod
    def _sweep() -> None:
        db = SessionLocal()
        try:
            interrupted = session_service.reap_stale_sessions(db)
            if interrupted:
                logger.info(
                    "reaper interrupted %d stale session(s): %s",
                    len(interrupted),
                    ", ".join(interrupted[:5]),
                )
        except Exception:
            logger.exception("reaper sweep failed")
        finally:
            db.close()
