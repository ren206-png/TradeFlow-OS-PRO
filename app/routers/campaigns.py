"""
Phase 4: Campaign management API endpoints.

Endpoints:
  POST /portal/campaigns/pause   — set outbound_paused=True (kill switch)
  POST /portal/campaigns/resume  — set outbound_paused=False
  GET  /portal/campaigns         — list campaigns for this tenant
  GET  /portal/campaigns/{id}/stats — sent/converted counts

All endpoints require portal auth (session cookie).
Tenant isolation enforced: every query filters by tenant_id.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.contractor import Contractor
from app.routers.portal import require_contractor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal/campaigns", tags=["campaigns"])


@router.post("/pause")
async def pause_outbound(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Campaign kill switch: pause all outbound for this tenant.
    Enforced by OutboundGateway within 60 seconds of toggle.
    """
    if contractor is None:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    contractor.outbound_paused = True
    contractor.outbound_paused_at = datetime.now(tz=timezone.utc)
    await db.commit()

    logger.info(
        "campaigns: outbound_paused=True | tenant=%s at=%s",
        contractor.id, contractor.outbound_paused_at.isoformat(),
    )
    return JSONResponse(content={
        "success": True,
        "outbound_paused": True,
        "paused_at": contractor.outbound_paused_at.isoformat(),
    })


@router.post("/resume")
async def resume_outbound(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Resume outbound after a kill-switch pause."""
    if contractor is None:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    contractor.outbound_paused = False
    # outbound_paused_at preserved as audit trail of last pause
    await db.commit()

    logger.info("campaigns: outbound_paused=False (resumed) | tenant=%s", contractor.id)
    return JSONResponse(content={"success": True, "outbound_paused": False})


@router.get("")
async def list_campaigns(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """List all campaigns for this tenant. Tenant-isolated query."""
    if contractor is None:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    result = await db.execute(
        select(Campaign)
        .where(Campaign.tenant_id == contractor.id)
        .order_by(Campaign.created_at.desc())
    )
    campaigns = result.scalars().all()

    return JSONResponse(content={
        "campaigns": [
            {
                "id": str(c.id),
                "name": c.name,
                "campaign_type": c.campaign_type,
                "status": c.status,
                "trade": c.trade,
                "season": c.season,
                "daily_send_cap": c.daily_send_cap,
                "total_sent": c.total_sent,
                "total_converted": c.total_converted,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in campaigns
        ],
        "outbound_paused": contractor.outbound_paused,
    })


@router.get("/{campaign_id}/stats")
async def campaign_stats(
    campaign_id: str,
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Sent/converted counts for a campaign. Tenant-isolated."""
    if contractor is None:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    try:
        cid = uuid.UUID(campaign_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid campaign ID"})

    result = await db.execute(
        select(Campaign).where(
            Campaign.id == cid,
            Campaign.tenant_id == contractor.id,  # tenant isolation
        )
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        return JSONResponse(status_code=404, content={"error": "Campaign not found"})

    # Per-status contact counts
    contacts_result = await db.execute(
        select(CampaignContact).where(CampaignContact.campaign_id == cid)
    )
    contacts = contacts_result.scalars().all()

    status_counts: dict[str, int] = {}
    for c in contacts:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1

    return JSONResponse(content={
        "campaign_id": str(campaign.id),
        "name": campaign.name,
        "status": campaign.status,
        "total_sent": campaign.total_sent,
        "total_converted": campaign.total_converted,
        "conversion_rate": (
            round(campaign.total_converted / campaign.total_sent * 100, 1)
            if campaign.total_sent > 0 else 0.0
        ),
        "contact_statuses": status_counts,
        "daily_send_cap": campaign.daily_send_cap,
    })
