"""设备认证：token 生成与校验、设备身份保护（§17.4 I4/I6/I8）。"""
import hmac
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session as DBSession

from app.core.config import get_settings
from app.db.database import get_db
from app.models.device import Device
from app.services import identity_service


def generate_device_token() -> str:
    return secrets.token_hex(32)


def verify_token(stored: str, provided: str) -> bool:
    return hmac.compare_digest(stored, provided)


def client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _api_key_matches(provided: Optional[str]) -> bool:
    """校验密钥是否为唯一的 ``query_api_key``。"""
    expected = get_settings().query_api_key
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def require_app_key(
    x_app_key: Optional[str] = Header(default=None, alias="X-App-Key"),
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
) -> None:
    """设备管理接口认证（§17.4 I5/I9）：统一使用 ``query_api_key``。

    ``X-App-Key`` 仅作为 ``X-Api-Key`` 的兼容别名保留，两者取值相同。
    """
    if not (_api_key_matches(x_api_key) or _api_key_matches(x_app_key)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHORIZED: invalid or missing X-Api-Key",
        )


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
) -> None:
    """查询接口基础鉴权（P0-17 / AC-15）：设备列表、会话查询、结果查询。"""
    if not _api_key_matches(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHORIZED: invalid or missing X-Api-Key",
        )


def require_research_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
    api_key: Optional[str] = Query(default=None),
) -> None:
    """科研导出 GET 接口鉴权：优先 ``X-Api-Key``，其次 ``?api_key=``。

    查询参数仅供内网/本人浏览器直接点击下载使用，会被访问日志记录；
    不要在其他业务接口上启用。
    """
    expected = get_settings().query_api_key
    provided = x_api_key or api_key
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHORIZED: invalid or missing X-Api-Key",
        )


def _reject_credentials(
    db: DBSession,
    *,
    device: Optional[Device],
    device_id: str,
    provided_token: str,
    source_ip: Optional[str],
    message: str,
) -> HTTPException:
    """认证失败统一处理：I4 跨设备 token → CRITICAL；I8 失败突发 → WARN。"""
    owner = (
        db.query(Device).filter(Device.device_token == provided_token).first()
    )
    if owner is not None and owner.device_id != device_id:
        identity_service.record_cross_device_token(
            db, device_id, owner.device_id, source_ip
        )
        db.commit()
    elif identity_service.record_auth_failure(db, device_id, source_ip):
        db.commit()
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=message
    )


def get_current_device(
    request: Request,
    x_device_id: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
    db: DBSession = Depends(get_db),
) -> Device:
    """设备认证依赖：请求头携带 X-Device-Id / X-Device-Token。"""
    if not x_device_id or not x_device_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Device-Id or X-Device-Token header",
        )
    source_ip = client_ip(request)
    device = db.query(Device).filter(Device.device_id == x_device_id).first()
    if device is None:
        raise _reject_credentials(
            db,
            device=None,
            device_id=x_device_id,
            provided_token=x_device_token,
            source_ip=source_ip,
            message="Invalid device credentials",
        )
    # I9：吊销后 token 立即不可用
    if device.token_revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DEVICE_REVOKED: device token has been revoked",
        )
    if not verify_token(device.device_token, x_device_token):
        raise _reject_credentials(
            db,
            device=device,
            device_id=x_device_id,
            provided_token=x_device_token,
            source_ip=source_ip,
            message="Invalid device credentials",
        )
    # I6：同一 device_id 多 IP 观测（仅告警）
    if identity_service.observe_device_ip(
        db, device.device_id, source_ip, channel="authenticated"
    ):
        db.commit()
    return device


def get_device_reader(
    request: Request,
    x_device_id: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
    x_app_key: Optional[str] = Header(default=None, alias="X-App-Key"),
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
    db: DBSession = Depends(get_db),
) -> Optional[Device]:
    """设备详情读取认证（§12.2 D-4）：设备 token 或 `X-Api-Key` 二者任一。

    - 带任一设备头（X-Device-Id / X-Device-Token）时，必须通过设备凭证校验：
      无效或已吊销一律 401，设备端据此触发重新注册；返回 Device 供路由校验
      `{device_id}` 与 `X-Device-Id` 一致（不一致 → 403）。
    - 仅带合法 X-Api-Key（`X-App-Key` 为兼容别名，取值相同）时返回 None
      （手机/查询侧合法调用）。
    - 两者都缺失或均无效 → 401。
    """
    if x_device_id or x_device_token:
        return get_current_device(
            request=request,
            x_device_id=x_device_id,
            x_device_token=x_device_token,
            db=db,
        )

    if _api_key_matches(x_api_key) or _api_key_matches(x_app_key):
        return None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="UNAUTHORIZED: missing device credentials or X-Api-Key",
    )


def get_optional_device(
    request: Request,
    x_device_id: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
    db: DBSession = Depends(get_db),
) -> Optional[Device]:
    """可选设备认证：未带头 → None；带头则必须有效（无效仍 401）。"""
    if not x_device_id and not x_device_token:
        return None
    if not x_device_id or not x_device_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Device-Id or X-Device-Token header",
        )
    source_ip = client_ip(request)
    device = db.query(Device).filter(Device.device_id == x_device_id).first()
    if device is None:
        raise _reject_credentials(
            db,
            device=None,
            device_id=x_device_id,
            provided_token=x_device_token,
            source_ip=source_ip,
            message="Invalid device credentials",
        )
    if device.token_revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DEVICE_REVOKED: device token has been revoked",
        )
    if not verify_token(device.device_token, x_device_token):
        raise _reject_credentials(
            db,
            device=device,
            device_id=x_device_id,
            provided_token=x_device_token,
            source_ip=source_ip,
            message="Invalid device credentials",
        )
    # 业务请求（手机端建会话/状态流转/事件/结果同步）不参与克隆检测：
    # 这些请求来自手机网络，与设备来源 IP 不可比（§17.4 I6 仅限设备身份请求）。
    return device
