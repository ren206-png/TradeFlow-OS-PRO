from __future__ import annotations

import uuid
import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FSMCredential(Base):
    __tablename__ = "fsm_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contractor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contractors.id"), unique=True, nullable=False
    )
    vendor: Mapped[str] = mapped_column(String(50), nullable=False)  # "jobber" or "housecall_pro"
    access_token_enc: Mapped[str] = mapped_column(String(500), nullable=False)  # Fernet-encrypted
    refresh_token_enc: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    token_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.datetime.utcnow
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
