"""科研导出 API 服务层：直接复用 ``scripts/research_export.py``。

只做三件事：
1. 解析当前 SQLite 数据库文件路径（导出以只读方式独立连接，不复用 ORM 会话）；
2. 在系统临时目录调用 ``run_export`` 生成同口径的导出物并打包 zip；
3. 返回 zip 路径、manifest 摘要等信息，响应结束后由调用方清理临时目录。

不复制、不改写脚本内的列白名单、隐私裁剪与 manifest 逻辑。
"""
import hashlib
import importlib
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.engine import make_url

from app.core.config import get_settings

_SCRIPT_MODULE = "scripts.research_export"
_script: Any = None


class ResearchExportError(Exception):
    """科研导出业务错误：由 API 层转换为对应 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ResearchExportArtifact:
    """一次导出生成的临时产物（zip + 元数据）。"""

    zip_path: Path
    work_dir: Path
    manifest_sha256: str
    generated_at: str
    session_ids: List[str] = field(default_factory=list)
    total_counts: Dict[str, int] = field(default_factory=dict)
    missing_tables: List[str] = field(default_factory=list)

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


def _load_exporter() -> Any:
    """延迟加载脚本模块，避免启动时因路径问题导致 ImportError。"""
    global _script
    if _script is None:
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            _script = importlib.import_module(_SCRIPT_MODULE)
        except ImportError as exc:  # pragma: no cover - 仅部署缺文件时触发
            raise ResearchExportError(
                f"research export module unavailable: {exc}", status_code=500
            ) from exc
    return _script


def resolve_database_path() -> Path:
    """当前配置对应 SQLite 文件路径（API 导出只读打开该文件）。"""
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite":
        raise ResearchExportError(
            "research export currently supports sqlite only", status_code=501
        )
    if not url.database or url.database == ":memory:":
        raise ResearchExportError(
            "in-memory sqlite cannot be exported", status_code=500
        )
    return Path(url.database).resolve()


def create_research_export(
    *,
    session_ids: Sequence[str] = (),
    export_all: bool = False,
    from_utc: Optional[str] = None,
    to_utc: Optional[str] = None,
    from_seconds: Optional[float] = None,
    to_seconds: Optional[float] = None,
    data_types: Optional[Sequence[str]] = None,
    fmt: str = "both",
) -> ResearchExportArtifact:
    """调用脚本导出口径生成 zip；失败时清理临时目录并抛出业务错误。"""
    exporter = _load_exporter()
    db_path = resolve_database_path()
    if not db_path.exists():
        raise ResearchExportError(f"database not found: {db_path}", status_code=500)

    work_dir = Path(tempfile.mkdtemp(prefix="research_export_"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = work_dir / f"research_export_{stamp}"
    try:
        manifest = exporter.run_export(
            db_path,
            out_dir,
            sessions=list(session_ids),
            export_all=export_all,
            from_utc=from_utc,
            to_utc=to_utc,
            from_seconds=from_seconds,
            to_seconds=to_seconds,
            data_types=list(data_types or exporter._DEFAULT_DATA_TYPES),
            fmt=fmt,
            # 外部 ID 默认不导出；需要时走脚本显式开关
            include_external_ids=False,
            make_zip=True,
            overwrite=False,
        )
    except ValueError as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        message = str(exc)
        status = 404 if "no sessions matched" in message else 400
        raise ResearchExportError(message, status_code=status) from exc
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():  # pragma: no cover - 防御脚本行为变化
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ResearchExportError("export manifest missing", status_code=500)
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    zip_path = Path(str(manifest.get("zip", "")))
    if not zip_path.is_file():  # pragma: no cover - 防御脚本行为变化
        shutil.rmtree(work_dir, ignore_errors=True)
        raise ResearchExportError("export zip missing", status_code=500)

    return ResearchExportArtifact(
        zip_path=zip_path,
        work_dir=work_dir,
        manifest_sha256=manifest_sha256,
        generated_at=str(manifest.get("generated_at", "")),
        session_ids=list(manifest.get("sessions", [])),
        total_counts=dict(manifest.get("total_counts", {})),
        missing_tables=list(manifest.get("missing_tables", [])),
    )
