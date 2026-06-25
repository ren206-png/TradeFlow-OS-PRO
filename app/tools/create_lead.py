from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.services.lead_scoring import calculate_scores


async def create_lead_record(tool_input: dict, context: dict) -> dict:
    """Upsert the CRM Lead record. Calculates priority scores before saving."""
    db: AsyncSession = context["db"]
    call_session = context["call_session"]
    contractor = context["contractor"]

    # Resolve existing lead or create new
    lead: Lead | None = None
    if call_session.lead_id:
        result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
        lead = result.scalar_one_or_none()

    if lead is None:
        lead = Lead(
            contractor_id=contractor.id,
            call_id=call_session.retell_call_id,
            lead_source="retell_call",
        )
        db.add(lead)

    # Apply all provided fields
    str_fields = [
        "caller_name", "phone", "email", "service_address", "city",
        "province_state", "postal_zip", "property_type", "business_name",
        "trade", "service_category", "problem_summary", "emergency_level",
        "service_area_status", "appointment_status", "calendar_event_id",
        "transfer_reason", "priority_level", "customer_sentiment", "notes",
        "call_direction", "lead_source",
    ]
    for field in str_fields:
        if field in tool_input:
            setattr(lead, field, tool_input[field])

    bool_fields = ["life_safety_risk", "human_transfer_required", "sms_confirmation_sent"]
    for field in bool_fields:
        if field in tool_input:
            setattr(lead, field, bool(tool_input[field]))

    int_fields = ["emergency_score", "revenue_score", "close_probability"]
    for field in int_fields:
        if field in tool_input:
            setattr(lead, field, int(tool_input[field]))

    if "appointment_time" in tool_input and tool_input["appointment_time"]:
        try:
            lead.appointment_time = datetime.fromisoformat(tool_input["appointment_time"])
        except ValueError:
            pass

    # Auto-calculate scores if raw scores provided but priority not set
    if not lead.priority_level and (lead.emergency_score or lead.revenue_score):
        scores = calculate_scores({
            "emergency_score": lead.emergency_score,
            "revenue_score": lead.revenue_score,
            "close_probability": lead.close_probability,
            "life_safety_risk": lead.life_safety_risk,
            "service_area_status": lead.service_area_status,
        })
        lead.priority_level = scores["priority_level"]
        if not lead.emergency_score:
            lead.emergency_score = scores["emergency_score"]
        if not lead.revenue_score:
            lead.revenue_score = scores["revenue_score"]
        if not lead.close_probability:
            lead.close_probability = scores["close_probability"]

    await db.flush()

    # Link back to call session
    if call_session.lead_id is None:
        call_session.lead_id = lead.id

    return {"success": True, "lead_id": str(lead.id)}
