from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.services.notifications import notify_appointment_booked


async def book_appointment(tool_input: dict, context: dict) -> dict:
    """Book an appointment: call CalendarService, write to DB lead record, trigger SMS confirmation."""
    from app.services.calendar import CalendarService

    db: AsyncSession = context["db"]
    call_session = context["call_session"]
    contractor = context["contractor"]

    slot_id: str = tool_input["slot_id"]
    caller_name: str = tool_input["caller_name"]
    phone: str = tool_input["phone"]
    service_address: str = tool_input["service_address"]
    trade: str = tool_input["trade"]
    problem_summary: str = tool_input.get("problem_summary", "")
    property_type: str = tool_input.get("property_type", "residential")
    appointment_time_str: Optional[str] = tool_input.get("appointment_time")

    appointment_time: Optional[datetime] = None
    if appointment_time_str:
        try:
            appointment_time = datetime.fromisoformat(appointment_time_str)
        except ValueError:
            appointment_time = datetime.now(tz=timezone.utc)

    # Call CalendarService to create the calendar event
    calendar_service = CalendarService(contractor)
    booking_result = await calendar_service.book_slot(
        slot_id=slot_id,
        customer_name=caller_name,
        phone=phone,
        address=service_address,
        trade=trade,
        notes=problem_summary,
        iso_start=appointment_time_str or "",
    )

    if not booking_result.get("success"):
        return {
            "success": False,
            "error": booking_result.get("error", "Calendar booking failed"),
        }

    calendar_event_id: str = booking_result.get("event_id", f"manual-{slot_id}")
    confirmation_number: str = booking_result.get("confirmation_number", "")

    # Upsert the Lead record for this call
    lead: Optional[Lead] = None
    if call_session.lead_id:
        result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
        lead = result.scalar_one_or_none()

    if lead is None:
        lead = Lead(
            contractor_id=contractor.id,
            call_id=call_session.retell_call_id,
            call_direction="inbound",
            lead_source="retell_call",
        )
        db.add(lead)

    lead.caller_name = caller_name
    lead.phone = phone
    lead.service_address = service_address
    lead.trade = trade
    lead.problem_summary = problem_summary
    lead.property_type = property_type
    lead.appointment_status = "booked"
    lead.appointment_time = appointment_time
    lead.calendar_event_id = calendar_event_id

    await db.flush()

    # Link lead back to call session
    if call_session.lead_id is None:
        call_session.lead_id = lead.id

    # Phase 1 — Consent capture (outbound_gateway flag guard)
    # Record implied_transaction consent for the inbound caller who just booked.
    # Only runs when the outbound_gateway flag is ON for this tenant.
    # Flag OFF → no change to existing behaviour.
    try:
        from app.services.feature_flags import is_enabled as _flag_enabled
        if await _flag_enabled(str(contractor.id), "outbound_gateway", db):
            from app.models.consent_ledger import ConsentLedger
            consent_row = ConsentLedger(
                tenant_id=str(contractor.id),
                recipient_phone=phone,
                channel="sms",
                consent_type="implied_transaction",
                consent_basis="caller booked appointment via AI receptionist",
                evidence_call_id=call_session.retell_call_id,
                expires_at=datetime.utcnow() + timedelta(days=730),  # CASL 2-year window
            )
            db.add(consent_row)
            await db.flush()
    except Exception as _consent_err:
        # Non-fatal — never block a booking due to consent recording failure
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "book_appointment: consent recording failed (non-fatal) | err=%s", _consent_err
        )

    # Notify contractor: appointment booked
    asyncio.ensure_future(notify_appointment_booked(contractor, lead))

    # Trigger SMS confirmation to customer
    if contractor.sms_enabled and phone:
        from app.tools.send_sms import send_sms
        await send_sms(
            {
                "to_number": phone,
                "message_type": "booking_confirmation",
                "name": caller_name,
                "trade": trade,
                "date_str": appointment_time.strftime("%B %-d, %Y") if appointment_time else "",
                "time_str": appointment_time.strftime("%-I:%M %p") if appointment_time else "",
                "address": service_address,
            },
            context,
        )

    return {
        "success": True,
        "calendar_event_id": calendar_event_id,
        "confirmation_number": confirmation_number,
        "appointment_time": appointment_time.isoformat() if appointment_time else "",
        "lead_id": str(lead.id),
    }
