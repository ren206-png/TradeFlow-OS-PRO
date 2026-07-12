from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead


async def transfer_call(tool_input: dict, context: dict) -> dict:
    """
    Queue a call transfer.

    Retell transfers are initiated by including `transfer_number` in the next
    WebSocket response payload — not via a REST API call. This tool:
      1. Marks the lead as transferred in the DB
      2. Queues the transfer number for the WebSocket handler via queue_transfer()

    The transfer destination is read from contractor.calendar_config["transfer_number"].
    """
    from app.routers.retell import queue_transfer

    db: AsyncSession = context["db"]
    call_session = context["call_session"]
    contractor = context["contractor"]

    from app.config import settings

    reason: str = tool_input["reason"]
    urgency: str = tool_input["urgency"]
    notes: str = tool_input.get("notes", "")

    if settings.live_transfer:
        from app.services.on_call import OnCallService
        transfer_number = await OnCallService().get_transfer_number(contractor, db) or ""
    else:
        transfer_number: str = contractor.calendar_config.get("transfer_number", "")

    # Update lead record
    if call_session.lead_id:
        result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
        lead = result.scalar_one_or_none()
        if lead:
            lead.human_transfer_required = True
            lead.transfer_reason = reason
            lead.appointment_status = "transferred"
            if notes:
                lead.notes = (lead.notes or "") + f"\n[Transfer notes] {notes}"

    # Queue the transfer — picked up by the WebSocket handler on next send
    if transfer_number:
        queue_transfer(call_session.retell_call_id, transfer_number)
    else:
        import logging
        logging.getLogger(__name__).warning(
            "transfer_call fired but no transfer_number configured | contractor=%s",
            contractor.name,
        )

    return {
        "success": True,
        "transfer_queued": bool(transfer_number),
        "transfer_destination": transfer_number or "not_configured",
        "reason": reason,
        "urgency": urgency,
    }
