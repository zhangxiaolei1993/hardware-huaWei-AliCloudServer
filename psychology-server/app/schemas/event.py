"""Timeline 事件 Schema（v1.4 §7.2 / §7.4 / §12.4 U-2）。

13 类事件白名单、payload 受控白名单与答案类禁字段在这里集中定义；
事件条目用 ``extra="forbid"``，避免客户端把答案「顺手」塞进事件。
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_TYPES = (
    "session_started",
    "session_ended",
    "device_connected",
    "device_disconnected",
    "question_shown",
    "response_started",
    "response_submitted",
    "response_modified",
    "question_skipped",
    "question_completed",
    "speech_started",
    "speech_ended",
    "emotion_recorded",
)
EventType = Literal[
    "session_started",
    "session_ended",
    "device_connected",
    "device_disconnected",
    "question_shown",
    "response_started",
    "response_submitted",
    "response_modified",
    "question_skipped",
    "question_completed",
    "speech_started",
    "speech_ended",
    "emotion_recorded",
]

#: 明确拒绝的答案类字段（AC-06：整批 400，不部分入库）
FORBIDDEN_EVENT_KEYS = (
    "answer_value",
    "answer_text",
    "selected_option_ids",
    "score_delta",
    "user_name",
)

#: 各事件类型允许的 payload 键（服务端常量，非运行时配置）
ALLOWED_PAYLOAD_KEYS: Dict[str, set] = {
    "question_shown": {"question_type", "render_ms"},
    "response_started": {"input_method"},
    "response_submitted": {"response_changed", "input_method"},
    "response_modified": {"modification_count"},
    "question_skipped": {"skip_reason_code"},
    "question_completed": {"total_time_ms", "hesitation_count"},
    "speech_started": {"audio_stream_id", "duration_ms", "bytes"},
    "speech_ended": {"audio_stream_id", "duration_ms", "bytes"},
    "session_started": {"ended_reason", "client_version"},
    "session_ended": {"ended_reason", "client_version"},
    "device_connected": {"connection_id", "rssi"},
    "device_disconnected": {"connection_id", "rssi"},
    "emotion_recorded": {"record_index", "window_ms"},
}


class TimelineEventIn(BaseModel):
    """单条事件。``quality`` / ``quality_info`` 即使传入也会被服务器忽略。"""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(pattern=r"^[0-9a-fA-F]{32}$", max_length=32)
    event_type: EventType
    occurred_at: datetime
    relative_seconds: float = Field(ge=-60.0, description="相对锚点秒数，允许 -60s 首题前锚缓冲")
    device_id: Optional[str] = Field(default=None, max_length=64)
    stream_id: Optional[str] = Field(default=None, max_length=40)
    device_timestamp: Optional[str] = Field(default=None, max_length=40)
    monotonic_ms: Optional[int] = None
    clock_offset_ms: Optional[int] = None
    time_uncertainty_ms: Optional[int] = None
    task_id: Optional[str] = Field(default=None, max_length=64)
    questionnaire_id: Optional[str] = Field(default=None, max_length=64)
    questionnaire_version: Optional[str] = Field(default=None, max_length=32)
    question_id: Optional[str] = Field(default=None, max_length=64)
    question_index: Optional[int] = Field(default=None, ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)
    protocol_version: Optional[str] = Field(default=None, max_length=16)
    payload: Optional[Dict[str, Any]] = None
    # 客户端伪造的质量字段：接受但不采信
    quality: Optional[str] = None
    quality_info: Optional[Dict[str, Any]] = None

    @field_validator("relative_seconds")
    @classmethod
    def _finite_seconds(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("relative_seconds must be a finite number")
        return value


class TimelineEventBatchRequest(BaseModel):
    """事件批量上传请求（``events`` 先按原始 dict 接收，便于统一做禁字段检查）。"""

    client_request_id: str = Field(min_length=8, max_length=128)
    schema_version: str = Field(default="1.0", max_length=16)
    source: Literal["device", "app", "server"] = "app"
    events: List[Dict[str, Any]] = Field(min_length=1, max_length=2000)


class TimelineUploadResponse(BaseModel):
    session_id: str
    accepted: int
    skipped_duplicate: int
    rejected: int = 0
    upload_id: str
    server_received_at: datetime


class TimelineEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    session_id: str
    device_id: Optional[str] = None
    stream_id: Optional[str] = None
    event_type: str
    source: str
    occurred_at: datetime
    relative_seconds: float
    questionnaire_id: Optional[str] = None
    question_id: Optional[str] = None
    question_index: Optional[int] = None
    duration_ms: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    quality: str
    quality_info: Optional[Dict[str, Any]] = None
    client_request_id: str
    server_received_at: datetime


class TimelineEventListResponse(BaseModel):
    session_id: str
    count: int
    events: List[TimelineEventOut]
