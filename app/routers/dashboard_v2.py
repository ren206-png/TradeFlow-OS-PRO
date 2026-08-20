"""
Phase 5: Owner Dashboard V2 — ROI, Call Analytics & Spam Shield.
All routes gated behind owner_dashboard_v2 feature flag (default OFF).
Flag OFF → redirect to existing portal (no regression).
Tenant isolation: every query filters by tenant_id.
Revenue figures always carry is_estimated label.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contractor import Contractor
from app.utils.sessions import SESSION_COOKIE, decode_session_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard_v2"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Auth helper (mirrors portal.py pattern exactly)
# ---------------------------------------------------------------------------

async def require_contractor(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[Contractor]:
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


async def _check_flag(contractor: Contractor, db: AsyncSession) -> bool:
    """Check owner_dashboard_v2 flag per tenant."""
    from app.services.feature_flags import is_enabled
    return await is_enabled(str(contractor.id), "owner_dashboard_v2", db)


def _portal_redirect():
    return RedirectResponse(url="/portal/leads", status_code=302)


def _login_redirect():
    return RedirectResponse(url="/auth/login", status_code=302)


# ---------------------------------------------------------------------------
# HTML Routes
# ---------------------------------------------------------------------------

@router.get("/dashboard/v2", response_class=HTMLResponse)
async def dashboard_v2_overview(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return _login_redirect()
    if not await _check_flag(contractor, db):
        return _portal_redirect()

    from app.services.stats_aggregator import StatsAggregator

    now = datetime.now(tz=timezone.utc)
    year, month = now.year, now.month

    aggregator = StatsAggregator()
    monthly = await aggregator.get_monthly_summary(contractor.id, year, month, db)

    # Spam stats (last 30 days)
    from app.services.spam_shield import SpamShield
    shield = SpamShield()
    spam_stats = await shield.get_shield_stats(contractor.id, 30, db)

    # Revenue always labeled
    assert monthly["is_estimated"], "Revenue is_estimated must be True"

    estimated_revenue_dollars = monthly["estimated_revenue_cents"] / 100

    avg_ticket_set = contractor.avg_ticket_cents is not None and contractor.avg_ticket_cents > 0

    return templates.TemplateResponse(
        "dashboard_v2_overview.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor": contractor,
            "contractor_verified": contractor.is_verified,
            "active_nav": "dashboard",
            "monthly": monthly,
            "estimated_revenue_dollars": estimated_revenue_dollars,
            # is_estimated always propagated to template — never hidden
            "is_estimated": monthly["is_estimated"],
            "spam_stats": spam_stats,
            "avg_ticket_set": avg_ticket_set,
            "year": year,
            "month": month,
        },
    )


@router.get("/dashboard/v2/calls", response_class=HTMLResponse)
async def dashboard_v2_calls(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    trade: Optional[str] = None,
    status: Optional[str] = None,
):
    if contractor is None:
        return _login_redirect()
    if not await _check_flag(contractor, db):
        return _portal_redirect()

    from app.models.lead import Lead
    from sqlalchemy import or_

    # Read from daily_call_stats for summary metrics (not raw table scan)
    from app.models.daily_call_stats import DailyCallStats
    from app.services.stats_aggregator import StatsAggregator

    now = datetime.now(tz=timezone.utc)
    aggregator = StatsAggregator()
    monthly = await aggregator.get_monthly_summary(contractor.id, now.year, now.month, db)

    # Lead list (filtered) — this is fine to query for the list view
    query = select(Lead).where(Lead.contractor_id == contractor.id)
    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            query = query.where(Lead.created_at >= df)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            query = query.where(Lead.created_at < dt)
        except ValueError:
            pass
    if trade:
        query = query.where(Lead.trade == trade)
    if status == "booked":
        query = query.where(Lead.appointment_status == "booked")
    elif status == "not_booked":
        query = query.where(Lead.appointment_status != "booked")
    elif status == "urgent":
        query = query.where(Lead.priority_level.in_(["emergency", "critical"]))

    query = query.order_by(Lead.created_at.desc()).limit(200)
    result = await db.execute(query)
    leads = result.scalars().all()

    # Unique trades for filter dropdown
    trades_result = await db.execute(
        select(Lead.trade).where(Lead.contractor_id == contractor.id, Lead.trade.isnot(None)).distinct()
    )
    available_trades = [r for r in trades_result.scalars().all() if r]

    return templates.TemplateResponse(
        "dashboard_v2_calls.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor_verified": contractor.is_verified,
            "active_nav": "dashboard",
            "leads": leads,
            "monthly": monthly,
            "available_trades": available_trades,
            "filters": {
                "date_from": date_from,
                "date_to": date_to,
                "trade": trade,
                "status": status,
            },
        },
    )


@router.get("/dashboard/v2/calls/{call_id}", response_class=HTMLResponse)
async def dashboard_v2_call_detail(
    call_id: str,
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return _login_redirect()
    if not await _check_flag(contractor, db):
        return _portal_redirect()

    from app.models.lead import Lead

    # Tenant isolation: filter by contractor_id
    result = await db.execute(
        select(Lead).where(Lead.call_id == call_id, Lead.contractor_id == contractor.id)
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        return HTMLResponse(content="<h1>Call not found</h1>", status_code=404)

    return templates.TemplateResponse(
        "dashboard_v2_calls.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor_verified": contractor.is_verified,
            "active_nav": "dashboard",
            "detail_lead": lead,
            "leads": [],
            "monthly": {},
            "available_trades": [],
            "filters": {},
        },
    )


@router.get("/dashboard/v2/campaigns", response_class=HTMLResponse)
async def dashboard_v2_campaigns(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return _login_redirect()
    if not await _check_flag(contractor, db):
        return _portal_redirect()

    from app.models.campaign import Campaign

    result = await db.execute(
        select(Campaign).where(Campaign.tenant_id == contractor.id).order_by(Campaign.created_at.desc())
    )
    campaigns = result.scalars().all()

    return templates.TemplateResponse(
        "dashboard_v2_overview.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor_verified": contractor.is_verified,
            "active_nav": "dashboard",
            "campaigns": campaigns,
            "monthly": {},
            "estimated_revenue_dollars": 0,
            "is_estimated": True,
            "spam_stats": {},
            "avg_ticket_set": False,
        },
    )


@router.get("/dashboard/v2/spam", response_class=HTMLResponse)
async def dashboard_v2_spam(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    if contractor is None:
        return _login_redirect()
    if not await _check_flag(contractor, db):
        return _portal_redirect()

    from app.models.spam_block import SpamBlock
    from app.services.spam_shield import SpamShield

    shield = SpamShield()
    shield_stats = await shield.get_shield_stats(contractor.id, 30, db)

    # List of blocked numbers (tenant-isolated)
    blocks_result = await db.execute(
        select(SpamBlock)
        .where(SpamBlock.tenant_id == contractor.id)
        .order_by(SpamBlock.created_at.desc())
        .limit(200)
    )
    blocks = blocks_result.scalars().all()

    return templates.TemplateResponse(
        "dashboard_v2_spam.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "contractor_verified": contractor.is_verified,
            "active_nav": "dashboard",
            "shield_stats": shield_stats,
            "blocks": blocks,
        },
    )


@router.post("/dashboard/v2/spam/{block_id}/unblock")
async def dashboard_v2_unblock(
    block_id: str,
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
):
    """One-tap false-positive unblock. Sets is_active=False — never deletes."""
    if contractor is None:
        return _login_redirect()
    if not await _check_flag(contractor, db):
        return _portal_redirect()

    from app.services.spam_shield import SpamShield

    try:
        bid = uuid.UUID(block_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid block ID"})

    shield = SpamShield()
    await shield.report_false_positive(bid, contractor.id, db)
    await db.commit()

    return RedirectResponse(url="/dashboard/v2/spam", status_code=303)


# ---------------------------------------------------------------------------
# JSON API — all revenue responses include is_estimated field
# ---------------------------------------------------------------------------

@router.get("/api/v2/stats/monthly")
async def api_v2_stats_monthly(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
    year: Optional[int] = None,
    month: Optional[int] = None,
):
    """
    Monthly summary JSON.
    ADVERSARIAL CHECK: every response MUST include is_estimated on every revenue value.
    """
    if contractor is None:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    if not await _check_flag(contractor, db):
        return JSONResponse(status_code=403, content={"error": "Feature not enabled"})

    now = datetime.now(tz=timezone.utc)
    year = year or now.year
    month = month or now.month

    from app.services.stats_aggregator import StatsAggregator

    aggregator = StatsAggregator()
    summary = await aggregator.get_monthly_summary(contractor.id, year, month, db)

    # Adversarial self-check: is_estimated MUST be present on every revenue field
    assert "is_estimated" in summary, "is_estimated missing from monthly summary — propagation failure"

    return JSONResponse({
        "tenant_id": str(contractor.id),
        "year": year,
        "month": month,
        "calls_answered": summary["calls_answered"],
        "jobs_booked": summary["jobs_booked"],
        # Revenue: always labeled — two fields together, never separated
        "estimated_revenue_cents": summary["estimated_revenue_cents"],
        "is_estimated": summary["is_estimated"],  # REQUIRED — never omit
        "currency": summary["currency"],
        "missed_calls_recovered": summary["missed_calls_recovered"],
        "no_shows_prevented": summary["no_shows_prevented"],
        "booking_rate_pct": summary["booking_rate_pct"],
    })


@router.get("/api/v2/stats/daily")
async def api_v2_stats_daily(
    request: Request,
    contractor: Contractor = Depends(require_contractor),
    db: AsyncSession = Depends(get_db),
    days: int = 30,
):
    """
    Daily breakdown for chart — last N days.
    Every row with revenue carries is_estimated.
    Reads from daily_call_stats only — never raw tables on page load.
    """
    if contractor is None:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    if not await _check_flag(contractor, db):
        return JSONResponse(status_code=403, content={"error": "Feature not enabled"})

    days = max(1, min(days, 365))
    since = date.today() - timedelta(days=days - 1)

    from app.models.daily_call_stats import DailyCallStats

    result = await db.execute(
        select(DailyCallStats)
        .where(
            DailyCallStats.tenant_id == contractor.id,
            DailyCallStats.stat_date >= since,
        )
        .order_by(DailyCallStats.stat_date)
    )
    rows = result.scalars().all()

    data = [
        {
            "stat_date": str(r.stat_date),
            "calls_total": r.calls_total,
            "calls_answered": r.calls_answered,
            "calls_booked": r.calls_booked,
            "calls_spam_blocked": r.calls_spam_blocked,
            "booking_rate_pct": r.booking_rate_pct,
            # Revenue always with is_estimated label
            "estimated_revenue_cents": r.estimated_revenue_cents,
            "is_estimated": r.is_estimated,  # REQUIRED on every row — never omit
            "currency": r.currency,
            "avg_duration_seconds": r.avg_duration_seconds,
        }
        for r in rows
    ]

    return JSONResponse({"tenant_id": str(contractor.id), "days": days, "data": data})
