"""会话相关 Schema。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64, examples=["atlas_001"])
    user_id: Optional[str] = Field(default=None, max_length=64)
    # 当前只允许 emotion；未来新增业务时在此扩展
    session_type: Literal["emotion"] = "emotion"
    # 手机端鉴权：connect 成功后返回的 connection_id；与设备 token 二选一
    connection_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="手机端使用：connect 返回的有效 connection_id；设备端可留空改用请求头 token",
    )


class SessionCreatedResponse(BaseModel):
    session_id: str
    status: str


class ActiveSessionResponse(BaseModel):
    """设备查询当前活跃会话（免认证）；无活跃会话时 session_id 为 None。"""

    device_id: str
    session_id: Optional[str] = None
    session_type: Optional[str] = None
    status: Optional[str] = None


class SessionStatusUpdateRequest(BaseModel):
    status: Literal["running", "completed"]


class SessionOut(BaseModel):
    session_id: str
    device_id: str
    user_id: Optional[str] = None
    session_type: str
    status: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
