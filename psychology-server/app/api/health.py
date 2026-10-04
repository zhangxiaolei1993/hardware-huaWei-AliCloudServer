"""健康检查。"""
from typing import Any, Dict

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="健康检查",
    description="返回服务运行状态，可用于监控探活与负载均衡健康探测。",
)
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": get_settings().app_name}
