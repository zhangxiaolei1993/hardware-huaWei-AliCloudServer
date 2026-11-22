"""测评结果同步 API（v1.4 §12.4 U-4 / §14.4）。"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy.orm import Session as DBSession

from app.core.security import get_optional_device
from app.db.database import get_db
from app.models.device import Device
from app.schemas.result_snapshot import ResultSyncResponse
from app.services import result_service, session_service

router = APIRouter(prefix="/sessions", tags=["results"])


@router.post(
    "/{session_id}/results",
    response_model=ResultSyncResponse,
    summary="同步外部测评结果快照",
    description=(
        "接收客户端转发的 B 端测评结果摘要（白名单）。\n\n"
        "- 幂等键固定为 `res:{session_id}:{questionnaire_code}:v{result_version}`，"
        "服务端重新拼接校验，不符 → 422 `IDEMPOTENCY_KEY_MALFORMED`\n"
        "- 同键同 `source_payload_sha256` → `200 duplicate:true`；同键不同摘要 → 409 `IDEMPOTENCY_CONFLICT`\n"
        "- `score_list`、`chart1_*`、逐题 `score`、报告长文本等答案等价物 → 422 `FORBIDDEN_FIELD`\n"
        "- `trust_level` 由服务器计算，一期恒为 `client_relayed`\n"
        "- `radar_norm` 由服务端按 series 的 `unit_max` 重算，客户端 norm 不采信"
    ),
)
def sync_result(
    session_id: str,
    response: Response,
    payload: Dict[str, Any] = Body(...),
    db: DBSession = Depends(get_db),
    device: Optional[Device] = Depends(get_optional_device),
) -> Any:
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    # 带设备凭证时校验归属；Flutter 的 X-Api-Key 鉴权属 P0-17
    if device is not None and session.device_id != device.device_id:
        raise HTTPException(status_code=403, detail="session belongs to another device")

    try:
        snapshot, duplicate = result_service.create_snapshot(db, session, payload)
    except result_service.ResultSyncError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    response.status_code = 200 if duplicate else 201
    return ResultSyncResponse(
        snapshot_id=snapshot.snapshot_id,
        session_id=snapshot.session_id,
        questionnaire_code=snapshot.questionnaire_code,
        result_version=snapshot.result_version,
        supersedes_snapshot_id=snapshot.supersedes_snapshot_id,
        trust_level=snapshot.trust_level,
        duplicate=duplicate,
        received_at=snapshot.received_at,
    )
