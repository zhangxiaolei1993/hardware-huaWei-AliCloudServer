"""表情识别业务 Schema。

合法表情共 8 类，服务器必须校验；confidence 必须在 0~1。
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

VALID_EXPRESSIONS = (
    "neutral",
    "happiness",
    "surprise",
    "sadness",
    "anger",
    "disgust",
    "fear",
    "contempt",
)
ExpressionType = Literal[
    "neutral",
    "happiness",
    "surprise",
    "sadness",
    "anger",
    "disgust",
    "fear",
    "contempt",
]


class TimelineItem(BaseModel):
    relative_seconds: float = Field(
        ge=-5.0,
        examples=[0.5],
        description="相对 session 开始的秒数（允许 -5s 锚点容差）",
    )
    expression: ExpressionType = Field(examples=["happiness"])
    confidence: float = Field(ge=0.0, le=1.0, examples=[0.92])
    frame_index: Optional[int] = Field(
        default=None, ge=0, examples=[30], description="摄像头帧序号（v2 推荐，v1 缺失不拒收）"
    )


class SessionMeta(BaseModel):
    """开发板本地记录的原始采集元信息（服务器只参考，不作为最终结果）。"""

    session_start: Optional[str] = None
    session_end: Optional[str] = None
    elapsed_seconds: Optional[float] = Field(default=None, ge=0)
    video_frames: Optional[int] = Field(default=None, ge=0)
    display_fps: Optional[float] = Field(default=None, ge=0)
    face_detected_frames: Optional[int] = Field(default=None, ge=0)
    face_coverage_percent: Optional[float] = Field(default=None, ge=0, le=100)
    inference_runs: Optional[int] = Field(default=None, ge=0)


class EmotionUploadRequest(BaseModel):
    client_request_id: str = Field(
        min_length=8,
        max_length=128,
        examples=["req-20261119-0001"],
        description="客户端生成的唯一请求 ID，用于防止网络重试导致重复插入",
    )
    session_meta: SessionMeta = Field(
        default_factory=SessionMeta,
        examples=[{"elapsed_seconds": 120.0, "video_frames": 3600, "display_fps": 30.0}],
    )
    timeline: List[TimelineItem] = Field(
        min_length=1,
        max_length=50000,
        examples=[[{"relative_seconds": 0.5, "expression": "happiness", "confidence": 0.92}]],
    )

    # ---- 分级兼容接收（P0-14）：v1 固件不带这些字段，全部可选 ----
    stream_id: Optional[str] = Field(default=None, max_length=40)
    device_timestamp: Optional[str] = Field(default=None, max_length=40)
    monotonic_ms: Optional[int] = None
    clock_offset_ms: Optional[int] = None
    protocol_version: Optional[str] = Field(default=None, max_length=16)
    # 客户端伪造的质量字段：接受但一律忽略（AC-21）
    quality: Optional[str] = None
    quality_info: Optional[Dict[str, Any]] = None


class EmotionResultData(BaseModel):
    valid_expression_seconds: float
    dominant_expression: str
    average_confidence: float
    face_coverage_percent: float
    result_json: Dict[str, Any]
    created_at: datetime


class EmotionUploadResponse(BaseModel):
    session_id: str
    session_status: str
    duplicate: bool = False
    result: EmotionResultData


class EmotionResultResponse(BaseModel):
    session_id: str
    result: Optional[EmotionResultData] = None


# ---------- 设备实时表情状态 ----------
class DeviceEmotionStatusRequest(BaseModel):
    """Atlas 约每 3 秒上报一次的当前识别状态。"""

    face_detected: bool = Field(examples=[True], description="当前帧是否检测到人脸")
    expression_detected: bool = Field(
        examples=[True], description="当前是否有效识别出表情"
    )
    current_expression: Optional[ExpressionType] = Field(
        default=None,
        examples=["happiness"],
        description="当前表情；识别到表情时必填且为 8 类之一",
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, examples=[0.92], description="当前表情置信度，0~1"
    )

    @model_validator(mode="after")
    def _check_expression_consistency(self) -> "DeviceEmotionStatusRequest":
        if self.expression_detected:
            if self.current_expression is None:
                raise ValueError(
                    "current_expression is required when expression_detected is true"
                )
        else:
            # 未识别出表情时不应携带具体表情/置信度
            self.current_expression = None
            self.confidence = None
        return self


class DeviceEmotionStatusResponse(BaseModel):
    device_id: str
    face_detected: bool
    expression_detected: bool
    current_expression: Optional[str]
    confidence: Optional[float]
    updated_at: datetime = Field(description="服务器最后一次收到状态的时间")
    stale: bool = Field(
        description="距上次上报是否已超过 10 秒；true 表示实时信号可能中断"
    )
