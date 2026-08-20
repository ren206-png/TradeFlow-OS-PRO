"""
Phase 4: Estimate Follow-Up Drip Service.

Adversarial check #2 built-in: before every drip step, re-check estimate status
from DB AND from FSM (if fsm_estimate_id set).

Stop conditions:
  - estimate.status in (accepted, declined)
  - STOP keyword / opt-out (caught by OutboundGateway consent/opt-out chain)
  - estimate.followup_paused = True
  - contractor.outbound_paused = True (kill switch, caught by OutboundGateway)
  - All drip steps completed (step >= 3)

Revenue attribution: integer cents ONLY. is_estimated=True when using avg_ticket fallback.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.contractor import Contractor
from app.models.estimate import Estimate
from app.models.revenue_attribution_ledger import RevenueAttributionLedger
from app.schemas.outbound import OutboundRequest
from app.services.feature_flags import is_enabled
from app.services.outbound_gateway import OutboundGateway

logger = logging.getLogger(__name__)

# Drip step → days after enrollment
_STEP_DAYS = {0: 2, 1: 5, 2: 10}

_TERMINAL_STATUSES = {"accepted", "declined", "expired"}


class EstimateFollowupService:

    async def enroll(
        self,
        estimate: Estimate,
        contractor: Contractor,
        db: AsyncSession,
    ) -> None:
        """
        Enroll estimate in followup drip.
        Sets followup_enrolled_at and step=0.
        Gated behind 'estimate_followup' feature flag.
        """
        if not await is_enabled(str(estimate.tenant_id), "estimate_followup", db):
            logger.debug("estimate_followup flag OFF — skipping enrollment | estimate=%s", estimate.id)
            return

        if estimate.followup_enrolled_at is not None:
            logger.info("estimate already enrolled in drip | estimate=%s", estimate.id)
            return

        estimate.followup_enrolled_at = datetime.now(tz=timezone.utc)
        estimate.followup_step = 0
        estimate.followup_paused = False
        await db.flush()
        logger.info("estimate enrolled in followup drip | estimate=%s", estimate.id)

    async def run_step(
        self,
        estimate_id: uuid.UUID,
        contractor: Contractor,
        db: AsyncSession,
    ) -> None:
        """
        Execute current drip step.
        ADVERSARIAL CHECK #2: re-fetch estimate from DB before every step.
        If status changed to accepted/declined → stop.
        If fsm_estimate_id set → also re-check via FSM.
        """

        if not await is_enabled(str(contractor.id), "estimate_followup", db):
            logger.debug("estimate_followup flag OFF — skipping run_step | estimate=%s", estimate_id)
            return

        # Re-fetch from DB — adversarial check #2
        result = await db.execute(
            select(Estimate).where(
                Estimate.id == estimate_id,
                Estimate.tenant_id == contractor.id,
            )
        )
        estimate = result.scalar_one_or_none()
        if estimate is None:
            logger.warning("estimate not found | estimate=%s", estimate_id)
            return

        # Check local status first
        if estimate.status in _TERMINAL_STATUSES:
            logger.info(
                "estimate drip stopped — terminal status=%s | estimate=%s",
                estimate.status, estimate.id,
            )
            return

        if estimate.followup_paused:
            logger.info("estimate drip paused | estimate=%s", estimate.id)
            return

        if estimate.followup_enrolled_at is None:
            logger.warning("estimate not enrolled in drip | estimate=%s", estimate.id)
            return

        step = estimate.followup_step
        if step >= 3:
            logger.info("estimate drip complete (all steps done) | estimate=%s", estimate.id)
            return

        # FSM re-verify if available (adversarial check #2 — external source truth)
        if estimate.fsm_estimate_id:
            try:
                from app.services.fsm.service import FSMService
                fsm_status = await FSMService().get_estimate_status(
                    contractor, estimate.fsm_estimate_id, db
                )
                if fsm_status in _TERMINAL_STATUSES:
                    logger.info(
                        "estimate drip stopped — FSM status=%s | estimate=%s",
                        fsm_status, estimate.id,
                    )
                    estimate.status = fsm_status
                    await db.flush()
                    return
                elif fsm_status is None:
                    # Adapter returned None (not implemented) → note source as manual
                    if estimate.source != "manual":
                        estimate.source = "manual"
                        await db.flush()
                        logger.info(
                            "estimate FSM read not implemented — source set to manual | estimate=%s",
                            estimate.id,
                        )
            except Exception as exc:
                logger.warning(
                    "estimate FSM status check failed, continuing drip | estimate=%s err=%s",
                    estimate.id, exc,
                )

        # Execute the step
        gateway = OutboundGateway()
        job_type = estimate.caller_name or "service"  # fallback

        if step == 0:
            # Day 2: SMS question
            message = (
                f"Hi {estimate.caller_name or 'there'}, still thinking about that estimate? "
                f"We can answer any questions — just reply or call {contractor.phone_number}."
            )
            await self._send_sms(
                estimate=estimate,
                contractor=contractor,
                message=message,
                idempotency_key=f"drip_{estimate.id}_step0",
                db=db,
                gateway=gateway,
            )

        elif step == 1:
            # Day 5: outbound AI call (short triage/booking flow)
            try:
                request = OutboundRequest(
                    tenant_id=str(estimate.tenant_id),
                    recipient_phone=estimate.caller_phone,
                    channel="call",
                    idempotency_key=f"drip_{estimate.id}_step1",
                    template_id="estimate_followup_call",
                    call_script_id="estimate_followup_flow",
                )
                result_gw = await gateway.send(request, db)
                if result_gw.success:
                    logger.info("estimate drip step 1 call sent | estimate=%s", estimate.id)
                else:
                    logger.warning(
                        "estimate drip step 1 call blocked | estimate=%s reason=%s",
                        estimate.id, result_gw.block_reason,
                    )
            except ValidationError as exc:
                logger.error("estimate drip step 1 validation failed | estimate=%s err=%s", estimate.id, exc)

        elif step == 2:
            # Day 10: last SMS reminder
            booking_url = contractor.booking_url or contractor.phone_number
            message = (
                f"Hi {estimate.caller_name or 'there'}, last reminder — your estimate expires soon. "
                f"Book now: {booking_url}"
            )
            await self._send_sms(
                estimate=estimate,
                contractor=contractor,
                message=message,
                idempotency_key=f"drip_{estimate.id}_step2",
                db=db,
                gateway=gateway,
            )

        # Advance step
        estimate.followup_step = step + 1
        await db.flush()
        logger.info("estimate drip step %d executed | estimate=%s", step, estimate.id)

    async def _send_sms(
        self,
        *,
        estimate: Estimate,
        contractor: Contractor,
        message: str,
        idempotency_key: str,
        db: AsyncSession,
        gateway: OutboundGateway,
    ) -> None:
        try:
            request = OutboundRequest(
                tenant_id=str(estimate.tenant_id),
                recipient_phone=estimate.caller_phone,
                channel="sms",
                message=message,
                idempotency_key=idempotency_key,
                template_id="estimate_followup_sms",
            )
            result = await gateway.send(request, db)
            if not result.success:
                logger.warning(
                    "estimate drip SMS blocked | estimate=%s reason=%s key=%s",
                    estimate.id, result.block_reason, idempotency_key,
                )
        except ValidationError as exc:
            logger.error(
                "estimate drip SMS validation failed | estimate=%s err=%s", estimate.id, exc
            )

    async def record_conversion(
        self,
        estimate: Estimate,
        appointment: Appointment,
        db: AsyncSession,
    ) -> RevenueAttributionLedger:
        """
        Write a revenue_attribution_ledger row on conversion.
        Integer cents ONLY. No floats.
        is_estimated=True when value comes from avg_ticket fallback (not verified FSM).
        is_estimated=False ONLY when estimate.estimate_value_cents is set AND was
        verified via FSM (i.e. fsm_estimate_id set and status confirmed externally).
        """
        # Fetch contractor for avg_ticket fallback
        contractor_result = await db.execute(
            select(Contractor).where(Contractor.id == estimate.tenant_id)
        )
        contractor = contractor_result.scalar_one_or_none()

        # Determine attributed value — integer cents only
        attributed_value_cents: int
        is_estimated: bool

        if estimate.estimate_value_cents is not None:
            # We have an explicit estimate value
            attributed_value_cents = int(estimate.estimate_value_cents)  # ensure int, never float
            # is_estimated=False only if FSM-verified (fsm_estimate_id + status accepted from FSM)
            is_estimated = estimate.fsm_estimate_id is None or estimate.source == "manual"
        elif contractor and contractor.avg_ticket_cents_by_trade and estimate.caller_name:
            # No explicit estimate — try trade-specific avg ticket
            # Note: caller_name is used as proxy for job_type when not set on estimate
            # Fall through to avg_ticket_cents
            job_type = getattr(appointment, "job_type", None)
            fallback = None
            if job_type and contractor.avg_ticket_cents_by_trade:
                fallback = contractor.avg_ticket_cents_by_trade.get(job_type)
            if fallback is None and contractor.avg_ticket_cents:
                fallback = contractor.avg_ticket_cents
            attributed_value_cents = int(fallback) if fallback else 0
            is_estimated = True  # always estimated when using avg_ticket fallback
        elif contractor and contractor.avg_ticket_cents:
            attributed_value_cents = int(contractor.avg_ticket_cents)
            is_estimated = True
        else:
            attributed_value_cents = 0
            is_estimated = True

        currency = estimate.currency or "CAD"

        ledger_row = RevenueAttributionLedger(
            id=uuid.uuid4(),
            tenant_id=estimate.tenant_id,
            event_type="estimate_converted",
            lead_id=estimate.lead_id,
            estimate_id=estimate.id,
            appointment_id=appointment.id,
            attributed_value_cents=attributed_value_cents,
            currency=currency,
            is_estimated=is_estimated,
            is_correction=False,
        )
        db.add(ledger_row)
        await db.flush()
        logger.info(
            "revenue_attribution: estimate_converted | estimate=%s appt=%s "
            "value_cents=%d currency=%s is_estimated=%s",
            estimate.id, appointment.id,
            attributed_value_cents, currency, is_estimated,
        )
        return ledger_row
