"""
Shared account creation for /auth/signup and /onboarding, plus email verification.

Accounts start as 'awaiting_verification'. No Retell agent or phone number is bought until the
owner clicks the link in their welcome email, so bots and typo'd addresses cost nothing.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import uuid

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contractor import Contractor
from app.utils.auth import hash_password
from app.utils.phone import normalize_nanp
from app.utils.timefmt import DEFAULT_TZ, timezone_for_phone

logger = logging.getLogger(__name__)

VERIFY_SALT = "email-verify"
VERIFY_MAX_AGE = 7 * 24 * 3600
VERIFY_BASE_URL = "https://tradesflowos.com/auth/verify-email"

# Throwaway-mailbox providers used for fake signups.
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "10minutemail.com", "10minutemail.net",
    "tempmail.com", "temp-mail.org", "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com",
    "getnada.com", "dispostable.com", "maildrop.cc", "fakeinbox.com", "mailnesia.com", "mintemail.com",
    "emailondeck.com", "spamgourmet.com", "mohmal.com", "tempail.com", "burnermail.io", "discard.email",
    "immenseignite.info", "formtests.info",
}


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def email_problem(email: str) -> str | None:
    """Reason the address can't be used, or None."""
    local, _, domain = email.partition("@")
    if not local or "." not in domain or " " in email or len(email) > 254:
        return "Enter a valid email address."
    if domain in DISPOSABLE_DOMAINS:
        return "Please use a real business email address (temporary mailboxes aren't supported)."
    return None


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=VERIFY_SALT)


def make_verify_token(contractor_id: str, email: str) -> str:
    return _serializer().dumps({"id": str(contractor_id), "email": email})


def read_verify_token(token: str) -> dict | None:
    try:
        return _serializer().loads(token, max_age=VERIFY_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def verify_url(contractor_id: str, email: str) -> str:
    return f"{VERIFY_BASE_URL}?token={make_verify_token(contractor_id, email)}"


async def create_account(
    db: AsyncSession, *, business_name: str, email: str, password: str, phone: str,
    trades: list[str], service_areas: list[str], agent_name: str = "Alex",
    diagnostic_fee: float | None = 89.0, attribution: dict | None = None,
) -> Contractor:
    """Create the contractor (committed). Caller has validated input and email uniqueness."""
    owner_phone = normalize_nanp(phone) or phone.strip()
    contractor = Contractor(
        name=business_name.strip(),
        agent_name=agent_name,
        email=email,
        hashed_password=hash_password(password),
        api_key=secrets.token_hex(32),
        trades=trades,
        service_areas=service_areas,
        # Placeholder until the AI number is bought; the column is unique and non-null.
        phone_number=f"pending:{uuid.uuid4().hex[:20]}",
        owner_phone=owner_phone,
        timezone=timezone_for_phone(normalize_nanp(phone)) or DEFAULT_TZ,
        provisioning_status="awaiting_verification",
        attribution=attribution,
        is_active=True,
        is_verified=False,
        plan="starter",
        calendar_provider="manual",
        calendar_config={},
        sms_enabled=True,
        diagnostic_fee=diagnostic_fee,
        free_estimate=False,
    )
    db.add(contractor)
    await db.commit()
    await db.refresh(contractor)
    return contractor


def fire_signup_side_effects(contractor: Contractor, trade: str, phone: str) -> None:
    """Welcome email with the verify link, plus the Mailchimp subscription (fire-and-forget)."""
    from app.services.welcome import send_welcome_email
    asyncio.create_task(send_welcome_email(contractor.email, contractor.name,
                                           verify_url(str(contractor.id), contractor.email)))


async def confirm_email(db: AsyncSession, token: str) -> Contractor | None:
    """Mark the email verified (idempotent) and start provisioning. None if the token is bad."""
    from datetime import datetime, timezone
    data = read_verify_token(token)
    if not data:
        return None
    try:
        cid = uuid.UUID(data["id"])
    except (KeyError, ValueError):
        return None
    contractor = (await db.execute(select(Contractor).where(Contractor.id == cid))).scalar_one_or_none()
    if contractor is None or contractor.email != data.get("email"):
        return None
    if contractor.email_verified_at is None:
        contractor.email_verified_at = datetime.now(tz=timezone.utc)
        contractor.is_verified = True
        await db.commit()
        # Drip sequence starts only for confirmed, real addresses (keeps bots out of the audience)
        from app.services.mailchimp import subscribe_contractor
        asyncio.create_task(subscribe_contractor(
            email=contractor.email, first_name=contractor.name,
            trade=", ".join(contractor.trades or []) or "General", phone=contractor.owner_phone or "", plan="starter"))
    if contractor.provisioning_status in ("awaiting_verification", "queued", "failed"):
        from app.services.provisioning import provision_contractor_by_id
        asyncio.create_task(provision_contractor_by_id(str(contractor.id)))
    return contractor
