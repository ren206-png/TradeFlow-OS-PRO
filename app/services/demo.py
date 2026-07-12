"""
Demo line service — Summit Plumbing Demo tenant.

Responsibilities:
  - is_demo_call(contractor_id) — fast check used in WebSocket/webhook
  - check_demo_daily_cap()      — enforce daily call cap on demo line
  - log_demo_call()             — write demo call event to demo_calls table
  - provision_demo_tenant()     — one-time setup (run via admin route)
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

DEMO_BUSINESS_NAME = "Summit Plumbing Demo"
DEMO_AGENT_NAME    = "Jordan"
DEMO_TRADE         = "plumbing"
DEMO_SERVICE_AREA  = "Demo City, CA"


def is_demo_call(contractor_id: str) -> bool:
    """Returns True if this call is on the demo tenant."""
    return bool(settings.demo_contractor_id and str(contractor_id) == settings.demo_contractor_id)


async def check_demo_daily_cap(db: AsyncSession) -> bool:
    """Returns True if the demo line is under the daily call cap."""
    from app.models.demo_call import DemoCall
    today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.count(DemoCall.id)).where(DemoCall.started_at >= today_start)
    )
    count = result.scalar_one() or 0
    allowed = count < settings.demo_daily_call_cap
    if not allowed:
        logger.warning("Demo daily cap reached | count=%d cap=%d", count, settings.demo_daily_call_cap)
    return allowed


async def log_demo_call(call_id: str, from_number: str, duration_seconds: int, db: AsyncSession) -> None:
    """Write a demo call record."""
    from app.models.demo_call import DemoCall
    db.add(DemoCall(
        retell_call_id=call_id,
        from_number=from_number,
        duration_seconds=duration_seconds,
    ))
    await db.flush()
    logger.info("Demo call logged | call_id=%s from=%s duration=%ds", call_id, from_number, duration_seconds)


async def provision_demo_tenant(db: AsyncSession) -> dict:
    """
    One-time: create the Summit Plumbing Demo contractor + Retell agent + phone number.
    Returns {"contractor_id": ..., "phone_number": ..., "agent_id": ...}
    Set DEMO_CONTRACTOR_ID and DEMO_PHONE_NUMBER in Railway after running this.
    """
    from app.models.contractor import Contractor
    from app.services.provisioning import provision_contractor
    from app.utils.auth import hash_password

    # Check if demo tenant already exists
    result = await db.execute(
        select(Contractor).where(Contractor.name == DEMO_BUSINESS_NAME)
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info("Demo tenant already exists | id=%s phone=%s", existing.id, existing.phone_number)
        return {
            "contractor_id": str(existing.id),
            "phone_number": existing.phone_number,
            "agent_id": existing.retell_agent_id,
            "already_existed": True,
        }

    contractor = Contractor(
        name=DEMO_BUSINESS_NAME,
        agent_name=DEMO_AGENT_NAME,
        email=f"demo-{secrets.token_hex(4)}@tradesflowos.internal",
        hashed_password=hash_password(secrets.token_hex(32)),  # random, no login
        api_key=secrets.token_hex(32),
        trades=[DEMO_TRADE],
        service_areas=[DEMO_SERVICE_AREA],
        phone_number=f"+10000000000",  # placeholder — overwritten by provisioning
        is_active=True,
        is_verified=True,
        plan="pro",
        sms_enabled=False,          # demo line never sends SMS to callers
        calls_this_month=0,
        sms_this_month=0,
        calendar_provider="manual",
        calendar_config={},
        diagnostic_fee=0.0,
        free_estimate=True,
        timezone="America/Los_Angeles",
    )
    db.add(contractor)
    await db.flush()
    await db.commit()

    result = await provision_contractor(contractor, db)
    if not result.get("success"):
        logger.error("Demo provisioning failed: %s", result)
        return result

    logger.info(
        "Demo tenant provisioned | id=%s phone=%s agent=%s",
        contractor.id, result["phone_number"], result["agent_id"],
    )
    return {
        "contractor_id": str(contractor.id),
        "phone_number": result["phone_number"],
        "agent_id": result["agent_id"],
        "already_existed": False,
    }
