"""设备身份审计（M13 / §17.4 I10）。

只记录可观测事实（事件类型、级别、源 IP、结构化 detail、token 指纹），
**不得写入 token 明文**。
"""
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class DeviceAuditEvent(Base):
    __tablename__ = "device_audit_events"
    __table_args__ = (
        Index("ix_device_audit_events_device_created", "device_id", "created_at"),
        Index("ix_device_audit_events_type_created", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<DeviceAuditEvent {self.device_id} {self.event_type} "
            f"{self.severity}>"
        )
