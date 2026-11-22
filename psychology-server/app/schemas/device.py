"""设备相关 Schema。"""
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeviceRegisterRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    device_id: str = Field(min_length=1, max_length=64, examples=["atlas_001"])
    device_name: str = Field(
        min_length=1, max_length=128, examples=["Atlas 001 开发板"]
    )
    device_type: str = Field(min_length=1, max_length=64, examples=["edge_ai_device"])
    manufacturer: Optional[str] = Field(
        default=None, max_length=64, examples=["Huawei"]
    )
    model: Optional[str] = Field(
        default=None, max_length=128, examples=["Atlas 200I DK A2"]
    )
    firmware_version: Optional[str] = Field(
        default=None, max_length=64, examples=["1.0.0"]
    )
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

    firmware_version: Optional[str] = Field(
        default=None, max_length=64, examples=["1.0.0"]
    )


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
        examples=["3f2a9c1e-8b7d-4c65-9a10-2d6e5f7b8c90"],
        description="手机端生成的安装/实例唯一 ID（UUID），同一台手机保持不变",
    )
    user_id: Optional[str] = Field(
        default=None, max_length=64, examples=["user_1001"]
    )


class DeviceConnectResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    connected: bool  # true=设备在线可连接；false=设备存在但离线
    device_id: str
    device_name: str
    device_status: str  # online / offline
    connection_id: str
    connected_at: datetime


class DeviceDisconnectRequest(BaseModel):
    """手机断开设备。

    `connection_id` 与 `app_client_id` 至少提供一个：
    - 提供 `connection_id`：精确定位连接（原有方式，保持兼容）
    - 未提供 `connection_id`：按 `device_id` + `app_client_id`(+`user_id`)
      解析当前 `connected` 连接并关闭
    """

    connection_id: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=64,
        examples=["conn_20261119_ab12cd34"],
        description="connect 返回的连接 ID；提供时优先按它关闭",
    )
    app_client_id: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        examples=["3f2a9c1e-8b7d-4c65-9a10-2d6e5f7b8c90"],
        description="手机端安装实例 ID；未传 connection_id 时用它解析活跃连接",
    )
    user_id: Optional[str] = Field(
        default=None,
        max_length=64,
        examples=["user_1001"],
        description="可选，传入时作为解析活跃连接的附加过滤条件",
    )


class DeviceDisconnectResponse(BaseModel):
    connection_id: str
    device_id: str
    status: str  # disconnected
    disconnected_at: datetime


# ---------- 设备身份保护（§17.4 I5/I9） ----------
class DeviceTokenRotateResponse(BaseModel):
    device_id: str
    device_token: str = Field(description="新 token，仅本次响应返回一次")
    token_rotated_at: datetime


class DeviceRevokeResponse(BaseModel):
    device_id: str
    status: str  # revoked
    token_revoked_at: datetime


class DeviceReinstateResponse(BaseModel):
    device_id: str
    device_token: str = Field(description="新 token，仅本次响应返回一次")
    status: str  # active
    token_rotated_at: datetime
