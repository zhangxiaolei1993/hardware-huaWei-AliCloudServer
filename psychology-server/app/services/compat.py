"""分级兼容接收器（v1.4 §8.5 / §8.6 / P0-14）。

职责：
- 协议版本推断（缺省 / 无 stream_id → 1.0）；
- 默认数据流补全（``stream_defaulted``）；
- ``quality`` 主等级与 ``quality_info`` 明细推导（客户端声明一律忽略）；
- 兼容告警响应头。

P0 事实：设备不上报 ``device_timestamp`` / ``monotonic_ms``，
因此正常链路只会产出 ``legacy`` / ``missing``。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_STREAM_ID = "facial_expression"
DEFAULT_PROTOCOL_VERSION = "1.0"
#: 服务器接受的协议版本列表（AC-19，固定响应头 X-Protocol-Accepted）
ACCEPTED_PROTOCOL_VERSIONS = "1.0,2.0"
#: 时间上界额外容差（§8.4）
UPPER_BOUND_MARGIN_SECONDS = 600
CLOCK_SKEW_THRESHOLD_MS = 300000


def protocol_lt(version: str, target: str = "2.0") -> bool:
    try:
        return float(version) < float(target)
    except (TypeError, ValueError):
        return True


def infer_protocol_version(
    header_version: Optional[str], body_version: Optional[str]
) -> Tuple[str, bool]:
    """返回 (版本, 是否为服务器推断)。"""
    provided = body_version or header_version
    if provided:
        return provided.strip(), False
    return DEFAULT_PROTOCOL_VERSION, True


def compatibility_headers(
    protocol_version: str, flags: List[str], inferred: bool
) -> Dict[str, str]:
    headers = {
        "X-Protocol-Accepted": ACCEPTED_PROTOCOL_VERSIONS,
        "X-Protocol-Version": protocol_version,
    }
    warnings: List[str] = []
    if inferred:
        warnings.append("protocol_inferred")
    if "stream_defaulted" in flags:
        warnings.append("stream_defaulted")
    if "batch_upload" in flags:
        warnings.append("batch_upload")
    if warnings:
        headers["X-Compat-Warning"] = ",".join(warnings)
    return headers


def derive_record_quality(
    *,
    protocol_version: str,
    stream_id: Optional[str],
    device_timestamp: Optional[str],
    monotonic_ms: Optional[int],
    frame_index: Optional[int],
    clock_offset_ms: Optional[int],
    relative_seconds: float,
    upper_bound_seconds: float,
    server_received_at: datetime,
) -> Tuple[str, Dict[str, Any], str, List[str]]:
    """按 §8.5 优先级推导单条记录的质量。

    返回 (quality, quality_info, 实际使用的 stream_id, flags)。
    """
    stream_defaulted = stream_id is None
    effective_stream = stream_id or DEFAULT_STREAM_ID

    flags: List[str] = ["batch_upload", "clock_unverified"]
    if stream_defaulted:
        flags.append("stream_defaulted")
    if -5.0 <= relative_seconds < 0:
        flags.append("pre_anchor")
    if relative_seconds > upper_bound_seconds:
        flags.append("out_of_range")
    if (
        clock_offset_ms is not None
        and abs(clock_offset_ms) > CLOCK_SKEW_THRESHOLD_MS
    ):
        flags.append("clock_skew")

    # 优先级 1：v1 固件 / 未提供 stream_id
    if stream_defaulted or protocol_lt(protocol_version):
        quality = "legacy"
    elif device_timestamp is None and monotonic_ms is None:
        quality = "missing"
    elif monotonic_ms is not None or device_timestamp is not None:
        # 时钟未校验，P0 不产出 device
        quality = "estimated"
    else:
        quality = "missing"

    quality_info = {
        "protocol_version": protocol_version,
        "stream_id": effective_stream,
        "stream_source": "server_default" if stream_defaulted else "client",
        "device_timestamp": device_timestamp,
        "monotonic_ms": monotonic_ms,
        "frame_index": frame_index,
        "clock_sync_status": "unverified",
        "server_received_at": server_received_at.isoformat(),
        "flags": flags,
    }
    return quality, quality_info, effective_stream, flags
