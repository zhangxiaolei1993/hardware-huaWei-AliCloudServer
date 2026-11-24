"""外部测评结果快照 Schema（v1.4 §14.2.1 / §14.4）。

顶层白名单通过 ``extra="forbid"`` 收口；答案等价物（`score_list`、`chart1_*`、
逐题 `score` 等）在服务层先行扫描并以 422 `FORBIDDEN_FIELD` 拒绝。
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DimensionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, max_length=128)
    score: float


class DimensionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["raw_map_v1"]
    items: List[DimensionItem] = Field(default_factory=list)
    dropped: Optional[List[Dict[str, Any]]] = None
    truncated: Optional[bool] = None


class RadarItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, max_length=128)
    raw: Optional[float] = None
    norm: Optional[float] = None


class RadarSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart: str = Field(min_length=1, max_length=128)
    unit: Optional[str] = Field(default=None, max_length=64)
    unit_max: Optional[float] = None
    unit_max_source: str = Field(default="not_provided", max_length=32)
    raw_precision: Optional[int] = Field(default=None, ge=0, le=6)
    norm_status: str = Field(default="unit_max_unknown", max_length=32)
    items: List[RadarItem] = Field(default_factory=list)


class RadarNormPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: Literal["radar_norm_v1"]
    series: List[RadarSeries] = Field(default_factory=list)
    truncated: Optional[bool] = None


class RawRadarChart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: List[str] = Field(default_factory=list)
    values: List[float] = Field(default_factory=list)


class ResultSyncRequest(BaseModel):
    """请求体白名单（§14.2.1「服务端唯一接受集」）。"""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)
    source_system: Optional[str] = Field(default=None, max_length=32)
    source_result_id: Optional[str] = Field(default=None, max_length=128)
    external_session_id: Optional[Union[str, int]] = None
    questionnaire_code: str = Field(min_length=1, max_length=64)
    questionnaire_version: Optional[str] = Field(default=None, max_length=32)
    scale_algorithm_version: Optional[str] = Field(default=None, max_length=32)
    scale_name: Optional[str] = Field(default=None, max_length=128)
    scale_question_count: Optional[int] = Field(default=None, ge=0)
    result_version: int = Field(default=1, ge=1)
    supersedes_snapshot_id: Optional[str] = Field(default=None, max_length=40)
    status: Literal["complete", "partial", "invalid"]
    total_score: Optional[float] = Field(default=None, ge=0, le=1000)
    scoring_mode: Optional[str] = Field(default=None, max_length=16)
    state_update_status: Optional[str] = Field(default=None, max_length=32)
    risk_level: Optional[str] = Field(default=None, max_length=32)
    level_code: Optional[str] = Field(default=None, max_length=32)
    recommendation_code: Optional[str] = Field(default=None, max_length=64)
    ai_profile_score: Optional[float] = Field(default=None, ge=0, le=100)
    summary_code: Optional[str] = Field(default=None, max_length=64)
    dimensions: Optional[Dict[str, Any]] = None
    radar_norm: Optional[Dict[str, Any]] = None
    # 原始聚合图（chart2/chart3）；服务端据此归一化，不信任客户端 norm
    radar_charts: Optional[Dict[str, Any]] = None
    reported_at: datetime
    source_payload_sha256: Optional[str] = Field(default=None, max_length=64)
    signature: Optional[str] = Field(default=None, max_length=512)
    signature_algorithm: Optional[str] = Field(default=None, max_length=32)

    @field_validator("external_session_id")
    @classmethod
    def _textual_external_id(cls, value):
        if value is None:
            return None
        return str(value)

    @field_validator("scale_name")
    @classmethod
    def _truncate_scale_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value[:128]


class ResultSyncResponse(BaseModel):
    snapshot_id: str
    session_id: str
    questionnaire_code: str
    result_version: int
    supersedes_snapshot_id: Optional[str] = None
    trust_level: str
    duplicate: bool = False
    received_at: datetime
    # 0012 科研域：会话绑定被试时返回对应测评 ID；旧客户端忽略即可
    assessment_id: Optional[str] = None
