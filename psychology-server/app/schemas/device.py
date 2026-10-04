"""设备相关 Schema。"""
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeviceRegisterRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    device_id: str = Field(min_length=1, max_length=64, examples=["atlas_001"])
    device_name: str = Field(min_length=1, max_length=128)
    device_type: str = Field(min_length=1, max_length=64, examples=["edge_ai_device"])
    manufacturer: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=128)
    firmware_version: Optional[str] = Field(default=None, max_length=64)
    capabilities: List[str] = Field(default_factory=list, examples=[["emotion"]])


class DeviceRegisterResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    device_id: str
    device_token: str
    created: bool  # True=新注册 False=已存在（返回原 token）
    status: str
    last_seen: Optional[datetime] = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    device_id: str
    device_name: str
    device_type: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    firmware_version: Optional[str] = None
    status: str
    last_seen: Optional[datetime] = None
    capabilities: Any = None
    created_at: datetime
    updated_at: datetime


class HeartbeatRequest(BaseModel):
    """心跳可为空 body，也可携带最新固件版本等信息。"""

    firmware_version: Optional[str] = Field(default=None, max_length=64)


class HeartbeatResponse(BaseModel):
    device_id: str
    status: str
    last_seen: datetime


# ---------- 手机 App 连接设备 ----------
class DeviceConnectRequest(BaseModel):
    """手机端点击"连接设备"时的请求。手机不持有设备 token，用 app_client_id 标识自身。"""

    app_client_id: str = Field(
        min_length=8,
        max_length=128,
        description="手机端生成的安装/实例唯一 ID（UUID），同一台手机保持不变",
    )
    user_id: Optional[str] = Field(default=None, max_length=64)


class DeviceConnectResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    connected: bool  # true=设备在线可连接；false=设备存在但离线
    device_id: str
    device_name: str
    device_status: str  # online / offline
    connection_id: str
    connected_at: datetime


class DeviceDisconnectRequest(BaseModel):
    connection_id: str = Field(min_length=8, max_length=64)


class DeviceDisconnectResponse(BaseModel):
    connection_id: str
    device_id: str
    status: str  # disconnected
    disconnected_at: datetime
