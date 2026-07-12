"""Outbound follow-up SMS sequence for missed callers (Feature 3)."""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


async def send_followup_sms(lead_id: str, contractor_id: str, touch: int) -> None:
    """Send follow-up SMS touch 1 (1h) or touch 2 (24h) to a missed caller."""
    from app.database import async_session_factory
    from app.models.lead import Lead
    from app.models.contractor import Contractor
    from app.services.sms import SMSService
    from sqlalchemy import select

    async with async_session_factory() as db:
        lead_uuid = uuid.UUID(lead_id) if isinstance(lead_id, str) else lead_id
        contractor_uuid = uuid.UUID(contractor_id) if isinstance(contractor_id, str) else contractor_id

        lead_result = await db.execute(select(Lead).where(Lead.id == lead_uuid))
        lead = lead_result.scalar_one_or_none()
        if not lead:
            logger.warning("followup_sms: lead %s not found", lead_id)
            return

        # Skip if already booked
        if lead.appointment_status == "booked":
            logger.info(
                "followup_sms: lead %s already booked — skipping touch %d", lead_id, touch
            )
            return

        # Skip if no phone
        if not lead.phone:
            logger.info("followup_sms: lead %s has no phone — skipping", lead_id)
            return

        contractor_result = await db.execute(
            select(Contractor).where(Contractor.id == contractor_uuid)
        )
        contractor = contractor_result.scalar_one_or_none()
        if not contractor or not contractor.sms_enabled:
            return

        agent_name = contractor.agent_name or "Alex"
        business_name = contractor.name or "your service provider"
        trade = (lead.trade or "service").lower()

        if touch == 1:
            body = (
                f"Hi, this is {agent_name} from {business_name}. "
                f"You called earlier about your {trade} issue — did you still need help? "
                f"Reply YES and we'll get someone out to you today."
            )
        else:
            body = (
                f"Following up from {business_name} — we still have availability "
                f"for your {trade} job. Give us a call or reply to this message. "
                f"We'd love to help!"
            )

        sms = SMSService(contractor)
        result = await sms._send_async(lead.phone, body, "followup")
        if result.get("success"):
            logger.info(
                "followup_sms: sent touch %d to lead %s (%s)", touch, lead_id, lead.phone
            )
            notes_line = f"\n[Follow-up SMS touch {touch} sent]"
            lead.notes = (lead.notes or "") + notes_line
            await db.flush()
            await db.commit()
        else:
            logger.error(
                "followup_sms: failed touch %d for lead %s: %s",
                touch, lead_id, result.get("error"),
            )
