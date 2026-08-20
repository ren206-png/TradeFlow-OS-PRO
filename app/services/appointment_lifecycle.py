"""
Phase 4: Appointment Lifecycle Service.

All outbound SMS/calls go through OutboundGateway only.
Gated behind 'appointment_lifecycle' feature flag.
911 safety rule untouchable — this file does not touch triage.py.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.contractor import Contractor
from app.schemas.outbound import OutboundRequest
from app.services.feature_flags import is_enabled
from app.services.outbound_gateway import OutboundGateway

logger = logging.getLogger(__name__)


class AppointmentLifecycleService:

    async def send_confirmation(
        self,
        appointment: Appointment,
        contractor: Contractor,
        db: AsyncSession,
    ) -> None:
        """
        Immediate SMS confirmation on booking.
        ALL sends go through OutboundGateway — never directly.
        Idempotency key: confirm_{appointment.id}
        """
        if not await is_enabled(str(appointment.tenant_id), "appointment_lifecycle", db):
            logger.debug(
                "appointment_lifecycle flag OFF — skipping confirmation | appt=%s", appointment.id
            )
            return

        appt_dt: datetime = appointment.appointment_time
        if appt_dt.tzinfo is None:
            appt_dt = appt_dt.replace(tzinfo=timezone.utc)

        date_str = appt_dt.strftime("%B %d, %Y")
        time_str = appt_dt.strftime("%I:%M %p UTC")

        message = (
            f"Confirmed: {contractor.name} on {date_str} at {time_str}. "
            f"Reply CONFIRM to confirm or RESCHEDULE to pick a new time. "
            f"Your number: {contractor.phone_number}"
        )

        try:
            request = OutboundRequest(
                tenant_id=str(appointment.tenant_id),
                recipient_phone=appointment.caller_phone,
                channel="sms",
                message=message,
                idempotency_key=f"confirm_{appointment.id}",
                template_id="appointment_confirmation",
            )
        except ValidationError as exc:
            logger.error(
                "appointment_lifecycle: confirmation request validation failed | appt=%s err=%s",
                appointment.id, exc,
            )
            return

        gateway = OutboundGateway()
        result = await gateway.send(request, db)

        if result.success:
            appointment.confirmation_sent_at = datetime.now(tz=timezone.utc)
            await db.flush()
            logger.info(
                "appointment_lifecycle: confirmation sent | appt=%s ledger=%s",
                appointment.id, result.ledger_id,
            )
        else:
            logger.warning(
                "appointment_lifecycle: confirmation blocked | appt=%s reason=%s",
                appointment.id, result.block_reason,
            )

    async def send_reminder(
        self,
        appointment: Appointment,
        contractor: Contractor,
        db: AsyncSession,
    ) -> None:
        """
        Day-before reminder SMS.
        ADVERSARIAL CHECK #1: Re-verify appointment status via FSM before sending.
        If FSM says cancelled/completed → skip, log 'skipped_stale'.
        Must be called within 15 minutes of fire time (enforced by scheduler job).
        """
        if not await is_enabled(str(appointment.tenant_id), "appointment_lifecycle", db):
            logger.debug(
                "appointment_lifecycle flag OFF — skipping reminder | appt=%s", appointment.id
            )
            return

        # Re-fetch appointment from DB (status may have changed since job was queued)
        fresh_result = await db.execute(
            select(Appointment).where(Appointment.id == appointment.id)
        )
        fresh = fresh_result.scalar_one_or_none()
        if fresh is None:
            logger.info("appointment_lifecycle: appointment not found, skipping reminder | appt=%s", appointment.id)
            return
        appointment = fresh

        # Local status check first
        if appointment.status in ("cancelled", "completed"):
            logger.info(
                "appointment_lifecycle: skipped_stale (local status=%s) | appt=%s",
                appointment.status, appointment.id,
            )
            return

        # FSM re-verify if fsm_appointment_id is set (adversarial check #1)
        if appointment.fsm_appointment_id:
            try:
                from app.services.fsm.service import FSMService
                fsm_status = await FSMService().get_appointment_status(
                    contractor, appointment.fsm_appointment_id, db
                )
                if fsm_status in ("cancelled", "completed"):
                    logger.info(
                        "appointment_lifecycle: skipped_stale (FSM status=%s) | appt=%s",
                        fsm_status, appointment.id,
                    )
                    appointment.status = fsm_status
                    await db.flush()
                    return
            except Exception as exc:
                logger.warning(
                    "appointment_lifecycle: FSM status check failed, proceeding with reminder | appt=%s err=%s",
                    appointment.id, exc,
                )

        appt_dt: datetime = appointment.appointment_time
        if appt_dt.tzinfo is None:
            appt_dt = appt_dt.replace(tzinfo=timezone.utc)
        date_str = appt_dt.strftime("%B %d, %Y")
        time_str = appt_dt.strftime("%I:%M %p UTC")
        name = appointment.caller_name or "there"

        message = (
            f"Hi {name}, reminder: your appointment with {contractor.name} is tomorrow, "
            f"{date_str} at {time_str}. "
            f"Reply CONFIRM to confirm or RESCHEDULE to reschedule. "
            f"Questions? Call {contractor.phone_number}"
        )

        try:
            request = OutboundRequest(
                tenant_id=str(appointment.tenant_id),
                recipient_phone=appointment.caller_phone,
                channel="sms",
                message=message,
                idempotency_key=f"reminder_{appointment.id}",
                template_id="appointment_reminder",
            )
        except ValidationError as exc:
            logger.error(
                "appointment_lifecycle: reminder request validation failed | appt=%s err=%s",
                appointment.id, exc,
            )
            return

        gateway = OutboundGateway()
        result = await gateway.send(request, db)

        if result.success:
            appointment.reminder_sent_at = datetime.now(tz=timezone.utc)
            await db.flush()
            logger.info(
                "appointment_lifecycle: reminder sent | appt=%s ledger=%s",
                appointment.id, result.ledger_id,
            )
        else:
            logger.warning(
                "appointment_lifecycle: reminder blocked | appt=%s reason=%s",
                appointment.id, result.block_reason,
            )

    async def handle_confirm_keyword(
        self,
        phone: str,
        tenant_id: str,
        db: AsyncSession,
    ) -> None:
        """
        CONFIRM keyword from inbound SMS — find most recent scheduled appointment
        for this phone + tenant and mark it confirmed.
        """
        if not await is_enabled(tenant_id, "appointment_lifecycle", db):
            return

        result = await db.execute(
            select(Appointment)
            .where(
                Appointment.tenant_id == uuid.UUID(tenant_id),
                Appointment.caller_phone == phone,
                Appointment.status == "scheduled",
            )
            .order_by(Appointment.appointment_time.asc())
            .limit(1)
        )
        appointment = result.scalar_one_or_none()
        if appointment is None:
            logger.info(
                "appointment_lifecycle: CONFIRM keyword — no scheduled appointment found | phone=%s tenant=%s",
                phone, tenant_id,
            )
            return

        appointment.status = "confirmed"
        await db.flush()
        logger.info(
            "appointment_lifecycle: CONFIRM keyword processed | appt=%s phone=%s",
            appointment.id, phone,
        )

    async def handle_reschedule_keyword(
        self,
        phone: str,
        tenant_id: str,
        db: AsyncSession,
    ) -> None:
        """
        RESCHEDULE keyword from inbound SMS — mark reschedule offered and
        trigger outbound Retell call to offer new slots.
        """
        if not await is_enabled(tenant_id, "appointment_lifecycle", db):
            return

        result = await db.execute(
            select(Appointment)
            .where(
                Appointment.tenant_id == uuid.UUID(tenant_id),
                Appointment.caller_phone == phone,
                Appointment.status.in_(["scheduled", "confirmed"]),
            )
            .order_by(Appointment.appointment_time.asc())
            .limit(1)
        )
        appointment = result.scalar_one_or_none()
        if appointment is None:
            logger.info(
                "appointment_lifecycle: RESCHEDULE keyword — no appointment found | phone=%s tenant=%s",
                phone, tenant_id,
            )
            return

        appointment.reschedule_offered_at = datetime.now(tz=timezone.utc)
        await db.flush()

        # Trigger outbound call via gateway
        try:
            contractor_result = await db.execute(
                select(Contractor).where(Contractor.id == uuid.UUID(tenant_id))
            )
            contractor = contractor_result.scalar_one_or_none()
            if contractor:
                request = OutboundRequest(
                    tenant_id=tenant_id,
                    recipient_phone=phone,
                    channel="call",
                    idempotency_key=f"reschedule_call_{appointment.id}",
                    template_id="reschedule_booking",
                    call_script_id="reschedule_flow",
                )
                gateway = OutboundGateway()
                result_gw = await gateway.send(request, db)
                logger.info(
                    "appointment_lifecycle: RESCHEDULE outbound call | appt=%s success=%s",
                    appointment.id, result_gw.success,
                )
        except Exception as exc:
            logger.error(
                "appointment_lifecycle: RESCHEDULE outbound call failed | appt=%s err=%s",
                appointment.id, exc,
            )
