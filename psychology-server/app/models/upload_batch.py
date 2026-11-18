"""上传批次表（v1.4 §11.3 / M4）。

每次客户端批量上传登记一行，幂等键 ``UNIQUE(session_id, client_request_id)``；
批次是「对账」依据，原始记录与派生结果仍落在各业务表。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class UploadBatch(Base):
    __tablename__ = "upload_batches"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "client_request_id", name="uq_upload_batches_session_request"
        ),
    )

    upload_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id"), nullable=False, index=True
    )
    app_client_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # event / emotion / raw / result
    batch_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # open / received / verified / failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<UploadBatch {self.upload_id} session={self.session_id}>"
