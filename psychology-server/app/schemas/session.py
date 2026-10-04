"""会话相关 Schema。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64, examples=["atlas_001"])
    user_id: Optional[str] = Field(default=None, max_length=64)
    # 当前只允许 emotion；未来新增业务时在此扩展
    session_type: Literal["emotion"] = "emotion"


class SessionCreatedResponse(BaseModel):
    session_id: str
    status: str


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
