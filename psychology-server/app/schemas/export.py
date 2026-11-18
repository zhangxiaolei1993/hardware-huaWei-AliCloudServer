"""科研导出 Schema（v1.4 §12.5 Q-5 / Q-6 / Q-7）。"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExportCreateRequest(BaseModel):
    """创建导出请求体：`format` + 可选时间窗（秒，相对锚点）。"""

    format: Literal["csv", "json"] = "json"
    modalities: Optional[List[str]] = None
    from_seconds: Optional[float] = Field(default=None, alias="from")
    to_seconds: Optional[float] = Field(default=None, alias="to")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ExportCreateResponse(BaseModel):
    """导出任务元数据（Q-6 响应 / Q-7 查询同一结构）。"""

    export_id: str
    session_id: str
    status: str
    format: str
    record_count: int
    download_url: str
    manifest_url: str
    created_at: str
    derived_time: dict
    note: str
