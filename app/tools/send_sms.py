from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.services.sms import SMSService


async def send_sms(tool_input: dict, context: dict) -> dict:
    """Send an SMS via Twilio and mark the lead record accordingly."""
    contractor = context["contractor"]
    db: AsyncSession = context["db"]
    call_session = context["call_session"]

    to_number: str = tool_input["to_number"]
    message_type: str = tool_input["message_type"]
    appointment_time: str = tool_input.get("appointment_time", "")
    service_address: str = tool_input.get("service_address", "")

    sms_service = SMSService(contractor)
    result = await sms_service.send(
        to_number=to_number,
        message_type=message_type,
        appointment_time=appointment_time,
        service_address=service_address,
    )

    # Update lead record to reflect SMS was sent
    if call_session.lead_id and message_type == "confirmation":
        lead_result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead:
            lead.sms_confirmation_sent = True

    return result
