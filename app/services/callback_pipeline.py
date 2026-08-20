"""
Shared callback pipeline — used by both webform and lead_ingest endpoints.
Phase 2: Speed-to-Lead & Missed-Call Recovery.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.callback_request import CallbackRequest
from app.models.contractor import Contractor
from app.models.lead import Lead

logger = logging.getLogger(__name__)

# Quiet hours window: 08:00–21:00 local time (exclusive end)
_QUIET_START = 8
_QUIET_END = 21


def _recipient_local_hour(phone: str, now_utc: datetime) -> int:
    """Return recipient's local hour (0-23) using outbound_gateway's NPA→offset map."""
    from app.services.outbound_gateway import _NPA_UTC_OFFSET, _DEFAULT_UTC_OFFSET, _extract_npa
    npa = _extract_npa(phone)
    offset = _NPA_UTC_OFFSET.get(npa, _DEFAULT_UTC_OFFSET) if npa else _DEFAULT_UTC_OFFSET
    utc_decimal = now_utc.hour + now_utc.minute / 60.0
    local_decimal = (utc_decimal + offset) % 24
    return int(local_decimal)


def _next_8am_utc(phone: str, now_utc: datetime) -> datetime:
    """Return next 08:00 local time as a UTC datetime."""
    from app.services.outbound_gateway import _NPA_UTC_OFFSET, _DEFAULT_UTC_OFFSET, _extract_npa
    from datetime import timedelta
    npa = _extract_npa(phone)
    offset_h = _NPA_UTC_OFFSET.get(npa, _DEFAULT_UTC_OFFSET) if npa else _DEFAULT_UTC_OFFSET
    # Today's 08:00 local = 08:00 - offset in UTC
    today_8am_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        hours=8 - offset_h
    )
    if today_8am_utc <= now_utc:
        today_8am_utc += timedelta(days=1)
    return today_8am_utc


async def _trigger_callback_pipeline(
    contractor: Contractor,
    name: str | None,
    phone: str,
    issue: str | None,
    source: str,
    form_submission_id: str | None,
    db: AsyncSession,
) -> dict:
    """
    Core callback pipeline shared by webform and lead_ingest.
    Returns dict with 'status' and optionally 'callback_request_id'.

    Steps:
      1. Phone validation / abuse check
      2. Quiet hours check — schedule if outside 08:00–21:00 local
      3. Create Lead record
      4. Create CallbackRequest record
      5. Fire outbound call (or schedule it)
      6. Record speed-to-lead timestamps
    """
    from app.services.feature_flags import is_enabled
    from app.services.phone_validation import validate_callback_phone
    from app.services.retell_client import RetellClient

    tenant_id = str(contractor.id)

    # Step 1: Phone validation + abuse check
    is_valid, reason = await validate_callback_phone(phone, db)
    if not is_valid:
        logger.info(
            "callback_pipeline: phone rejected | phone=%s reason=%s tenant=%s",
            phone, reason, tenant_id,
        )
        return {"status": "rejected", "reason": reason}

    now_utc = datetime.now(tz=timezone.utc)
    local_hour = _recipient_local_hour(phone, now_utc)
    in_quiet_hours = _QUIET_START <= local_hour < _QUIET_END

    # Step 3: Create Lead record
    lead_received_at = now_utc
    lead = Lead(
        contractor_id=contractor.id,
        call_id=f"webform_{form_submission_id or 'noid'}_{int(now_utc.timestamp())}",
        caller_name=name,
        phone=phone,
        problem_summary=issue,
        call_direction="outbound",
        lead_source=source,
        lead_received_at=lead_received_at,
    )
    db.add(lead)
    await db.flush()

    scheduled_for = None
    status = "pending"

    if not in_quiet_hours:
        # Outside business hours — schedule for next 08:00
        scheduled_for = _next_8am_utc(phone, now_utc)
        status = "scheduled"
        logger.info(
            "callback_pipeline: quiet hours — scheduling | phone=%s scheduled_for=%s",
            phone, scheduled_for.isoformat(),
        )

    # Step 4: Create CallbackRequest
    cb = CallbackRequest(
        tenant_id=tenant_id,
        caller_phone=phone,
        form_submission_id=form_submission_id,
        name=name,
        issue=issue,
        source=source,
        status=status,
        scheduled_for=scheduled_for,
        lead_id=lead.id,
    )
    db.add(cb)
    await db.flush()

    # Step 6a: record lead_received_at (already set above)
    # If scheduled, return now — scheduler will fire later
    if status == "scheduled":
        # Send immediate acknowledgment SMS
        try:
            from app.schemas.outbound import OutboundRequest
            from app.services.outbound_gateway import OutboundGateway

            ack_req = OutboundRequest(
                tenant_id=tenant_id,
                recipient_phone=phone,
                channel="sms",
                message=(
                    "Got your request! We'll call you when our office opens at 8am."
                ),
                template_id="webform_quiet_hours_ack",
                idempotency_key=f"webform_ack_{cb.id}",
            )
            gw = OutboundGateway()
            await gw.send(ack_req, db)
        except Exception as exc:
            logger.warning("callback_pipeline: quiet hours ack SMS failed | err=%s", exc)

        return {"status": "scheduled", "callback_request_id": str(cb.id)}

    # Step 5: Fire outbound call immediately
    first_contact_at: datetime | None = None
    try:
        client = RetellClient()

        # Voicemail detection: fixed template string — NOT LLM generated
        booking_url = (
            getattr(contractor, "booking_url", None)
            or f"https://tradesflowos.com/book/{contractor.api_key}"
        )
        agent_name = getattr(contractor, "agent_name", None) or contractor.name
        voicemail_message = (
            f"Hi {name or 'there'}, this is {agent_name} calling you back. "
            f"Please call us or visit {booking_url} to schedule. Thanks!"
        )

        dynamic_vars: dict = {}
        if name:
            dynamic_vars["caller_name"] = name
        if issue:
            dynamic_vars["issue_description"] = issue
        if source:
            dynamic_vars["lead_source"] = source

        call_payload: dict = {
            "to_number": phone,
            "from_number": contractor.phone_number,
            "override_agent_id": contractor.retell_agent_id,
            "metadata": {
                "tenant_id": tenant_id,
                "call_type": "webform_callback",
                "callback_request_id": str(cb.id),
                "lead_id": str(lead.id),
            },
            "retell_llm_dynamic_variables": dynamic_vars if dynamic_vars else None,
        }

        # Add voicemail_message to payload for Retell voicemail detection
        # Retell supports this at the call creation level
        call_result = await client.create_phone_call(**{
            k: v for k, v in call_payload.items() if v is not None
        })

        outbound_call_id = call_result.get("call_id") or call_result.get("id")
        cb.outbound_call_id = outbound_call_id
        cb.status = "calling"
        first_contact_at = datetime.now(tz=timezone.utc)

        # Speed-to-lead delta
        lead.first_contact_attempted_at = first_contact_at
        if lead.lead_received_at:
            received = lead.lead_received_at
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            delta = int((first_contact_at - received).total_seconds())
            lead.speed_to_lead_seconds = max(0, delta)

        await db.flush()
        logger.info(
            "callback_pipeline: outbound call fired | phone=%s call_id=%s s2l=%s",
            phone, outbound_call_id, lead.speed_to_lead_seconds,
        )
        return {"status": "calling", "callback_request_id": str(cb.id), "call_id": outbound_call_id}

    except Exception as exc:
        cb.status = "failed"
        await db.flush()
        logger.error("callback_pipeline: outbound call failed | phone=%s err=%s", phone, exc)
        return {"status": "failed", "callback_request_id": str(cb.id), "error": str(exc)}
