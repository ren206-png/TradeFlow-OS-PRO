from __future__ import annotations
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead


async def book_appointment(tool_input: dict, context: dict) -> dict:
    """Book an appointment: write to DB lead record and trigger SMS confirmation."""
    db: AsyncSession = context["db"]
    call_session = context["call_session"]
    contractor = context["contractor"]

    slot_id = tool_input["slot_id"]
    caller_name = tool_input["caller_name"]
    phone = tool_input["phone"]
    service_address = tool_input["service_address"]
    trade = tool_input["trade"]
    problem_summary = tool_input.get("problem_summary", "")
    property_type = tool_input.get("property_type", "residential")
    appointment_time_str = tool_input.get("appointment_time")

    appointment_time = None
    if appointment_time_str:
        try:
            appointment_time = datetime.fromisoformat(appointment_time_str)
        except ValueError:
            appointment_time = datetime.now(tz=timezone.utc)

    calendar_event_id = f"manual-{slot_id}"

    # Upsert the Lead record for this call
    lead: Lead | None = None
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

    # Trigger SMS confirmation
    if contractor.sms_enabled and phone:
        from app.tools.send_sms import send_sms
        await send_sms(
            {
                "to_number": phone,
                "message_type": "confirmation",
                "appointment_time": appointment_time.isoformat() if appointment_time else "",
                "service_address": service_address,
            },
            context,
        )

    return {
        "success": True,
        "calendar_event_id": calendar_event_id,
        "appointment_time": appointment_time.isoformat() if appointment_time else "",
        "lead_id": str(lead.id),
    }
