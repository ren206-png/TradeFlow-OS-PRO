from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contractor import Contractor
from app.utils.auth import hash_password
from app.utils.sessions import SESSION_COOKIE, create_session_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
templates = Jinja2Templates(directory="app/templates")

TRADES = ["Plumbing", "HVAC", "Electrical", "Heating", "Cooling", "General"]


# ---------------------------------------------------------------------------
# GET /onboarding — render the multi-step form
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def onboarding_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "onboarding.html",
        {
            "request": request,
            "errors": {},
            "form": {},
        },
    )


# ---------------------------------------------------------------------------
# POST /onboarding — process submission
# ---------------------------------------------------------------------------

@router.post("", response_class=HTMLResponse)
async def onboarding_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    # Step 1 — Company Info
    company_name: str = Form(""),
    agent_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    phone_number: str = Form(""),
    # Step 2 — Services
    service_areas: Optional[str] = Form(None),
) -> HTMLResponse:
    form_data = await request.form()
    selected_trades: list[str] = list(form_data.getlist("trades"))

    errors: dict[str, str] = {}

    # --- Required field validation ---
    if not company_name.strip():
        errors["company_name"] = "Business name is required."
    if not agent_name.strip():
        errors["agent_name"] = "Owner / contact name is required."
    if not email.strip():
        errors["email"] = "Email address is required."
    elif "@" not in email:
        errors["email"] = "Enter a valid email address."
    if not password:
        errors["password"] = "Password is required."
    elif len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    if not confirm_password:
        errors["confirm_password"] = "Please confirm your password."
    elif password and confirm_password and password != confirm_password:
        errors["confirm_password"] = "Passwords do not match."
    if not phone_number.strip():
        errors["phone_number"] = "Phone number is required."

    def _re_render(extra: Optional[dict] = None) -> HTMLResponse:
        all_errors = {**errors, **(extra or {})}
        return templates.TemplateResponse(
            "onboarding.html",
            {
                "request": request,
                "errors": all_errors,
                "form": dict(form_data),
            },
            status_code=422,
        )

    if errors:
        return _re_render()

    # --- Check email uniqueness ---
    existing = await db.execute(
        select(Contractor).where(Contractor.email == email.strip().lower())
    )
    if existing.scalar_one_or_none() is not None:
        return _re_render({"email": "An account with this email already exists."})

    # --- Build contractor record ---
    hashed_pw = hash_password(password)
    api_key = secrets.token_urlsafe(32)
    areas = [a.strip() for a in (service_areas or "").split(",") if a.strip()]

    contractor = Contractor(
        name=company_name.strip(),
        agent_name=agent_name.strip(),
        email=email.strip().lower(),
        hashed_password=hashed_pw,
        phone_number=phone_number.strip(),
        trades=selected_trades,
        service_areas=areas,
        api_key=api_key,
        plan="starter",
        is_active=True,
    )
    db.add(contractor)
    await db.commit()
    await db.refresh(contractor)

    # --- Set session cookie so they're logged in immediately ---
    session_token = create_session_token(str(contractor.id))
    response = RedirectResponse(url="/portal/leads?welcome=1", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    return response
