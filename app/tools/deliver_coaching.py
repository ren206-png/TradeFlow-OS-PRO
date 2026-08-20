"""
deliver_coaching tool — delivers a FIXED allowlist coaching script to the caller.

SAFETY RULES (non-negotiable):
  1. The LLM selects a script_id from a Pydantic Literal — it CANNOT invent freeform text.
  2. Validation failure → log + return error dict, never crash.
  3. Every delivery is written append-only to safety_action_ledger.
  4. On urgent/emergency_911 urgency, an SMS is sent to the on-call tech via OutboundGateway.
  5. No direct Anthropic/Claude API calls here. Ever.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coaching_script import CoachingScript
from app.models.on_call_rotation import OnCallRotation
from app.models.safety_action_ledger import SafetyActionLedger
from app.services.triage_library import TriageLibraryService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowlist of valid coaching script IDs.
# The LLM CANNOT invent a new ID — Pydantic validation will reject it.
# To add a new script: (1) add to this Literal, (2) add row to coaching_scripts table.
# ---------------------------------------------------------------------------
COACHING_SCRIPT_IDS = Literal[
    "water_main_shutoff_en",
    "water_main_shutoff_fr",
    "breaker_safety_en",
    "breaker_safety_fr",
    "thermostat_off_en",
    "thermostat_off_fr",
]


class DeliverCoachingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: COACHING_SCRIPT_IDS  # Pydantic Literal — LLM CANNOT invent a new ID
    language: Literal["en", "fr"] = "en"
    # Optional context injected by the agent loop — not from the LLM
    node_key: str = "unknown"


def get_deliver_coaching_tool_schema() -> dict:
    """Returns the Claude tool schema for deliver_coaching."""
    return {
        "name": "deliver_coaching",
        "description": (
            "Deliver a pre-approved safety coaching script to the caller. "
            "You MUST select a script_id from the exact allowlist below — do NOT invent coaching text. "
            "Use this when the current triage node has a coaching_script_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "script_id": {
                    "type": "string",
                    "enum": [
                        "water_main_shutoff_en",
                        "water_main_shutoff_fr",
                        "breaker_safety_en",
                        "breaker_safety_fr",
                        "thermostat_off_en",
                        "thermostat_off_fr",
                    ],
                    "description": "The ID of the coaching script to deliver. Must be from the exact allowlist.",
                },
                "language": {
                    "type": "string",
                    "enum": ["en", "fr"],
                    "description": "Language to deliver the script in. Defaults to 'en'.",
                    "default": "en",
                },
                "node_key": {
                    "type": "string",
                    "description": "The triage node key that triggered this script (for audit trail).",
                    "default": "unknown",
                },
            },
            "required": ["script_id"],
        },
    }


async def deliver_coaching(tool_input: dict, context: dict) -> dict:
    """
    Handler for the deliver_coaching tool.

    Steps:
    1. Validate input via DeliverCoachingInput (Pydantic Literal enforcement).
    2. Fetch CoachingScript from DB.
    3. Select French or English text.
    4. Write append-only row to safety_action_ledger.
    5. If urgency is urgent/emergency_911, send SMS to on-call tech via OutboundGateway.
    6. Return script text for the AI to read aloud.

    Never calls Claude/Anthropic API. Never generates coaching text freeform.
    """
    db: Optional[AsyncSession] = context.get("db")
    contractor = context.get("contractor")
    call_session = context.get("call_session")

    # ------------------------------------------------------------------
    # Step 1: Validate input — Pydantic Literal enforcement
    # ------------------------------------------------------------------
    try:
        validated = DeliverCoachingInput(**tool_input)
    except ValidationError as exc:
        logger.warning(
            "deliver_coaching: invalid input rejected | input=%s error=%s",
            tool_input, exc.errors(),
        )
        return {
            "success": False,
            "error": "invalid_script_id",
            "detail": str(exc.errors()),
        }

    script_id = validated.script_id
    language = validated.language
    node_key = validated.node_key

    if db is None or contractor is None:
        logger.error("deliver_coaching: missing db or contractor in context")
        return {"success": False, "error": "context_missing"}

    # ------------------------------------------------------------------
    # Step 2: Fetch coaching script from DB
    # ------------------------------------------------------------------
    try:
        svc = TriageLibraryService()
        script = await svc.get_coaching_script(script_id, language, db)
    except Exception as exc:
        logger.exception("deliver_coaching: DB fetch failed | script_id=%s", script_id)
        return {"success": False, "error": "db_fetch_failed", "detail": str(exc)}

    if script is None or not script.is_active:
        logger.warning("deliver_coaching: script not found or inactive | id=%s", script_id)
        return {"success": False, "error": "script_not_found", "script_id": script_id}

    # ------------------------------------------------------------------
    # Step 3: Select language variant
    # ------------------------------------------------------------------
    if language == "fr" and script.script_text_fr:
        delivered_text = script.script_text_fr
    else:
        delivered_text = script.script_text

    # ------------------------------------------------------------------
    # Step 4: Determine urgency from the active triage node (if available)
    # For audit trail we record the urgency. Default "standard" if node not found.
    # ------------------------------------------------------------------
    urgency_level = "standard"
    try:
        # Attempt to fetch node urgency_level from DB for accurate audit
        from app.models.triage_node import TriageNode
        from sqlalchemy import select as sa_select
        node_result = await db.execute(
            sa_select(TriageNode).where(TriageNode.node_key == node_key)
        )
        node = node_result.scalar_one_or_none()
        if node:
            urgency_level = node.urgency_level
    except Exception:
        pass  # use default

    # ------------------------------------------------------------------
    # Step 5: Write append-only row to safety_action_ledger
    # ------------------------------------------------------------------
    call_id = getattr(call_session, "retell_call_id", "unknown") if call_session else "unknown"
    try:
        ledger_row = SafetyActionLedger(
            id=uuid.uuid4(),
            call_id=call_id,
            tenant_id=contractor.id,
            node_key=node_key,
            script_id=script_id,
            script_text_delivered=delivered_text,
            language=language,
            urgency_level=urgency_level,
            delivered_at=datetime.now(tz=timezone.utc),
        )
        db.add(ledger_row)
        await db.flush()
        logger.info(
            "deliver_coaching: ledger row written | call_id=%s script=%s urgency=%s",
            call_id, script_id, urgency_level,
        )
    except Exception as exc:
        logger.exception("deliver_coaching: safety_action_ledger write failed | script=%s", script_id)
        # Do NOT fail the whole tool — coaching was selected; log and continue
        return {"success": False, "error": "ledger_write_failed", "detail": str(exc)}

    # ------------------------------------------------------------------
    # Step 6: Send on-call SMS for urgent/emergency_911 urgency
    # ------------------------------------------------------------------
    if urgency_level in ("urgent", "emergency_911"):
        await _send_oncall_alert(
            contractor=contractor,
            call_id=call_id,
            script_id=script_id,
            urgency_level=urgency_level,
            call_session=call_session,
            db=db,
        )

    return {
        "success": True,
        "script_id": script_id,
        "language": language,
        "coaching_text": delivered_text,
        "urgency_level": urgency_level,
    }


async def _send_oncall_alert(
    contractor,
    call_id: str,
    script_id: str,
    urgency_level: str,
    call_session,
    db: AsyncSession,
) -> None:
    """
    Send an urgent SMS to the on-call tech via OutboundGateway.
    Only fires once per call+script combination (idempotency_key deduplication).
    """
    try:
        from app.schemas.outbound import OutboundRequest
        from app.services.outbound_gateway import OutboundGateway

        # Look up on-call tech from on_call_rotation table
        now = datetime.now(tz=timezone.utc)
        day_of_week = now.weekday()  # 0=Mon, 6=Sun
        current_hour = now.hour

        oncall_result = await db.execute(
            select(OnCallRotation).where(
                OnCallRotation.tenant_id == contractor.id,
                OnCallRotation.day_of_week == day_of_week,
                OnCallRotation.start_hour <= current_hour,
                OnCallRotation.end_hour > current_hour,
                OnCallRotation.is_active == True,  # noqa: E712
            ).limit(1)
        )
        oncall = oncall_result.scalar_one_or_none()

        if oncall is None:
            # Fall back to OnCallSchedule if no rotation entry found
            from app.models.on_call_schedule import OnCallSchedule
            schedule_result = await db.execute(
                select(OnCallSchedule).where(
                    OnCallSchedule.contractor_id == contractor.id,
                    OnCallSchedule.day_of_week == day_of_week,
                    OnCallSchedule.is_active == True,  # noqa: E712
                ).limit(1)
            )
            schedule = schedule_result.scalar_one_or_none()
            if schedule is None:
                logger.info(
                    "deliver_coaching: no on-call tech found for tenant=%s day=%d hour=%d",
                    contractor.id, day_of_week, current_hour,
                )
                return
            oncall_phone = schedule.phone_number
        else:
            oncall_phone = oncall.phone_number

        # Build SMS message
        caller_phone = getattr(call_session, "caller_phone", "unknown") if call_session else "unknown"
        booking_url = getattr(contractor, "booking_url", None) or ""
        business_name = getattr(contractor, "name", "contractor")

        message = (
            f"URGENT CALL - {business_name}: {urgency_level} issue. "
            f"Caller: {caller_phone}. "
            f"Script: {script_id}. "
            f"Callback: {booking_url}"
        )

        idempotency_key = f"oncall_alert_{call_id}_{script_id}"

        request = OutboundRequest(
            tenant_id=str(contractor.id),
            recipient_phone=oncall_phone,
            channel="sms",
            message=message,
            idempotency_key=idempotency_key,
        )

        gateway = OutboundGateway()
        result = await gateway.send(request, db)
        logger.info(
            "deliver_coaching: on-call SMS sent | tenant=%s phone=%s urgency=%s result=%s",
            contractor.id, oncall_phone, urgency_level, result.success,
        )

    except Exception as exc:
        logger.exception(
            "deliver_coaching: on-call SMS failed (non-fatal) | call_id=%s err=%s",
            call_id, exc,
        )
