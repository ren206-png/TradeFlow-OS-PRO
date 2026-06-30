from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.contractor import Contractor
from app.services.retell_client import RetellClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
templates = Jinja2Templates(directory="app/templates")

_COOKIE_NAME = "onboarding_api_key"
_RETELL_LLM_ID = "llm_79d99a4e3c07b1d222b3120b692d"

VOICE_MAP = {
    "11labs-Adrian": "Adrian — Professional Male",
    "11labs-Ava": "Ava — Professional Female",
}

TIMEZONES = [
    "America/Edmonton",
    "America/Vancouver",
    "America/Toronto",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
]

TRADES = [
    "Plumbing",
    "HVAC",
    "Roofing",
    "Electrical",
    "Garage Door",
    "Locksmith",
    "Towing",
]


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt="onboarding-api-key")


# ---------------------------------------------------------------------------
# GET /onboarding — render the form
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
async def onboarding_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "onboarding.html",
        {
            "request": request,
            "timezones": TIMEZONES,
            "trades": TRADES,
            "voice_map": VOICE_MAP,
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
    # Step 1
    company_name: str = Form(...),
    agent_name: str = Form("Alex"),
    phone_number: str = Form(...),
    timezone: str = Form("America/New_York"),
    # Step 2
    service_areas: Optional[str] = Form(None),
    # Step 3
    diagnostic_fee: Optional[str] = Form(None),
    free_estimate: Optional[str] = Form(None),
    sms_enabled: Optional[str] = Form(None),
    review_link: Optional[str] = Form(None),
    voice_id: str = Form("11labs-Adrian"),
) -> HTMLResponse:
    form_data = await request.form()
    selected_trades = form_data.getlist("trades")

    errors: dict = {}

    # --- Validate required fields ---
    if not company_name.strip():
        errors["company_name"] = "Company name is required."
    if not agent_name.strip():
        errors["agent_name"] = "Agent name is required."
    if not phone_number.strip():
        errors["phone_number"] = "Phone number is required."

    def _re_render(extra_errors: Optional[dict] = None) -> HTMLResponse:
        all_errors = {**errors, **(extra_errors or {})}
        return templates.TemplateResponse(
            "onboarding.html",
            {
                "request": request,
                "timezones": TIMEZONES,
                "trades": TRADES,
                "voice_map": VOICE_MAP,
                "errors": all_errors,
                "form": dict(form_data),
            },
            status_code=422,
        )

    if errors:
        return _re_render()

    # --- Check phone uniqueness ---
    existing = await db.execute(
        select(Contractor).where(Contractor.phone_number == phone_number.strip())
    )
    if existing.scalar_one_or_none() is not None:
        return _re_render({"phone_number": "This phone number is already registered."})

    # --- Parse optional fields ---
    fee: Optional[float] = None
    if diagnostic_fee and diagnostic_fee.strip():
        try:
            fee = float(diagnostic_fee.strip().lstrip("$"))
        except ValueError:
            pass

    areas = [a.strip() for a in (service_areas or "").split(",") if a.strip()]

    # --- Generate API key ---
    api_key = secrets.token_hex(32)

    # --- Create contractor record ---
    contractor = Contractor(
        name=company_name.strip(),
        agent_name=agent_name.strip(),
        phone_number=phone_number.strip(),
        api_key=api_key,
        trades=selected_trades,
        service_areas=areas,
        timezone=timezone,
        diagnostic_fee=fee,
        free_estimate=bool(free_estimate),
        sms_enabled=bool(sms_enabled) if sms_enabled is not None else True,
        review_link=review_link.strip() if review_link and review_link.strip() else None,
    )
    db.add(contractor)
    await db.flush()  # get contractor.id without committing

    # --- Provision Retell agent ---
    retell_agent_id: Optional[str] = None
    try:
        retell = RetellClient()
        agent_data = await retell.create_agent({
            "agent_name": f"{agent_name.strip()} — {company_name.strip()}",
            "voice_id": voice_id if voice_id in VOICE_MAP else "11labs-Adrian",
            "response_engine": {
                "type": "retell-llm",
                "llm_id": _RETELL_LLM_ID,
            },
            "language": "en-US",
            "enable_backchannel": True,
            "responsiveness": 1.0,
            "interruption_sensitivity": 0.8,
        })
        retell_agent_id = agent_data.get("agent_id")
    except Exception as exc:
        logger.error("Retell agent creation failed for contractor %s: %s", contractor.id, exc)

    contractor.retell_agent_id = retell_agent_id
    await db.commit()
    await db.refresh(contractor)

    # --- Sign the API key in a cookie ---
    signer = _signer()
    signed = signer.dumps(api_key)

    contractor_id = str(contractor.id)
    response = RedirectResponse(
        url=f"/onboarding/success/{contractor_id}", status_code=303
    )
    response.set_cookie(
        key=_COOKIE_NAME,
        value=signed,
        httponly=True,
        max_age=300,  # 5 minutes — shown once
        samesite="lax",
    )
    return response


# ---------------------------------------------------------------------------
# GET /onboarding/success/{contractor_id}
# ---------------------------------------------------------------------------

@router.get("/success/{contractor_id}", response_class=HTMLResponse)
async def onboarding_success(
    request: Request,
    contractor_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    # Fetch contractor
    result = await db.execute(
        select(Contractor).where(Contractor.id == contractor_id)
    )
    contractor = result.scalar_one_or_none()

    # Read & delete the signed API key cookie
    api_key: Optional[str] = None
    signed_cookie = request.cookies.get(_COOKIE_NAME)
    if signed_cookie:
        try:
            signer = _signer()
            api_key = signer.loads(signed_cookie)
        except (BadSignature, SignatureExpired):
            api_key = None

    response = templates.TemplateResponse(
        "onboarding_success.html",
        {
            "request": request,
            "contractor": contractor,
            "api_key": api_key,
            "contractor_id": contractor_id,
        },
    )
    # Delete the cookie so the key is shown only once
    response.delete_cookie(_COOKIE_NAME)
    return response
