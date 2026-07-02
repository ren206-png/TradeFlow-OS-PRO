from __future__ import annotations

import logging
import uuid
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
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
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    query = select(Lead).where(Lead.contractor_id == contractor.id)
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(
                Lead.caller_name.ilike(term),
                Lead.phone.ilike(term),
                Lead.trade.ilike(term),
                Lead.problem_summary.ilike(term),
            )
        )
    if status_filter and status_filter != "all":
        query = query.where(Lead.appointment_status == status_filter)
    query = query.order_by(Lead.created_at.desc()).limit(100)
    result = await db.execute(query)
    leads = result.scalars().all()

    flash = "Welcome to TradeFlow! Your account is ready." if welcome == "1" else None

    return templates.TemplateResponse(
        "portal_leads.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor": contractor,
            "contractor_verified": contractor.is_verified,
            "active_nav": "leads",
            "leads": leads,
            "flash": flash,
            "search": search,
            "status_filter": status_filter,
        },
    )


@router.get("/leads/export/csv")
async def portal_leads_export(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    import csv
    import io
    from fastapi.responses import StreamingResponse

    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    result = await db.execute(
        select(Lead)
        .where(Lead.contractor_id == contractor.id)
        .order_by(Lead.created_at.desc())
    )
    leads = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date", "Name", "Phone", "Trade", "Problem", "Status",
        "Priority", "Emergency Score", "Revenue Score",
        "Address", "City", "Notes"
    ])
    for lead in leads:
        writer.writerow([
            lead.created_at.strftime("%Y-%m-%d %H:%M") if lead.created_at else "",
            lead.caller_name or "",
            lead.phone or "",
            lead.trade or "",
            lead.problem_summary or "",
            lead.appointment_status or "",
            lead.priority_level or "",
            lead.emergency_score or "",
            lead.revenue_score or "",
            lead.service_address or "",
            lead.city or "",
            lead.notes or "",
        ])

    output.seek(0)
    filename = f"tradeflow-leads-{contractor.name.replace(' ', '-').lower()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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
            "contractor_verified": contractor.is_verified,
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


@router.get("/analytics", response_class=HTMLResponse)
async def portal_analytics(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    now = datetime.now(tz=timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # All leads for this contractor this month
    result = await db.execute(
        select(Lead).where(
            Lead.contractor_id == contractor.id,
            Lead.created_at >= month_start,
        )
    )
    leads_month = result.scalars().all()

    # All-time leads
    result_all = await db.execute(
        select(Lead).where(Lead.contractor_id == contractor.id)
    )
    leads_all = result_all.scalars().all()

    total_month = len(leads_month)
    booked_month = sum(1 for l in leads_month if l.appointment_status == "booked")
    called_month = sum(1 for l in leads_month if l.appointment_status in ("called", "booked", "lost", "follow_up"))
    emergency_month = sum(1 for l in leads_month if l.priority_level == "emergency")

    booking_rate = round((booked_month / total_month * 100) if total_month else 0)
    answer_rate = round((called_month / total_month * 100) if total_month else 0)

    # Revenue pipeline (revenue_score * avg_job_value proxy $450)
    avg_job = 450
    pipeline_value = sum((l.revenue_score or 0) * avg_job / 10 for l in leads_month)

    # Leads by status breakdown
    status_counts = {}
    for l in leads_all:
        s = l.appointment_status or "new"
        status_counts[s] = status_counts.get(s, 0) + 1

    # Trade breakdown this month
    trade_counts = {}
    for l in leads_month:
        t = l.trade or "Unknown"
        trade_counts[t] = trade_counts.get(t, 0) + 1

    # Last 7 days daily lead counts
    from datetime import timedelta
    daily_labels = []
    daily_counts = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        count = sum(
            1 for l in leads_all
            if l.created_at and (
                l.created_at.replace(tzinfo=timezone.utc) if l.created_at.tzinfo is None else l.created_at
            ) >= day_start and (
                l.created_at.replace(tzinfo=timezone.utc) if l.created_at.tzinfo is None else l.created_at
            ) <= day_end
        )
        daily_labels.append(day.strftime("%a"))
        daily_counts.append(count)

    # Top leads by revenue score
    top_leads = sorted(leads_month, key=lambda l: l.revenue_score or 0, reverse=True)[:5]

    return templates.TemplateResponse(
        "portal_analytics.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor_verified": contractor.is_verified,
            "active_nav": "analytics",
            "total_month": total_month,
            "booked_month": booked_month,
            "emergency_month": emergency_month,
            "booking_rate": booking_rate,
            "answer_rate": answer_rate,
            "pipeline_value": int(pipeline_value),
            "calls_this_month": contractor.calls_this_month or 0,
            "status_counts": status_counts,
            "trade_counts": trade_counts,
            "daily_labels": daily_labels,
            "daily_counts": daily_counts,
            "top_leads": top_leads,
        },
    )


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
            "contractor_verified": contractor.is_verified,
            "active_nav": "live",
            "back_url": "/portal/leads",
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def portal_settings(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    saved: Optional[str] = Query(None),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    limits = PLAN_LIMITS.get(contractor.plan, PLAN_LIMITS["starter"])

    return templates.TemplateResponse(
        "portal_settings.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor_verified": contractor.is_verified,
            "active_nav": "settings",
            "contractor": contractor,
            "plan_limits": limits,
            "saved": saved == "1",
        },
    )


@router.post("/settings/update", response_class=RedirectResponse)
async def portal_settings_update(
    request: Request,
    name: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    service_areas: Optional[str] = Form(None),
    timezone: Optional[str] = Form(None),
    agent_name: Optional[str] = Form(None),
    diagnostic_fee: Optional[str] = Form(None),
    free_estimate: Optional[str] = Form(None),
    sms_enabled: Optional[str] = Form(None),
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    if name and name.strip():
        contractor.name = name.strip()
    if phone_number is not None:
        contractor.phone_number = phone_number.strip()
    if service_areas is not None:
        contractor.service_areas = [a.strip() for a in service_areas.split(",") if a.strip()]
    if timezone:
        contractor.timezone = timezone
    if agent_name and agent_name.strip():
        contractor.agent_name = agent_name.strip()
    if diagnostic_fee is not None:
        try:
            contractor.diagnostic_fee = float(diagnostic_fee)
        except ValueError:
            pass
    contractor.free_estimate = free_estimate == "1"
    contractor.sms_enabled = sms_enabled == "1"

    await db.commit()
    return RedirectResponse(url="/portal/settings?saved=1", status_code=302)


@router.get("/setup", response_class=HTMLResponse)
async def portal_setup(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
):
    if contractor is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    return templates.TemplateResponse(
        "portal_setup.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor_verified": contractor.is_verified,
            "active_nav": "setup",
            "contractor": contractor,
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
