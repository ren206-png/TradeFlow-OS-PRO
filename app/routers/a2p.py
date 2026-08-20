"""
A2P 10DLC Registration Admin API.

Endpoints:
  GET  /admin/a2p/{tenant_id}  — get registration status
  POST /admin/a2p/{tenant_id}  — create or update registration record
  PATCH /admin/a2p/{tenant_id} — update status field only

All endpoints require X-Admin-Key header matching settings.admin_password.
These are admin-only — not contractor-facing in Phase 1.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.a2p_registration import A2PRegistration
from app.schemas.outbound import A2PStatus

router = APIRouter(prefix="/admin/a2p", tags=["admin", "a2p"])
logger = logging.getLogger(__name__)


def _require_admin(x_admin_key: str = Header(...)) -> None:
    """
    Dependency: verify X-Admin-Key matches settings.admin_password.
    Fails closed — returns 503 if ADMIN_PASSWORD is not set in env vars,
    rather than silently falling back to secret_key (which signs session tokens).
    """
    expected = settings.admin_password
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin password not configured. Set ADMIN_PASSWORD in Railway env vars.",
        )
    if not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key.",
        )


class A2PCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_registration_id: Optional[str] = None
    campaign_id: Optional[str] = None
    status: str = "unregistered"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


class A2PPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


def _to_schema(row: A2PRegistration) -> A2PStatus:
    return A2PStatus(
        tenant_id=row.tenant_id,
        brand_registration_id=row.brand_registration_id,
        campaign_id=row.campaign_id,
        status=row.status,  # type: ignore[arg-type]
        submitted_at=row.submitted_at,
        approved_at=row.approved_at,
        rejection_reason=row.rejection_reason,
        updated_at=row.updated_at,
    )


@router.get("/{tenant_id}", response_model=A2PStatus)
async def get_a2p_status(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> A2PStatus:
    """Return the A2P registration record for a tenant."""
    result = await db.execute(
        select(A2PRegistration).where(A2PRegistration.tenant_id == tenant_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No A2P record for tenant {tenant_id!r}.")
    return _to_schema(row)


@router.post("/{tenant_id}", response_model=A2PStatus, status_code=status.HTTP_200_OK)
async def upsert_a2p_status(
    tenant_id: str,
    body: A2PCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> A2PStatus:
    """Create or replace the A2P registration record for a tenant."""
    # Validate the status value
    valid_statuses = {"unregistered", "pending", "approved", "rejected", "suspended"}
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(valid_statuses)}",
        )

    result = await db.execute(
        select(A2PRegistration).where(A2PRegistration.tenant_id == tenant_id)
    )
    row = result.scalar_one_or_none()

    if row is None:
        row = A2PRegistration(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
        )
        db.add(row)

    row.brand_registration_id = body.brand_registration_id
    row.campaign_id = body.campaign_id
    row.status = body.status
    row.submitted_at = body.submitted_at
    row.approved_at = body.approved_at
    row.rejection_reason = body.rejection_reason
    row.updated_at = datetime.now(tz=timezone.utc)

    await db.flush()
    logger.info("A2P record upserted | tenant=%s status=%s", tenant_id, row.status)
    return _to_schema(row)


@router.patch("/{tenant_id}", response_model=A2PStatus)
async def patch_a2p_status(
    tenant_id: str,
    body: A2PPatchRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin),
) -> A2PStatus:
    """Update the status field only on an existing A2P record."""
    valid_statuses = {"unregistered", "pending", "approved", "rejected", "suspended"}
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(valid_statuses)}",
        )

    result = await db.execute(
        select(A2PRegistration).where(A2PRegistration.tenant_id == tenant_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No A2P record for tenant {tenant_id!r}.")

    row.status = body.status
    row.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    logger.info("A2P status patched | tenant=%s status=%s", tenant_id, row.status)
    return _to_schema(row)
