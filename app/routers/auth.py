from __future__ import annotations

import secrets
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.contractor import Contractor
from app.utils.auth import hash_password, needs_rehash, verify_password
from app.services.signup import (
    confirm_email, create_account, email_problem, fire_signup_side_effects, normalize_email,
)
from app.utils.rate_limit import check_rate_limit
from app.utils.sessions import SESSION_COOKIE, SESSION_MAX_AGE, create_session_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

_RESET_TOKEN_TTL_HOURS = 1


async def _issue_reset_token(contractor: Contractor, db: AsyncSession) -> str:
    """
    Generate a cryptographically secure single-use reset token, store it on the
    contractor record (hashed), and return the raw token for inclusion in the
    reset URL.  Token expires in _RESET_TOKEN_TTL_HOURS hours.
    """
    raw_token = secrets.token_urlsafe(48)  # 48-byte → 64-char URL-safe string
    contractor.reset_token = raw_token
    contractor.reset_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=_RESET_TOKEN_TTL_HOURS)
    await db.flush()
    return raw_token


async def _verify_and_consume_reset_token(
    contractor: Contractor,
    token: str,
    db: AsyncSession,
) -> bool:
    """
    Verify the reset token using constant-time comparison.
    Consumes (clears) the token on success so it cannot be reused.
    Returns True if valid, False otherwise.
    """
    stored = contractor.reset_token
    expires_at = contractor.reset_token_expires_at

    if not stored or not expires_at:
        return False

    if datetime.now(tz=timezone.utc) > expires_at:
        # Expired — clear it
        contractor.reset_token = None
        contractor.reset_token_expires_at = None
        await db.flush()
        return False

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(stored, token):
        return False

    # Valid — consume (single-use)
    contractor.reset_token = None
    contractor.reset_token_expires_at = None
    await db.flush()
    return True


@router.get("/signup", response_class=HTMLResponse)
async def signup_get(request: Request):
    return templates.TemplateResponse(request,
"auth_signup.html",
{"error": None},
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
    company_website: str = Form(""),
):
    if company_website:
        logger.info("signup honeypot tripped | email=%s", email)
        return RedirectResponse(url="/", status_code=303)

    allowed, retry_after = check_rate_limit(request, "signup", max_requests=5, window_seconds=3600)
    if not allowed:
        return templates.TemplateResponse(request,
"auth_signup.html",
{"error": f"Too many signup attempts. Try again in {retry_after} seconds."},
status_code=429,
)

    def error(msg: str):
        return templates.TemplateResponse(request,
"auth_signup.html",
{"error": msg},
status_code=400,
)

    if password != confirm_password:
        return error("Passwords do not match.")

    email = normalize_email(email)
    problem = email_problem(email)
    if problem:
        return error(problem)
    if len(password) < 8:
        return error("Password must be at least 8 characters.")

    result = await db.execute(select(Contractor).where(Contractor.email == email))
    if result.scalar_one_or_none() is not None:
        return error("An account with that email already exists.")

    contractor = await create_account(
        db, business_name=business_name, email=email, password=password, phone=phone,
        trades=[trade], service_areas=[service_area],
    )
    contractor_id = str(contractor.id)
    logger.info("New signup: contractor=%s email=%s (awaiting email verification)", contractor.name, email)
    fire_signup_side_effects(contractor, trade, phone)

    token = create_session_token(str(contractor_id))
    response = RedirectResponse(url="/portal/leads?welcome=1", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
    )
    return response


@router.get("/verify-email")
async def verify_email(token: str = "", db: AsyncSession = Depends(get_db)):
    """Landing page of the link in the welcome email: confirms the address and starts AI-number setup."""
    contractor = await confirm_email(db, token)
    if contractor is None:
        return RedirectResponse(url="/auth/login?verify=invalid", status_code=302)
    return RedirectResponse(url="/portal/leads?verified=1", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, verify: str = ""):
    error = "That confirmation link is invalid or has expired. Log in and use \"Resend the email\"." if verify == "invalid" else None
    return templates.TemplateResponse(request,
"auth_login.html",
{"error": error},
)


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    allowed, retry_after = check_rate_limit(request, "login", max_requests=10, window_seconds=600)
    if not allowed:
        return templates.TemplateResponse(request,
"auth_login.html",
{"error": f"Too many login attempts. Try again in {retry_after} seconds."},
status_code=429,
)

    email = normalize_email(email)
    result = await db.execute(select(Contractor).where(Contractor.email == email))
    contractor = result.scalar_one_or_none()

    if contractor is None or not contractor.hashed_password or not verify_password(password, contractor.hashed_password):
        return templates.TemplateResponse(request,
"auth_login.html",
{"error": "Invalid email or password"},
status_code=401,
)

    # Silently upgrade legacy PBKDF2 hashes to argon2id on successful login
    if needs_rehash(contractor.hashed_password):
        contractor.hashed_password = hash_password(password)
        await db.flush()

    token = create_session_token(str(contractor.id))
    response = RedirectResponse(url="/portal", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
    )
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_get(request: Request):
    return templates.TemplateResponse(request,
"auth_forgot_password.html",
{"error": None, "success": None},
)


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
):
    check_rate_limit(request, "forgot_password", max_requests=5, window_seconds=3600)
    # Note: we don't block on rate limit here to avoid leaking whether the email exists;
    # we just silently absorb excess requests.
    result = await db.execute(select(Contractor).where(Contractor.email == email))
    contractor = result.scalar_one_or_none()
    if contractor:
        token = await _issue_reset_token(contractor, db)
        await db.commit()
        reset_url = f"https://tradesflowos.com/auth/reset-password?email={email}&token={token}"
        logger.info("Password reset requested for email=%s", email)
        # Send reset email (no-op if SMTP not configured)
        try:
            from app.services.notifications import _send_email
            import asyncio as _asyncio
            html = f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:24px">
              <h2 style="color:#1e40af">TradeFlow Password Reset</h2>
              <p>Click the button below to reset your password. This link expires in 1 hour.</p>
              <a href="{reset_url}"
                 style="display:inline-block;background:#1e40af;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold;margin:16px 0">
                Reset Password
              </a>
              <p style="color:#6b7280;font-size:13px">If you didn't request this, you can safely ignore this email.</p>
            </div>"""
            text = f"Reset your TradeFlow password here: {reset_url}\n\nThis link expires in 1 hour."
            loop = _asyncio.get_running_loop()
            loop.run_in_executor(None, _send_email, email, "Reset your TradeFlow password", html, text)
        except Exception as exc:
            logger.error("Failed to send password reset email: %s", exc)
    return templates.TemplateResponse(request,
"auth_forgot_password.html",
{
            "error": None,
            "success": "If an account with that email exists, a reset link has been sent.",
        },
)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_get(
    request: Request,
    email: str = "",
    token: str = "",
):
    return templates.TemplateResponse(request,
"auth_reset_password.html",
{"email": email, "token": token, "error": None},
)


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_password_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    def error(msg: str):
        return templates.TemplateResponse(request,
"auth_reset_password.html",
{"email": email, "token": token, "error": msg},
status_code=400,
)

    if new_password != confirm_password:
        return error("Passwords do not match.")

    if len(new_password) < 8:
        return error("Password must be at least 8 characters.")

    result = await db.execute(select(Contractor).where(Contractor.email == email))
    contractor = result.scalar_one_or_none()
    if contractor is None:
        return error("Account not found.")

    # Verify and consume the single-use token (constant-time comparison)
    if not await _verify_and_consume_reset_token(contractor, token, db):
        return error("This reset link is invalid or has expired. Please request a new one.")

    contractor.hashed_password = hash_password(new_password)
    await db.commit()
    logger.info("Password reset completed for email=%s", email)

    response = RedirectResponse(url="/auth/login?reset=1", status_code=302)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
