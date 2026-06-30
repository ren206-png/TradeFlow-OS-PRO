from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.services.billing import BillingService
from app.services.sms import SMSService


async def send_sms(tool_input: dict, context: dict) -> dict:
    """
    Send an SMS via Twilio using the message_type to select the right template.

    Supported message_type values:
        "booking_confirmation"  — confirms a booked appointment
        "appointment_reminder"  — day-before reminder
        "missed_call"           — recovery SMS for missed inbound call
        "review_request"        — post-job review ask
        "followup"              — re-engage an unbooked lead
    """
    contractor = context["contractor"]
    db: AsyncSession = context["db"]
    call_session = context["call_session"]

    to_number: str = tool_input["to_number"]
    message_type: str = tool_input["message_type"]
    name: str = tool_input.get("name", "there")

    # Check SMS usage limit before sending
    usage = await BillingService().check_usage_limit(contractor, "sms")
    if not usage["allowed"]:
        return {"success": False, "error": "Monthly SMS limit reached."}

    sms = SMSService(contractor)

    if message_type == "booking_confirmation":
        result = sms.send_booking_confirmation(
            phone=to_number,
            name=name,
            trade=tool_input.get("trade", "service"),
            date_str=tool_input.get("date_str", ""),
            time_str=tool_input.get("time_str", ""),
            address=tool_input.get("address", ""),
        )
    elif message_type == "appointment_reminder":
        result = sms.send_appointment_reminder(
            phone=to_number,
            name=name,
            date_str=tool_input.get("date_str", ""),
            time_str=tool_input.get("time_str", ""),
        )
    elif message_type == "missed_call":
        result = sms.send_missed_call_recovery(phone=to_number)
    elif message_type == "review_request":
        result = sms.send_review_request(
            phone=to_number,
            name=name,
            review_link=tool_input.get("review_link", contractor.review_link or ""),
        )
    elif message_type == "followup":
        result = sms.send_followup(phone=to_number, name=name)
    else:
        return {"success": False, "error": f"Unknown message_type: {message_type!r}"}

    # Increment SMS usage counter when the send succeeded
    if result.get("success"):
        await BillingService().increment_usage(contractor, "sms", db)

    # Mark lead record when a booking confirmation is sent
    if call_session.lead_id and message_type == "booking_confirmation" and result.get("success"):
        lead_result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead.sms_confirmation_sent = True

    return result
