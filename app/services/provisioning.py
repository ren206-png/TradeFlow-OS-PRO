"""
Contractor provisioning service.

On signup, automatically:
  1. Create a Retell agent for the contractor
  2. Purchase a US phone number via Retell
  3. Set the inbound webhook URL on the number
  4. Save agent_id + phone_number to the contractor record
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.services.retell_client import RetellClient

logger = logging.getLogger(__name__)

INBOUND_WEBHOOK_URL = "https://tradesflowos.com/retell/inbound"

# Default voice — same as the existing TradeFlow agent
DEFAULT_VOICE_ID = "11labs-Adrian"

# Area codes to try in order when purchasing numbers
AREA_CODE_POOL = ["212", "310", "404", "512", "602", "702", "770", "813", "832", "972"]


async def provision_contractor(contractor, db: AsyncSession) -> dict:
    """
    Full provisioning flow for a new contractor.
    Creates a Retell agent + purchases a phone number.
    Updates contractor record in DB.
    Returns {"success": True, "agent_id": ..., "phone_number": ...}

    NOTE: This runs as a background task AFTER db.commit() in the signup route.
    We re-fetch the contractor from DB to avoid using a detached SQLAlchemy object.
    """
    if not settings.retell_api_key:
        logger.warning("Retell not configured — skipping provisioning for %s", contractor.name)
        return {"success": False, "error": "Retell not configured"}

    # Re-fetch contractor to get a fresh attached instance (avoids DetachedInstanceError)
    from app.models.contractor import Contractor as _Contractor
    contractor_id = contractor.id
    contractor_name = contractor.name  # cache before potential detach
    try:
        result = await db.execute(select(_Contractor).where(_Contractor.id == contractor_id))
        fresh = result.scalar_one_or_none()
        if fresh:
            contractor = fresh
    except Exception:
        pass  # use original contractor object if re-fetch fails

    client = RetellClient()

    # ------------------------------------------------------------------
    # Step 1 — Create Retell agent for this contractor
    # ------------------------------------------------------------------
    agent_config = {
        "agent_name": f"{contractor.name} — {contractor.agent_name or 'Alex'}",
        "response_engine": {
            "type": "custom_llm",
            "llm_websocket_url": f"https://tradesflowos.com/llm-websocket/{{call_id}}",
        },
        "voice_id": DEFAULT_VOICE_ID,
        "language": "en-US",
        "ambient_sound": "office",
        "boosted_keywords": contractor.trades or [],
        "end_call_after_silence_ms": 30000,
        "max_call_duration_ms": 1800000,  # 30 min
        "metadata": {
            "contractor_id": str(contractor.id),
            "contractor_name": contractor.name,
        },
    }

    try:
        agent_resp = await client.create_agent(agent_config)
        agent_id = agent_resp.get("agent_id", "")
        logger.info("Retell agent created | contractor=%s agent_id=%s", contractor.name, agent_id)
    except Exception as exc:
        logger.error("Failed to create Retell agent for %s: %s", contractor.name, exc)
        return {"success": False, "error": f"Agent creation failed: {exc}"}

    # ------------------------------------------------------------------
    # Step 2 — Purchase a phone number
    # ------------------------------------------------------------------
    phone_number = None
    for area_code in AREA_CODE_POOL:
        try:
            num_resp = await client.purchase_phone_number(
                area_code=area_code,
                inbound_webhook_url=INBOUND_WEBHOOK_URL,
            )
            phone_number = num_resp.get("phone_number", "")
            if phone_number:
                logger.info(
                    "Phone number purchased | contractor=%s number=%s area_code=%s",
                    contractor.name, phone_number, area_code,
                )
                break
        except Exception as exc:
            logger.warning("Area code %s failed: %s — trying next", area_code, exc)
            continue

    if not phone_number:
        logger.error("Could not purchase any phone number for %s", contractor.name)
        # Still save the agent_id even if number purchase failed
        contractor.retell_agent_id = agent_id
        await db.commit()
        return {"success": False, "error": "Phone number purchase failed", "agent_id": agent_id}

    # ------------------------------------------------------------------
    # Step 3 — Save to contractor record
    # ------------------------------------------------------------------
    contractor.retell_agent_id = agent_id
    contractor.phone_number = phone_number
    await db.commit()

    logger.info(
        "Contractor provisioned | name=%s agent_id=%s phone=%s",
        contractor.name, agent_id, phone_number,
    )

    return {
        "success": True,
        "agent_id": agent_id,
        "phone_number": phone_number,
    }
