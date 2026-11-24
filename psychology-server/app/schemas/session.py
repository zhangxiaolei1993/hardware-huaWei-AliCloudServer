"""会话相关 Schema。"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# v1.4 §11.2 / §12.3 S-1：会话类型白名单
SessionType = Literal[
    "emotion", "multimodal", "assessment", "physiological", "speech", "custom"
]
# 来源白名单；custom 保留但客户端提交时拒绝
SourceSystem = Literal["xin_tan_tan", "standalone", "custom"]

# 禁止把兜底串当作外部会话编号（§12.3 S-1）
_FALLBACK_EXTERNAL_IDS = {"unknown", "null", "none", "undefined", "n/a", "na"}


class AnchorOut(BaseModel):
    utc_at: datetime
    source: str
    uncertainty_ms: int


class SessionCreateRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=64, examples=["atlas_001"])
    user_id: Optional[str] = Field(
        default=None, max_length=64, examples=["user_1001"]
    )
    # P0-9：来自服务器 B 的受试者 ID；优先于 user_id 计算 subject_ref
    external_user_id: Optional[str] = Field(
        default=None,
        max_length=64,
        examples=["subject_20261119_001"],
        description="外部系统受试者 ID（来自服务器 B）；服务器据此计算脱敏 subject_ref",
    )
    session_type: SessionType = Field(
        default="emotion", examples=["emotion"], description="会话类型"
    )
    # 不传时按 standalone 处理，保证旧客户端兼容
    source_system: Optional[SourceSystem] = Field(
        default=None,
        examples=["standalone"],
        description="会话来源；不传＝standalone；custom 不接受客户端提交",
    )
    # 外部系统会话编号 N1：只读、原样保存；同一 N1 可关联多个 S1
    external_session_id: Optional[str] = Field(
        default=None,
        max_length=128,
        examples=["xin_tan_tan_20261119_001"],
        description="外部系统会话编号（N1），仅 source_system != standalone 时允许",
    )
    # 创建幂等键：同 source_system + client_request_id 重复请求不重复创建
    client_request_id: Optional[str] = Field(
        default=None, max_length=128, examples=["req-20261119-0001"]
    )
    # 手机端鉴权：connect 成功后返回的 connection_id；与设备 token 二选一
    connection_id: Optional[str] = Field(
        default=None,
        max_length=64,
        examples=["conn_20261119_ab12cd34"],
        description="手机端使用：connect 返回的有效 connection_id；设备端可留空改用请求头 token",
    )
    # 科研版：可选绑定被试；绑定后会话自动继承项目/组别
    participant_id: Optional[str] = Field(
        default=None,
        max_length=40,
        examples=["pt_0123456789abcdef"],
        description="科研被试 ID（可选）；绑定后会话继承被试的项目与组别",
    )

    @field_validator("external_session_id")
    @classmethod
    def _reject_fallback_external_id(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            if not value.strip() or value.strip().lower() in _FALLBACK_EXTERNAL_IDS:
                raise ValueError(
                    "INVALID_EXTERNAL_SESSION_ID: 不得使用空值或 unknown/null 等兜底串"
                )
        return value


class SessionCreatedResponse(BaseModel):
    """创建会话响应。

    `status` 为会话状态；`created=False` 表示幂等重放，返回的是原会话。
    """

    session_id: str
    status: str
    created: bool = True
    source_system: str
    external_session_id: Optional[str] = None
    session_type: str
    related_session_ids: List[str] = Field(default_factory=list)
    anchor: Optional[AnchorOut] = None
    late_upload_deadline: Optional[datetime] = None
    created_at: Optional[datetime] = None
    # P0-9：脱敏受试者标识与回退标记
    subject_ref: Optional[str] = None
    deprecated_identity_used: bool = False
    # 0012 科研域（新增可选字段，旧客户端可忽略）
    participant_id: Optional[str] = None
    project_id: Optional[str] = None
    group_id: Optional[str] = None
    data_origin: Optional[str] = None


class ActiveSessionResponse(BaseModel):
    """设备查询当前活跃会话（免认证）；无活跃会话时 session_id 为 None。"""

    device_id: str
    session_id: Optional[str] = None
    external_session_id: Optional[str] = None
    session_type: Optional[str] = None
    status: Optional[str] = None
    anchor: Optional[AnchorOut] = None
    server_time: datetime
    poll_interval_seconds: int


class SessionStatusUpdateRequest(BaseModel):
    status: Literal["running", "completed", "aborted", "interrupted"] = Field(
        examples=["running"],
        description=(
            "目标状态：running / completed / aborted / interrupted。"
            "为兼容客户端，interrupted 会落库为 aborted，"
            "ended_reason 记录 client_interrupted。"
        ),
    )


class SessionOut(BaseModel):
    session_id: str
    device_id: str
    user_id: Optional[str] = None
    subject_ref: Optional[str] = None
    deprecated_identity_used: bool = False
    session_type: str
    source_system: str
    external_session_id: Optional[str] = None
    status: str
    anchor: Optional[AnchorOut] = None
    late_upload_deadline: Optional[datetime] = None
    ended_reason: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # 0012 科研域（新增可选字段）
    participant_id: Optional[str] = None
    project_id: Optional[str] = None
    group_id: Optional[str] = None
    data_origin: Optional[str] = None


class SessionDeviceBindRequest(BaseModel):
    device_id: str = Field(
        min_length=1, max_length=64, examples=["atlas_002"], description="要绑定的设备 ID"
    )
    role: Literal["primary", "auxiliary"] = Field(
        default="auxiliary", examples=["auxiliary"], description="设备在会话中的角色"
    )


class SessionDeviceBindResponse(BaseModel):
    session_id: str
    device_id: str
    role: str
    status: str
    bound_at: datetime
    created: bool
