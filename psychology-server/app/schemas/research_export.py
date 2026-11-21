"""科研数据导出 API Schema（P1-A 脚本口径的 HTTP 封装）。

字段与 ``scripts/research_export.py`` 的 CLI 参数一一对应，不引入新的导出口径。
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATA_TYPE_CHOICES = ("timeline", "emotion", "results", "snapshots", "batches")
DataTypes = Literal["timeline", "emotion", "results", "snapshots", "batches"]
ExportFormat = Literal["csv", "jsonl", "both"]

DEFAULT_DATA_TYPES: List[str] = list(DATA_TYPE_CHOICES)


def parse_utc(value: str) -> Optional[datetime]:
    """解析 ISO8601（支持 Z / 偏移）；非法返回 None。"""
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


class ResearchSessionItem(BaseModel):
    """会话清单行：只保留导出选号所需字段。"""

    session_id: str
    external_session_id: Optional[str] = None
    source_system: Optional[str] = None
    status: Optional[str] = None
    started_at_bj: Optional[str] = Field(
        default=None, description="开始时间（北京时间 UTC+8，ISO8601）"
    )
    ended_at_bj: Optional[str] = Field(
        default=None, description="结束时间（北京时间 UTC+8，ISO8601）"
    )
    emotion_records: int
    timeline_events: int
    has_result: bool


class ResearchSessionList(BaseModel):
    """会话清单响应。"""

    total: int
    sessions: List[ResearchSessionItem]


class ResearchExportRequest(BaseModel):
    """批量/多会话科研导出请求。

    ``session_ids`` 与 ``export_all`` 二选一；时间范围按会话
    ``coalesce(started_at, created_at)`` 过滤，``from_seconds`` / ``to_seconds``
    按行级 ``relative_seconds`` 过滤（仅 timeline_events / emotion_records）。
    """

    session_ids: List[str] = Field(
        default_factory=list,
        description="会话号 S1 列表；与 export_all=true 二选一",
    )
    export_all: bool = Field(
        default=False, description="导出全部会话（受时间范围约束）"
    )
    from_utc: Optional[str] = Field(
        default=None, description="会话起始时间下界（ISO8601，含）"
    )
    to_utc: Optional[str] = Field(
        default=None, description="会话起始时间上界（ISO8601，含）"
    )
    from_seconds: Optional[float] = Field(
        default=None, description="行级 relative_seconds 下界（含）"
    )
    to_seconds: Optional[float] = Field(
        default=None, description="行级 relative_seconds 上界（含）"
    )
    data_types: List[DataTypes] = Field(
        default_factory=lambda: list(DEFAULT_DATA_TYPES),
        description="数据类型子集：timeline/emotion/results/snapshots/batches；"
        "sessions 与 session_devices 始终导出",
    )
    format: ExportFormat = Field(
        default="both", description="csv / jsonl / both（两者同时输出）"
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_filters(self) -> "ResearchExportRequest":
        if not self.export_all and not self.session_ids:
            raise ValueError("必须提供 session_ids 或 export_all=true")
        if self.export_all and self.session_ids:
            raise ValueError("session_ids 与 export_all 不能同时提供")
        if self.from_utc or self.to_utc:
            lower = parse_utc(self.from_utc) if self.from_utc else None
            upper = parse_utc(self.to_utc) if self.to_utc else None
            if self.from_utc and lower is None:
                raise ValueError("from_utc 不是合法的 ISO8601")
            if self.to_utc and upper is None:
                raise ValueError("to_utc 不是合法的 ISO8601")
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("from_utc 不能晚于 to_utc")
        if (
            self.from_seconds is not None
            and self.to_seconds is not None
            and self.from_seconds > self.to_seconds
        ):
            raise ValueError("from_seconds 不能大于 to_seconds")
        return self
