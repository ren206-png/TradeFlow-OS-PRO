"""
Contractor provisioning: a Retell agent plus a phone number (the contractor's "AI line").

New signups are provisioned only after they confirm their email, so bots can't run up costs. The flow:

  awaiting_verification --(email confirmed)--> provisioning --> active
                                                   |-- daily cap reached --> queued   (retried by the scheduler)
                                                   '-- Retell error ------> failed   (retried up to MAX_ATTEMPTS)

Numbers are bought in the owner's country and area code first (Canadian owners get Canadian numbers),
then other area codes in the same region.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.retell_client import RetellClient
from app.utils.timefmt import area_codes_in_zone, timezone_for_phone

logger = logging.getLogger(__name__)

INBOUND_WEBHOOK_URL = "https://tradesflowos.com/retell/inbound"
CALL_EVENTS_WEBHOOK_URL = "https://api.tradesflowos.com/retell/webhook"
LLM_WEBSOCKET_URL = "wss://api.tradesflowos.com/llm-websocket"  # Retell appends /{call_id}

DEFAULT_VOICE_ID = "11labs-Adrian"

US_AREA_CODE_POOL = ["212", "310", "404", "512", "602", "702", "770", "813", "832", "972"]
CA_FALLBACK_AREA_CODES = ["587", "780", "403", "604", "416", "647", "514", "613", "204", "306", "902"]

MAX_ATTEMPTS = 4
STALE_PROVISIONING_MINUTES = 15
MAX_AREA_CODES_TRIED = 10


def number_search_plan(owner_phone: str | None) -> tuple[str, list[str]]:
    """(country_code, area codes to try in order): the owner's own area code first, then nearby ones."""
    digits = "".join(ch for ch in (owner_phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    own = digits[:3] if len(digits) == 10 else ""

    tz = timezone_for_phone(owner_phone)
    if tz:  # Canadian owner
        candidates = [own, *area_codes_in_zone(tz), *CA_FALLBACK_AREA_CODES]
        country = "CA"
    else:
        candidates = [own, *US_AREA_CODE_POOL]
        country = "US"
    seen: list[str] = []
    for code in candidates:
        if code and code not in seen:
            seen.append(code)
    return country, seen[:MAX_AREA_CODES_TRIED]


def _utc_day_start() -> datetime:
    return datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def _numbers_bought_today(db: AsyncSession) -> int:
    from app.models.contractor import Contractor
    return (await db.execute(
        select(func.count()).select_from(Contractor).where(Contractor.provisioned_at >= _utc_day_start())
    )).scalar_one()


async def provision_contractor_by_id(contractor_id: str) -> dict:
    """
    Background-task entry point (opens its own DB session). Safe to call repeatedly: it atomically
    claims the account, so double clicks and scheduler retries never buy two numbers.
    """
    from app.database import async_session_factory
    from app.models.contractor import Contractor

    cid = uuid.UUID(contractor_id)
    stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=STALE_PROVISIONING_MINUTES)
    async with async_session_factory() as db:
        claimed = await db.execute(
            update(Contractor)
            .where(
                Contractor.id == cid,
                Contractor.email_verified_at.isnot(None),
                or_(
                    Contractor.provisioning_status.in_(("awaiting_verification", "queued", "failed")),
                    (Contractor.provisioning_status == "provisioning") & (Contractor.updated_at < stale_cutoff),
                ),
                Contractor.provisioning_attempts < MAX_ATTEMPTS,
            )
            .values(provisioning_status="provisioning")
        )
        await db.commit()
        if claimed.rowcount != 1:
            logger.info("provisioning: nothing to do for %s", contractor_id)
            return {"success": False, "error": "Not eligible for provisioning"}

        contractor = (await db.execute(select(Contractor).where(Contractor.id == cid))).scalar_one()

        if await _numbers_bought_today(db) >= settings.provisioning_daily_cap:
            first_time = contractor.provisioning_error != "daily_cap"
            contractor.provisioning_status = "queued"
            contractor.provisioning_error = "daily_cap"
            await db.commit()
            if first_time:
                await _alert_admin(
                    "Daily AI-number cap reached",
                    f"{contractor.name} <{contractor.email}> is waiting for an AI number. "
                    f"The cap is {settings.provisioning_daily_cap} per UTC day; they are queued and will be "
                    "provisioned automatically tomorrow, or raise PROVISIONING_DAILY_CAP in Railway.",
                )
            return {"success": False, "error": "daily_cap"}

        return await provision_contractor(contractor, db)


async def provision_contractor(contractor, db: AsyncSession, *, reuse_agent: bool = True) -> dict:
    """
    Create the Retell agent (unless one exists) and buy a number. Updates the contractor row.
    Returns {"success": True, "agent_id": ..., "phone_number": ...} or {"success": False, "error": ...}.
    """
    if not settings.retell_api_key:
        logger.warning("Retell not configured — skipping provisioning for %s", contractor.name)
        return {"success": False, "error": "Retell not configured"}

    client = RetellClient()

    async def _fail(error: str, agent_id: str | None = None) -> dict:
        contractor.provisioning_status = "failed"
        contractor.provisioning_attempts = (contractor.provisioning_attempts or 0) + 1
        contractor.provisioning_error = error[:255]
        if agent_id:
            contractor.retell_agent_id = agent_id
        await db.commit()
        await _alert_admin(
            f"AI number setup failed for {contractor.name}",
            f"{contractor.name} <{contractor.email}>: {error}\n"
            f"Attempt {contractor.provisioning_attempts} of {MAX_ATTEMPTS}; the scheduler retries every 30 minutes.",
        )
        return {"success": False, "error": error, "agent_id": agent_id}

    # Step 1 — the Retell agent
    agent_id = contractor.retell_agent_id if (reuse_agent and contractor.retell_agent_id) else ""
    if not agent_id:
        agent_config = {
            "agent_name": f"{contractor.name} — {contractor.agent_name or 'Alex'}",
            "response_engine": {"type": "custom-llm", "llm_websocket_url": LLM_WEBSOCKET_URL},
            # call_started / call_ended / call_analyzed events: without this, calls are never finalised or summarised
            "webhook_url": CALL_EVENTS_WEBHOOK_URL,
            # Same voice/language as every live agent. (The multilang voice id isn't available on our Retell
            # account: with MULTILANG_ENABLED=true in production, create-agent returned 404 for every signup.)
            "voice_id": DEFAULT_VOICE_ID,
            "language": "en-US",
            "boosted_keywords": contractor.trades or [],
            "end_call_after_silence_ms": 30000,
            "max_call_duration_ms": 1800000,  # 30 min
            "normalize_for_speech": True,
            "enable_backchannel": False,
            "interruption_sensitivity": 0.8,
            "stt_mode": "accurate",
            "volume": 0.9,
            "metadata": {"contractor_id": str(contractor.id), "contractor_name": contractor.name},
        }
        try:
            agent_id = (await client.create_agent(agent_config)).get("agent_id", "")
            logger.info("Retell agent created | contractor=%s agent_id=%s", contractor.name, agent_id)
        except Exception as exc:
            logger.error("Failed to create Retell agent for %s: %s", contractor.name, exc)
            return await _fail(f"Agent creation failed: {exc}")

    # Step 2 — a phone number in the owner's country and area
    country, area_codes = number_search_plan(contractor.owner_phone)
    phone_number = ""
    for area_code in area_codes:
        try:
            resp = await client.purchase_phone_number(
                area_code=area_code,
                inbound_webhook_url=INBOUND_WEBHOOK_URL,
                country_code=country,
                nickname=f"{contractor.name} — AI line",
            )
            phone_number = resp.get("phone_number", "")
            if phone_number:
                logger.info("Phone number purchased | contractor=%s number=%s country=%s area=%s",
                            contractor.name, phone_number, country, area_code)
                break
        except Exception as exc:
            logger.warning("Area code %s (%s) unavailable: %s", area_code, country, exc)

    if not phone_number:
        return await _fail(f"No {country} number available in areas {', '.join(area_codes)}", agent_id)

    # Step 3 — save
    contractor.retell_agent_id = agent_id
    contractor.phone_number = phone_number
    contractor.provisioning_status = "active"
    contractor.provisioned_at = datetime.now(tz=timezone.utc)
    contractor.provisioning_error = None
    await db.commit()
    logger.info("Contractor provisioned | name=%s agent_id=%s phone=%s", contractor.name, agent_id, phone_number)

    if contractor.email:
        from app.services.welcome import send_number_ready_email
        asyncio.create_task(send_number_ready_email(contractor.email, contractor.name, phone_number))
    return {"success": True, "agent_id": agent_id, "phone_number": phone_number}


async def retry_pending_provisioning() -> int:
    """Scheduler job: retry queued/failed/stuck accounts (verified emails only). Returns how many were tried."""
    from app.database import async_session_factory
    from app.models.contractor import Contractor

    stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=STALE_PROVISIONING_MINUTES)
    async with async_session_factory() as db:
        ids = [str(i) for i in (await db.execute(
            select(Contractor.id).where(
                Contractor.email_verified_at.isnot(None),
                Contractor.provisioning_attempts < MAX_ATTEMPTS,
                or_(
                    Contractor.provisioning_status.in_(("queued", "failed")),
                    (Contractor.provisioning_status == "provisioning") & (Contractor.updated_at < stale_cutoff),
                ),
            ).limit(10)
        )).scalars().all()]
    for contractor_id in ids:
        try:
            await provision_contractor_by_id(contractor_id)
        except Exception as exc:
            logger.error("provisioning retry failed | contractor=%s err=%s", contractor_id, exc)
    return len(ids)


async def _alert_admin(subject: str, body: str) -> None:
    try:
        from app.services.notifications import notify_admin
        await notify_admin(subject, body)
    except Exception as exc:
        logger.error("admin alert failed | subject=%s err=%s", subject, exc)
