from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.call import CallSession
from app.models.contractor import Contractor
from app.models.lead import Lead

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

security = HTTPBasic()

templates = Jinja2Templates(directory="app/templates")

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    correct_username = secrets.compare_digest(credentials.username.encode(), b"admin")
    correct_password = secrets.compare_digest(
        credentials.password.encode(), settings.secret_key.encode()
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", include_in_schema=False)
async def dashboard_root(_: None = Depends(verify_admin)) -> RedirectResponse:
    return RedirectResponse(url="/dashboard/leads")


@router.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    priority: Optional[str] = Query(None),
    trade: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    _: None = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    page_size = 25

    # Build base query
    stmt = select(Lead)

    if priority:
        stmt = stmt.where(Lead.priority_level == priority)
    if trade:
        stmt = stmt.where(Lead.trade == trade)
    if status:
        stmt = stmt.where(Lead.appointment_status == status)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Lead.caller_name.ilike(like) | Lead.phone.ilike(like)
        )

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total: int = total_result.scalar_one()

    # Fetch page
    stmt = stmt.order_by(Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    leads = result.scalars().all()

    # Distinct trades for filter dropdown
    trades_result = await db.execute(
        select(Lead.trade).distinct().where(Lead.trade.isnot(None)).order_by(Lead.trade)
    )
    trades = [row[0] for row in trades_result.fetchall()]

    stats: dict = {
        "total": total,
        "emergency": 0,
        "booked": 0,
        "avg_close": 0,
    }

    emerg_res = await db.execute(
        select(func.count(Lead.id)).where(Lead.priority_level == "emergency")
    )
    stats["emergency"] = emerg_res.scalar_one() or 0

    booked_res = await db.execute(
        select(func.count(Lead.id)).where(Lead.appointment_status == "booked")
    )
    stats["booked"] = booked_res.scalar_one() or 0

    avg_res = await db.execute(
        select(func.avg(Lead.close_probability)).where(Lead.close_probability.isnot(None))
    )
    avg_val = avg_res.scalar_one()
    stats["avg_close"] = round(avg_val) if avg_val is not None else 0

    return templates.TemplateResponse(
        "leads.html",
        {
            "request": request,
            "active_nav": "leads",
            "leads": leads,
            "stats": stats,
            "trades": trades,
            "filters": {"priority": priority, "trade": trade, "status": status, "q": q},
            "page": page,
            "total": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        },
    )


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
async def lead_detail_page(
    request: Request,
    lead_id: str,
    _: None = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(select(Lead).where(Lead.id == lead_id))
    lead = result.scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Fetch associated call session
    call_result = await db.execute(
        select(CallSession)
        .where(CallSession.retell_call_id == lead.call_id)
        .options(selectinload(CallSession.contractor))
    )
    call_session = call_result.scalar_one_or_none()

    # Extract conversation history
    conversation = []
    if call_session and call_session.conversation_history:
        conversation = call_session.conversation_history
    elif lead.raw_transcript:
        conversation = lead.raw_transcript if isinstance(lead.raw_transcript, list) else []

    return templates.TemplateResponse(
        "lead_detail.html",
        {
            "request": request,
            "active_nav": "leads",
            "lead": lead,
            "call_session": call_session,
            "conversation": conversation,
        },
    )


@router.get("/calls", response_class=HTMLResponse)
async def calls_page(
    request: Request,
    _: None = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(CallSession)
        .options(selectinload(CallSession.contractor))
        .order_by(CallSession.started_at.desc())
        .limit(100)
    )
    sessions = result.scalars().all()

    return templates.TemplateResponse(
        "calls.html",
        {
            "request": request,
            "active_nav": "calls",
            "sessions": sessions,
        },
    )


@router.get("/contractors", response_class=HTMLResponse)
async def contractors_page(
    request: Request,
    _: None = Depends(verify_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(Contractor).order_by(Contractor.name)
    )
    contractors = result.scalars().all()

    return templates.TemplateResponse(
        "contractors.html",
        {
            "request": request,
            "active_nav": "contractors",
            "contractors": contractors,
        },
    )
