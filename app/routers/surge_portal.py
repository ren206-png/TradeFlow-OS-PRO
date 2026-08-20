"""
Phase 6: Surge Portal API — manual surge mode control for contractors.
Routes:
  POST /portal/surge/activate   — manual override activate
  POST /portal/surge/deactivate — manual override deactivate
  GET  /portal/surge/status     — current surge state
All gated behind weather_surge_mode feature flag.
Tenant isolation enforced at every query.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.contractor import Contractor
from app.models.surge_mode_record import SurgeModeRecord

router = APIRouter(prefix="/portal/surge", tags=["surge-portal"])
logger = logging.getLogger(__name__)

_MAX_MULTIPLIER = Decimal("1.5")


def _flag_check():
    if not settings.weather_surge_mode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="weather_surge_mode feature flag is not enabled",
        )


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SurgeActivateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    tenant_id: str
    surge_type: str  # extreme_cold|heat|storm
    duration_hours: int = 4  # how long the manual surge lasts (max 12)
    overbooking_multiplier: Decimal = Decimal("1.0")

    @field_validator("surge_type")
    @classmethod
    def validate_surge_type(cls, v: str) -> str:
        allowed = {"extreme_cold", "heat", "storm"}
        if v not in allowed:
            raise ValueError(f"surge_type must be one of {allowed}")
        return v

    @field_validator("overbooking_multiplier")
    @classmethod
    def cap_multiplier(cls, v: Decimal) -> Decimal:
        if v > _MAX_MULTIPLIER:
            raise ValueError(f"overbooking_multiplier {v} exceeds hard cap of {_MAX_MULTIPLIER}")
        return v

    @field_validator("duration_hours")
    @classmethod
    def cap_duration(cls, v: int) -> int:
        if v < 1 or v > 12:
            raise ValueError("duration_hours must be 1-12")
        return v


class SurgeDeactivateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    tenant_id: str


class SurgeStatusResponse(BaseModel):
    model_config = {"extra": "forbid"}

    tenant_id: str
    surge_active: bool
    surge_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    is_manual: Optional[bool] = None
    overbooking_multiplier: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/activate", status_code=status.HTTP_200_OK)
async def activate_surge_manual(
    body: SurgeActivateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manual override: activate surge mode for a tenant."""
    _flag_check()

    try:
        tenant_uuid = uuid.UUID(body.tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")

    result = await db.execute(select(Contractor).where(Contractor.id == tenant_uuid))
    contractor = result.scalar_one_or_none()
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    now = datetime.now(tz=timezone.utc)
    expires_at = now + timedelta(hours=body.duration_hours)

    record = SurgeModeRecord(
        id=uuid.uuid4(),
        tenant_id=contractor.id,
        alert_id=None,
        surge_type=body.surge_type,
        expires_at=expires_at,
        is_manual=True,
        overbooking_multiplier=body.overbooking_multiplier,
        activated_by_alert_title="Manual override",
    )
    db.add(record)
    contractor.surge_mode_active = True
    contractor.surge_overbooking_multiplier = body.overbooking_multiplier
    await db.commit()

    logger.info(
        "surge_portal: manual activate | tenant=%s surge_type=%s expires_at=%s",
        body.tenant_id, body.surge_type, expires_at.isoformat(),
    )
    return {
        "success": True,
        "surge_type": body.surge_type,
        "expires_at": expires_at.isoformat(),
        "overbooking_multiplier": str(body.overbooking_multiplier),
    }


@router.post("/deactivate", status_code=status.HTTP_200_OK)
async def deactivate_surge_manual(
    body: SurgeDeactivateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manual override: deactivate surge mode for a tenant."""
    _flag_check()

    try:
        tenant_uuid = uuid.UUID(body.tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")

    result = await db.execute(select(Contractor).where(Contractor.id == tenant_uuid))
    contractor = result.scalar_one_or_none()
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    from app.services.weather_surge import WeatherSurgeService
    svc = WeatherSurgeService()
    await svc.deactivate_surge(contractor, db)
    await db.commit()

    logger.info("surge_portal: manual deactivate | tenant=%s", body.tenant_id)
    return {"success": True, "message": "Surge mode deactivated."}


@router.get("/status", status_code=status.HTTP_200_OK, response_model=SurgeStatusResponse)
async def get_surge_status(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> SurgeStatusResponse:
    """Get current surge state for a tenant."""
    _flag_check()

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id UUID")

    result = await db.execute(select(Contractor).where(Contractor.id == tenant_uuid))
    contractor = result.scalar_one_or_none()
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    if not contractor.surge_mode_active:
        return SurgeStatusResponse(
            tenant_id=tenant_id,
            surge_active=False,
        )

    active_result = await db.execute(
        select(SurgeModeRecord).where(
            SurgeModeRecord.tenant_id == tenant_uuid,
            SurgeModeRecord.deactivated_at.is_(None),
        ).order_by(SurgeModeRecord.activated_at.desc()).limit(1)
    )
    record = active_result.scalar_one_or_none()

    if record is None:
        return SurgeStatusResponse(tenant_id=tenant_id, surge_active=False)

    return SurgeStatusResponse(
        tenant_id=tenant_id,
        surge_active=True,
        surge_type=record.surge_type,
        expires_at=record.expires_at,
        activated_at=record.activated_at,
        is_manual=record.is_manual,
        overbooking_multiplier=record.overbooking_multiplier,
    )
