"""科研版云端请求 Schema（第三阶段）。

响应统一使用服务层构造的 dict，时间字段由服务层转成 ``+08:00`` 字符串；
这里只定义入参，避免客户端多传字段被静默忽略。
"""
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

GroupType = Literal["experimental", "control", "other"]
ParticipantStatus = Literal["active", "archived"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# 项目 / 分组
# ---------------------------------------------------------------------------
class ProjectCreate(_Strict):
    name: str = Field(min_length=1, max_length=128)
    code: Optional[str] = Field(default=None, max_length=32)
    description: Optional[str] = Field(default=None, max_length=512)


class ProjectUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    code: Optional[str] = Field(default=None, max_length=32)
    description: Optional[str] = Field(default=None, max_length=512)


class GroupCreate(_Strict):
    name: str = Field(min_length=1, max_length=64, description="用户自定义组名")
    group_type: GroupType = "experimental"
    sort_order: int = 0


class GroupUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    group_type: Optional[GroupType] = None
    sort_order: Optional[int] = None


# ---------------------------------------------------------------------------
# 被试
# ---------------------------------------------------------------------------
class ParticipantCreate(_Strict):
    project_id: str = Field(min_length=1, max_length=40)
    participant_code: str = Field(min_length=1, max_length=32)
    name: Optional[str] = Field(default=None, max_length=64)
    phone: Optional[str] = Field(default=None, max_length=32)
    group_id: Optional[str] = Field(default=None, max_length=40)
    gender: Optional[str] = Field(default=None, max_length=16)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    school: Optional[str] = Field(default=None, max_length=128)
    grade: Optional[str] = Field(default=None, max_length=32)
    source_local_id: Optional[str] = Field(default=None, max_length=64)
    client_request_id: Optional[str] = Field(default=None, max_length=128)


class ParticipantUpdate(_Strict):
    participant_code: Optional[str] = Field(default=None, min_length=1, max_length=32)
    name: Optional[str] = Field(default=None, max_length=64)
    phone: Optional[str] = Field(default=None, max_length=32)
    group_id: Optional[str] = Field(default=None, max_length=40)
    gender: Optional[str] = Field(default=None, max_length=16)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    school: Optional[str] = Field(default=None, max_length=128)
    grade: Optional[str] = Field(default=None, max_length=32)
    status: Optional[ParticipantStatus] = None
    row_version: int = Field(description="客户端持有的 row_version，用于乐观锁")


class ParticipantBatchItem(_Strict):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    participant_code: str = Field(min_length=1, max_length=32)
    name: Optional[str] = Field(default=None, max_length=64)
    phone: Optional[str] = Field(default=None, max_length=32)
    group_id: Optional[str] = Field(default=None, max_length=40)
    gender: Optional[str] = Field(default=None, max_length=16)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    school: Optional[str] = Field(default=None, max_length=128)
    grade: Optional[str] = Field(default=None, max_length=32)
    source_local_id: Optional[str] = Field(default=None, max_length=64)
    row_version: Optional[int] = None
    updated_at: Optional[datetime] = None
    deleted: bool = False


class ParticipantBatchSyncRequest(_Strict):
    project_id: str = Field(min_length=1, max_length=40)
    items: List[ParticipantBatchItem] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# 会话绑定 / 逐题作答
# ---------------------------------------------------------------------------
class AssignParticipantRequest(_Strict):
    participant_id: str = Field(min_length=1, max_length=40)
    backfill_assessments: bool = True


class AssessmentItemIn(_Strict):
    question_id: str = Field(min_length=1, max_length=64)
    question_index: int = Field(ge=0)
    question_type: Optional[str] = Field(default=None, max_length=32)
    answer_value: Optional[Any] = None
    score: Optional[float] = None
    max_score: Optional[float] = None
    response_ms: Optional[int] = Field(default=None, ge=0)
    answered_at: Optional[datetime] = None


class AssessmentItemsUpsertRequest(_Strict):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    result_version: int = Field(ge=1)
    items: List[AssessmentItemIn] = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# AI 对话
# ---------------------------------------------------------------------------
class ConversationCreate(_Strict):
    session_id: str = Field(min_length=1, max_length=64)
    participant_id: Optional[str] = Field(default=None, max_length=40)
    assessment_id: Optional[str] = Field(default=None, max_length=40)
    external_conversation_id: Optional[str] = Field(default=None, max_length=128)
    source: str = Field(default="xin_tan_tan", max_length=32)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class ConversationMessageIn(_Strict):
    message_id: str = Field(min_length=1, max_length=64)
    seq: int = Field(ge=1)
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1)
    content_type: str = Field(default="text", max_length=16)
    attachment_ref: Optional[str] = Field(default=None, max_length=256)
    model: Optional[str] = Field(default=None, max_length=64)
    token_count: Optional[int] = Field(default=None, ge=0)
    occurred_at: datetime

    @field_validator("content")
    @classmethod
    def _content_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 64 * 1024:
            raise ValueError("content exceeds 64KB")
        return value


class ConversationMessagesUploadRequest(_Strict):
    client_request_id: Optional[str] = Field(default=None, max_length=128)
    messages: List[ConversationMessageIn] = Field(min_length=1, max_length=500)


class LinkAssessmentRequest(_Strict):
    assessment_id: str = Field(min_length=1, max_length=40)


# ---------------------------------------------------------------------------
# 下载中心
# ---------------------------------------------------------------------------
class DownloadScope(_Strict):
    mode: Literal["all", "group", "participants"] = "all"
    group_ids: List[str] = Field(default_factory=list)
    group_names: List[str] = Field(default_factory=list)
    participant_ids: List[str] = Field(default_factory=list, max_length=500)


class AssessmentScope(_Strict):
    mode: Literal[
        "all", "latest_per_scale", "date_range", "questionnaire", "session"
    ] = "all"
    from_: Optional[datetime] = Field(default=None, alias="from")
    to: Optional[datetime] = None
    questionnaire_codes: List[str] = Field(default_factory=list)
    session_ids: List[str] = Field(default_factory=list)


class DownloadJobCreate(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: str = Field(min_length=1, max_length=40)
    scope: DownloadScope = Field(default_factory=DownloadScope)
    assessment_scope: AssessmentScope = Field(default_factory=AssessmentScope)
    data_types: Optional[List[str]] = None
    format: Literal["csv", "json", "both"] = "csv"
    include_archived: bool = False


# ---------------------------------------------------------------------------
# 历史迁移（仅运维用途）
# ---------------------------------------------------------------------------
class LegacyParticipantIn(_Strict):
    source_local_id: str = Field(min_length=1, max_length=64)
    participant_code: str = Field(min_length=1, max_length=32)
    name: Optional[str] = Field(default=None, max_length=64)
    phone: Optional[str] = Field(default=None, max_length=32)
    group_id: Optional[str] = Field(default=None, max_length=40)
    group_name: Optional[str] = Field(default=None, max_length=64)
    gender: Optional[str] = Field(default=None, max_length=16)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    school: Optional[str] = Field(default=None, max_length=128)
    grade: Optional[str] = Field(default=None, max_length=32)


class LegacyAssessmentIn(_Strict):
    source_local_id: str = Field(min_length=1, max_length=64)
    questionnaire_code: str = Field(min_length=1, max_length=64)
    reported_at: datetime
    s1_session_id: Optional[str] = Field(default=None, max_length=64)
    total_score: Optional[float] = None
    result_payload: dict = Field(default_factory=dict)


class LegacyImportRequest(_Strict):
    project_id: str = Field(min_length=1, max_length=40)
    dry_run: bool = True
    participants: List[LegacyParticipantIn] = Field(default_factory=list)
    assessments: List[LegacyAssessmentIn] = Field(default_factory=list)
