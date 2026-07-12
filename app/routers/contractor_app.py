from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.call import CallSession
from app.models.contractor import Contractor
from app.models.lead import Lead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/app", tags=["contractor-app"])

templates = Jinja2Templates(directory="app/templates")

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


class _AuthError(Exception):
    """Internal sentinel used to carry a JSONResponse out of the auth helper."""

    def __init__(self, response: JSONResponse) -> None:
        self.response = response


async def get_contractor_from_request(request: Request, db: AsyncSession) -> Contractor:
    import uuid as _uuid
    from app.utils.sessions import SESSION_COOKIE, decode_session_token

    # 1. X-API-Key header only — never from query params (keys in URLs leak into logs/Referer)
    api_key: Optional[str] = request.headers.get("X-API-Key")

    if api_key:
        result = await db.execute(select(Contractor).where(Contractor.api_key == api_key))
        contractor = result.scalar_one_or_none()
        if contractor is not None and contractor.is_active:
            return contractor
        raise _AuthError(JSONResponse(status_code=401, content={"error": "Invalid API key"}))

    # 3. Session cookie fallback
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        contractor_id = decode_session_token(token)
        if contractor_id:
            try:
                uid = _uuid.UUID(contractor_id)
            except (ValueError, AttributeError):
                raise _AuthError(JSONResponse(status_code=401, content={"error": "Invalid session"}))
            result = await db.execute(select(Contractor).where(Contractor.id == uid))
            contractor = result.scalar_one_or_none()
            if contractor is not None:
                return contractor

    raise _AuthError(JSONResponse(status_code=401, content={"error": "Missing API key"}))


# Auth errors are wrapped in _AuthError (a proper BaseException subclass) so they can be
# raised inside async functions and caught in each route with `except _AuthError as err:
# return err.response`.  We call the helper manually inside each route rather than using
# FastAPI's dependency injection so that HTML routes can return a JSON error response
# instead of an HTTPException-driven 403 page.

# ---------------------------------------------------------------------------
# PWA assets
# ---------------------------------------------------------------------------


@router.get("/manifest.json")
async def pwa_manifest():
    return JSONResponse(
        content={
            "name": "TradeFlow Contractor",
            "short_name": "TradeFlow",
            "start_url": "/app/leads",
            "display": "standalone",
            "background_color": "#1e40af",
            "theme_color": "#1e40af",
            "icons": [
                {
                    "src": "https://via.placeholder.com/192x192/1e40af/ffffff?text=TF",
                    "sizes": "192x192",
                    "type": "image/png",
                }
            ],
        }
    )


@router.get("/sw.js")
async def service_worker():
    js = "self.addEventListener('fetch', function(event) {});"
    return PlainTextResponse(content=js, media_type="application/javascript")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_class=RedirectResponse)
async def app_root(request: Request):
    return RedirectResponse(url="/app/leads", status_code=302)


@router.get("/leads", response_class=HTMLResponse)
async def leads_list(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        contractor = await get_contractor_from_request(request, db)
    except _AuthError as err:
        return err.response

    result = await db.execute(
        select(Lead)
        .where(Lead.contractor_id == contractor.id)
        .order_by(Lead.created_at.desc())
        .limit(50)
    )
    leads = result.scalars().all()

    api_key = ""  # no longer passed in URLs — navigation uses session cookies

    return templates.TemplateResponse(
        "app_leads.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "api_key": api_key,
            "active_nav": "leads",
            "leads": leads,
        },
    )


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
async def lead_detail(lead_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        contractor = await get_contractor_from_request(request, db)
    except _AuthError as err:
        return err.response

    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.contractor_id == contractor.id)
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        return HTMLResponse(content="<h1>Lead not found</h1>", status_code=404)

    api_key = ""  # no longer passed in URLs — navigation uses session cookies

    sentiment_emoji = {"positive": "😊", "neutral": "😐", "negative": "😞"}.get(
        (lead.sentiment or "neutral").lower(), "😐"
    )

    return templates.TemplateResponse(
        "app_lead_detail.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "api_key": api_key,
            "active_nav": "leads",
            "lead": lead,
            "sentiment_emoji": sentiment_emoji,
        },
    )


@router.get("/live", response_class=HTMLResponse)
async def live_calls(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        contractor = await get_contractor_from_request(request, db)
    except _AuthError as err:
        return err.response

    api_key = ""  # no longer passed in URLs — navigation uses session cookies

    return templates.TemplateResponse(
        "app_live.html",
        {
            "request": request,
            "contractor_name": contractor.name,
            "api_key": api_key,
            "active_nav": "live",
        },
    )


@router.get("/events")
async def active_calls_json(request: Request, db: AsyncSession = Depends(get_db)):
    """Polling endpoint — returns JSON list of active calls for this contractor."""
    try:
        contractor = await get_contractor_from_request(request, db)
    except _AuthError as err:
        return err.response

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
