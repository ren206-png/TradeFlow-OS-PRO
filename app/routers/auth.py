from __future__ import annotations

import secrets
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contractor import Contractor
from app.utils.auth import hash_password, verify_password
from app.utils.sessions import SESSION_COOKIE, SESSION_MAX_AGE, create_session_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/signup", response_class=HTMLResponse)
async def signup_get(request: Request):
    return templates.TemplateResponse(
        "auth_signup.html",
        {"request": request, "error": None},
    )


@router.post("/signup", response_class=HTMLResponse)
async def signup_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    trade: str = Form(...),
    phone: str = Form(...),
    service_area: str = Form(...),
):
    def error(msg: str):
        return templates.TemplateResponse(
            "auth_signup.html",
            {"request": request, "error": msg},
            status_code=400,
        )

    if password != confirm_password:
        return error("Passwords do not match.")

    # Check email uniqueness
    result = await db.execute(select(Contractor).where(Contractor.email == email))
    if result.scalar_one_or_none() is not None:
        return error("An account with that email already exists.")

    contractor = Contractor(
        name=business_name,
        email=email,
        hashed_password=hash_password(password),
        api_key=secrets.token_hex(32),
        trades=[trade],
        service_areas=[service_area],
        phone_number=phone,
        is_active=True,
        is_verified=False,
        plan="starter",
        calls_this_month=0,
        sms_this_month=0,
        calendar_provider="manual",
        calendar_config={},
        sms_enabled=True,
        diagnostic_fee=89.0,
        free_estimate=False,
        agent_name="Alex",
    )
    db.add(contractor)
    await db.flush()
    contractor_id = str(contractor.id)

    # --- Auto-provision Retell agent + phone number (fire-and-forget) ---
    import asyncio as _asyncio
    from app.services.provisioning import provision_contractor as _provision
    _asyncio.create_task(_provision(contractor, db))

    await db.commit()

    # --- Subscribe to Mailchimp drip sequence (fire-and-forget) ---
    from app.services.mailchimp import subscribe_contractor as _mc_subscribe
    _asyncio.create_task(
        _mc_subscribe(
            email=email,
            first_name=business_name,
            trade=trade,
            phone=phone,
            plan="starter",
        )
    )

    token = create_session_token(contractor_id)
    response = RedirectResponse(url="/portal/leads?welcome=1", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(
        "auth_login.html",
        {"request": request, "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    result = await db.execute(select(Contractor).where(Contractor.email == email))
    contractor = result.scalar_one_or_none()

    if contractor is None or not contractor.hashed_password or not verify_password(password, contractor.hashed_password):
        return templates.TemplateResponse(
            "auth_login.html",
            {"request": request, "error": "Invalid email or password"},
            status_code=401,
        )

    token = create_session_token(str(contractor.id))
    response = RedirectResponse(url="/portal", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
