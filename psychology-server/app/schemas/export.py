"""科研导出 Schema（v1.4 §12.5 Q-5 / Q-6 / Q-7）。"""
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExportCreateRequest(BaseModel):
    """创建导出请求体：`format` + 可选时间窗（秒，相对锚点）。"""

    format: Literal["csv", "json"] = Field(
        default="json", examples=["json"], description="导出格式：csv 或 json"
    )
    modalities: Optional[List[str]] = Field(
        default=None, examples=[["emotion"]], description="模态子集；不填＝全部"
    )
    from_seconds: Optional[float] = Field(
        default=None, alias="from", examples=[0.0], description="起始秒数（含）"
    )
    to_seconds: Optional[float] = Field(
        default=None, alias="to", examples=[120.0], description="结束秒数（含）"
    )

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        json_schema_extra={
            "example": {"format": "json", "from": 0.0, "to": 120.0}
        },
    )


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
