import logging

from app.config import settings
from app.tools.book_appointment import book_appointment
from app.tools.check_availability import check_availability
from app.tools.create_lead import create_lead_record
from app.tools.deliver_coaching import deliver_coaching
from app.tools.send_sms import send_sms
from app.tools.transfer_call import transfer_call
from app.tools.validate_address import validate_service_area

logger = logging.getLogger(__name__)

_TOOL_MAP_BASE = {
    "check_availability": check_availability,
    "book_appointment": book_appointment,
    "validate_service_area": validate_service_area,
    "send_sms": send_sms,
    "create_lead_record": create_lead_record,
    "transfer_call": transfer_call,
}

# deliver_coaching is gated behind settings.safety_coaching feature flag.
# When flag is ON, it is included in _TOOL_MAP at import time.
# The tool schema is added to TRADEFLOW_TOOLS list in claude_agent.py dynamically.
_TOOL_MAP = {
    **_TOOL_MAP_BASE,
    **({"deliver_coaching": deliver_coaching} if settings.safety_coaching else {}),
    # Phase 6: commercial intake — gated behind commercial_intake flag
    **(
        {"collect_commercial_intake": __import__(
            "app.tools.commercial_intake_tool",
            fromlist=["collect_commercial_intake"],
        ).collect_commercial_intake}
        if settings.commercial_intake
        else {}
    ),
}


async def execute_tool(tool_name: str, tool_input: dict, context: dict) -> dict:
    """Route a tool call to its handler. Always returns a dict; never raises."""
    if tool_name == "classify_urgency":
        try:
            urgency = tool_input.get("urgency_level", "standard")
            reason = tool_input.get("reason", "")
            lead_id = context.get("lead_id")
            if lead_id:
                db = context.get("db")
                if db is not None:
                    from app.models.lead import Lead
                    lead = await db.get(Lead, lead_id)
                    if lead:
                        lead.emergency_level = urgency
                        lead.notes = (lead.notes or "") + f"\n[Urgency classified: {urgency} — {reason}]"
                        await db.flush()
            return {"classified": urgency, "reason": reason}
        except Exception as exc:
            logger.exception("Tool classify_urgency failed with input %s", tool_input)
            return {"error": str(exc), "detail": type(exc).__name__, "success": False}

    handler = _TOOL_MAP.get(tool_name)
    if handler is None:
        logger.warning("Unknown tool requested: %s", tool_name)
        return {"error": f"Unknown tool: {tool_name}", "success": False}

    try:
        return await handler(tool_input, context)
    except Exception as exc:
        logger.exception("Tool %s failed with input %s", tool_name, tool_input)
        return {"error": str(exc), "detail": type(exc).__name__, "success": False}
