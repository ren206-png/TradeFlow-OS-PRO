from __future__ import annotations

import logging
import uuid
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PLAN_LIMITS
from app.database import get_db
from app.models.call import CallSession
from app.models.contractor import Contractor
from app.models.lead import Lead
from app.utils.sessions import SESSION_COOKIE, decode_session_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal", tags=["portal"])
templates = Jinja2Templates(directory="app/templates")


async def require_contractor(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    token = request.cookies.get(SESSION_COOKIE)
    contractor_id = decode_session_token(token) if token else None
    if not contractor_id:
        return None
    try:
        uid = uuid.UUID(contractor_id)
    except (ValueError, AttributeError):
        return None
    result = await db.execute(select(Contractor).where(Contractor.id == uid))
    contractor = result.scalar_one_or_none()
    if contractor is None or not contractor.is_active:
        return None
    return contractor


@router.get("", response_class=RedirectResponse)
async def portal_root():
    return RedirectResponse(url="/portal/leads", status_code=302)


@router.get("/leads", response_class=HTMLResponse)
async def portal_leads(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
    welcome: Optional[str] = None,
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    result = await db.execute(
        select(Lead)
        .where(Lead.contractor_id == contractor.id)
        .order_by(Lead.created_at.desc())
        .limit(50)
    )
    leads = result.scalars().all()

    flash = "Welcome to TradeFlow! Your account is ready." if welcome == "1" else None

    return templates.TemplateResponse(
        "portal_leads.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "active_nav": "leads",
            "leads": leads,
            "flash": flash,
        },
    )


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
async def portal_lead_detail(
    lead_id: str,
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.contractor_id == contractor.id)
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        return HTMLResponse(content="<h1>Lead not found</h1>", status_code=404)

    sentiment_emoji = {"positive": "😊", "neutral": "😐", "negative": "😞"}.get(
        (lead.sentiment or "neutral").lower(), "😐"
    )

    return templates.TemplateResponse(
        "portal_lead_detail.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "active_nav": "leads",
            "lead": lead,
            "sentiment_emoji": sentiment_emoji,
            "back_url": "/portal/leads",
        },
    )


@router.post("/leads/{lead_id}/update", response_class=RedirectResponse)
async def portal_lead_update(
    lead_id: str,
    request: Request,
    status: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.contractor_id == contractor.id)
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        return RedirectResponse(url="/portal/leads", status_code=302)

    if status:
        lead.appointment_status = status
    if notes is not None:
        lead.notes = notes

    await db.commit()
    return RedirectResponse(url=f"/portal/leads/{lead_id}", status_code=302)


@router.get("/live", response_class=HTMLResponse)
async def portal_live(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    return templates.TemplateResponse(
        "portal_live.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "active_nav": "live",
            "back_url": "/portal/leads",
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def portal_settings(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    limits = PLAN_LIMITS.get(contractor.plan, PLAN_LIMITS["starter"])

    return templates.TemplateResponse(
        "portal_settings.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "active_nav": "settings",
            "contractor": contractor,
            "plan_limits": limits,
            "back_url": "/portal/leads",
        },
    )


@router.get("/events")
async def portal_events(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    result = await db.execute(
        select(CallSession, Lead)
        .outerjoin(Lead, CallSession.lead_id == Lead.id)
        .where(CallSession.contractor_id == contractor.id, CallSession.status == "active")
    )
    rows = result.all()

    now = datetime.now(tz=timezone.utc)
    calls = []
    for session, lead in rows:
        started = session.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = int((now - started).total_seconds())
        calls.append(
            {
                "call_id": session.retell_call_id,
                "elapsed_seconds": elapsed,
                "caller_name": lead.caller_name if lead else "Unknown",
                "trade": lead.trade if lead else "",
            }
        )

    return JSONResponse(content={"calls": calls})
