"""
Phase 6: collect_commercial_intake tool.
Gated behind commercial_intake feature flag.
Pydantic input model with extra="forbid".
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, ValidationError

from app.services.commercial_intake import CommercialIntakeInput, CommercialIntakeService

logger = logging.getLogger(__name__)


def get_collect_commercial_intake_tool_schema() -> dict:
    """Return the tool schema for the Claude tool list."""
    return {
        "name": "collect_commercial_intake",
        "description": (
            "Collect commercial/mechanical service intake information for commercial tenants. "
            "Call this after confirming the caller is from a commercial or industrial account. "
            "Gated behind commercial_intake feature flag."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "site_contact_name": {
                    "type": "string",
                    "description": "Name of the on-site contact person",
                },
                "site_contact_phone": {
                    "type": "string",
                    "description": "Phone number of the on-site contact",
                },
                "account_contact_name": {
                    "type": "string",
                    "description": "Name of the billing or account owner contact",
                },
                "po_number": {
                    "type": "string",
                    "description": "Purchase order number (optional)",
                },
                "building_id": {
                    "type": "string",
                    "description": "Building identifier",
                },
                "unit_id": {
                    "type": "string",
                    "description": "Unit or suite identifier",
                },
                "equipment_tag_id": {
                    "type": "string",
                    "description": "Equipment tag or asset ID",
                },
                "caller_company": {
                    "type": "string",
                    "description": "Company name of the caller for service agreement matching",
                },
                "service_address": {
                    "type": "string",
                    "description": "Service address",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["emergency", "same_day", "next_day", "scheduled", "standard"],
                    "description": "Urgency level of the service request",
                },
            },
            "required": [],  # All fields optional at collection time
        },
    }


async def collect_commercial_intake(tool_input: dict, context: dict) -> dict:
    """
    Handler for collect_commercial_intake tool.
    Validates input with Pydantic (extra=forbid), saves to DB, returns acknowledgment.
    """
    # Pydantic validation — extra=forbid
    try:
        validated = CommercialIntakeInput(**tool_input)
    except ValidationError as exc:
        logger.warning("collect_commercial_intake: validation error | errors=%s", exc.errors())
        return {
            "success": False,
            "error": "invalid_input",
            "detail": exc.errors()[:3],  # truncate for safety
        }

    contractor = context.get("contractor")
    db = context.get("db")
    call_session = context.get("call_session")

    if contractor is None or db is None:
        logger.warning("collect_commercial_intake: missing contractor or db in context")
        return {"success": False, "error": "context_missing"}

    svc = CommercialIntakeService()

    # Check service agreement match
    match_result = None
    acknowledgment: Optional[str] = None

    if validated.caller_company:
        try:
            is_match, match_result = await svc.check_service_agreement(
                validated.caller_company,
                contractor.id,
                db,
            )
            if is_match and match_result:
                acknowledgment = svc.get_confirmatory_acknowledgment(match_result)
        except Exception as exc:
            logger.warning(
                "collect_commercial_intake: service agreement check failed (non-fatal) | err=%s", exc
            )

    # Save to DB if we have a lead_id
    lead_id = getattr(call_session, "lead_id", None) if call_session else None
    if lead_id:
        await svc.save_intake(
            lead_id=lead_id,
            tenant_id=contractor.id,
            intake_data=validated.model_dump(),
            match_result=match_result,
            db=db,
        )

    result: dict = {"success": True, "fields_collected": validated.model_dump(exclude_none=True)}

    if acknowledgment:
        result["acknowledgment"] = acknowledgment
        result["agreement_matched"] = True
        result["priority_routing"] = match_result.priority_routing if match_result else False

    logger.info(
        "collect_commercial_intake: complete | tenant=%s lead=%s matched=%s",
        contractor.id, lead_id, bool(match_result),
    )
    return result
