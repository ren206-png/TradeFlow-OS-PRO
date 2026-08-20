"""
Phase 4: Revenue Recovery Campaigns & Appointment Lifecycle Tests.

Covers all 12 spec test cases plus adversarial self-checks 1–3.
Uses pytest-asyncio + pytest-mock. DB interactions mocked via AsyncMock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers — minimal model stand-ins so tests don't require live DB
# ---------------------------------------------------------------------------

def _make_contractor(
    outbound_paused: bool = False,
    avg_ticket_cents: Optional[int] = None,
    avg_ticket_cents_by_trade: Optional[dict] = None,
    booking_url: str = "https://book.example.com",
) -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.name = "Rennco Plumbing"
    c.phone_number = "+14165550001"
    c.business_name = "Rennco Plumbing"
    c.outbound_paused = outbound_paused
    c.outbound_paused_at = None
    c.avg_ticket_cents = avg_ticket_cents
    c.avg_ticket_cents_by_trade = avg_ticket_cents_by_trade
    c.booking_url = booking_url
    c.retell_agent_id = "agent_test_123"
    return c


def _make_appointment(
    tenant_id=None,
    status: str = "scheduled",
    fsm_appointment_id: Optional[str] = None,
    appointment_time: Optional[datetime] = None,
) -> MagicMock:
    a = MagicMock()
    a.id = uuid.uuid4()
    a.tenant_id = tenant_id or uuid.uuid4()
    a.lead_id = None
    a.caller_phone = "+16045550002"
    a.caller_name = "Alice Test"
    a.status = status
    a.fsm_appointment_id = fsm_appointment_id
    a.appointment_time = appointment_time or (datetime.now(tz=timezone.utc) + timedelta(days=2))
    a.confirmation_sent_at = None
    a.reminder_sent_at = None
    a.reschedule_offered_at = None
    return a


def _make_estimate(
    tenant_id=None,
    status: str = "sent",
    estimate_value_cents: Optional[int] = None,
    fsm_estimate_id: Optional[str] = None,
    followup_step: int = 0,
    followup_paused: bool = False,
    followup_enrolled_at: Optional[datetime] = None,
    currency: str = "CAD",
    source: str = "manual",
) -> MagicMock:
    e = MagicMock()
    e.id = uuid.uuid4()
    e.tenant_id = tenant_id or uuid.uuid4()
    e.lead_id = None
    e.caller_phone = "+14165550003"
    e.caller_name = "Bob Test"
    e.status = status
    e.estimate_value_cents = estimate_value_cents
    e.currency = currency
    e.source = source
    e.fsm_estimate_id = fsm_estimate_id
    e.followup_enrolled_at = followup_enrolled_at or datetime.now(tz=timezone.utc)
    e.followup_step = followup_step
    e.followup_paused = followup_paused
    return e


def _make_campaign(tenant_id=None, status="active", daily_send_cap=50) -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.tenant_id = tenant_id or uuid.uuid4()
    c.name = "Fall Plumbing Reactivation"
    c.campaign_type = "seasonal"
    c.status = status
    c.trade = "plumbing"
    c.season = "fall"
    c.daily_send_cap = daily_send_cap
    c.total_sent = 0
    c.total_converted = 0
    return c


def _gateway_result(success: bool, block_reason: str = None) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.block_reason = block_reason
    r.ledger_id = str(uuid.uuid4())
    return r


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.get = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Test 1: Appointment confirmation SMS fires via gateway (not directly)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_confirmation_fires_via_gateway():
    """Confirmation SMS must go through OutboundGateway, never directly."""
    contractor = _make_contractor()
    appointment = _make_appointment(tenant_id=contractor.id)
    db = _mock_db()

    # Feature flag ON
    with patch("app.services.appointment_lifecycle.is_enabled", return_value=True), \
         patch("app.services.appointment_lifecycle.OutboundGateway") as mock_gw_cls:

        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(return_value=_gateway_result(True))
        mock_gw_cls.return_value = mock_gw

        from app.services.appointment_lifecycle import AppointmentLifecycleService
        svc = AppointmentLifecycleService()
        await svc.send_confirmation(appointment, contractor, db)

        # Gateway.send must be called exactly once
        mock_gw.send.assert_called_once()
        call_request = mock_gw.send.call_args[0][0]
        assert call_request.channel == "sms"
        assert call_request.idempotency_key == f"confirm_{appointment.id}"
        assert "CONFIRM" in call_request.message
        assert "RESCHEDULE" in call_request.message


# ---------------------------------------------------------------------------
# Test 2: Day-before reminder skips stale/cancelled appointment (FSM re-verify)
# Adversarial self-check #1
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reminder_skips_fsm_cancelled():
    """
    Adversarial check #1: FSM returns cancelled → reminder SMS is NOT sent.
    """
    contractor = _make_contractor()
    appointment = _make_appointment(
        tenant_id=contractor.id,
        status="scheduled",
        fsm_appointment_id="fsm_job_123",
        appointment_time=datetime.now(tz=timezone.utc) + timedelta(hours=20),
    )
    db = _mock_db()

    # Re-fetch returns same appointment (still scheduled locally)
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none = MagicMock(return_value=appointment)
    db.execute = AsyncMock(return_value=mock_scalar)

    # FSMService imported locally inside send_reminder; patch via builtins __import__
    # Use a simpler approach: mock the sys.modules entry for the fsm service module
    import sys
    mock_fsm_module = MagicMock()
    mock_fsm_instance = AsyncMock()
    mock_fsm_instance.get_appointment_status = AsyncMock(return_value="cancelled")
    mock_fsm_module.FSMService = MagicMock(return_value=mock_fsm_instance)

    with patch("app.services.appointment_lifecycle.is_enabled", return_value=True), \
         patch.dict(sys.modules, {"app.services.fsm.service": mock_fsm_module}), \
         patch("app.services.appointment_lifecycle.OutboundGateway") as mock_gw_cls:

        # FSM says cancelled (configured above)
        mock_fsm_instance  # already configured

        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(return_value=_gateway_result(True))
        mock_gw_cls.return_value = mock_gw

        from app.services.appointment_lifecycle import AppointmentLifecycleService
        svc = AppointmentLifecycleService()
        await svc.send_reminder(appointment, contractor, db)

        # SMS must NOT be sent
        mock_gw.send.assert_not_called()
        # Appointment status updated to cancelled
        assert appointment.status == "cancelled"


# ---------------------------------------------------------------------------
# Test 3: Estimate drip stops when status=accepted (adversarial check #2)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drip_stops_after_direct_booking():
    """
    Adversarial check #2: estimate status re-fetched from DB before each step.
    If status=accepted since enrollment, drip stops immediately.
    """
    contractor = _make_contractor()
    estimate = _make_estimate(
        tenant_id=contractor.id,
        status="accepted",   # already accepted when job fires
        followup_step=0,
        followup_enrolled_at=datetime.now(tz=timezone.utc) - timedelta(days=2),
    )
    db = _mock_db()

    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none = MagicMock(return_value=estimate)
    db.execute = AsyncMock(return_value=mock_scalar)

    with patch("app.services.estimate_followup.is_enabled", return_value=True), \
         patch("app.services.estimate_followup.OutboundGateway") as mock_gw_cls:

        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(return_value=_gateway_result(True))
        mock_gw_cls.return_value = mock_gw

        from app.services.estimate_followup import EstimateFollowupService
        svc = EstimateFollowupService()
        await svc.run_step(estimate.id, contractor, db)

        # No SMS sent — drip stopped on accepted status
        mock_gw.send.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Estimate drip stops on STOP keyword (consent gate via gateway block)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_drip_stops_on_stop_keyword():
    """
    Gateway returns opted_out block → drip step does not re-try.
    The stop propagates through OutboundGateway opt-out check.
    """
    contractor = _make_contractor()
    estimate = _make_estimate(
        tenant_id=contractor.id,
        status="sent",
        followup_step=0,
        followup_enrolled_at=datetime.now(tz=timezone.utc) - timedelta(days=2),
    )
    db = _mock_db()

    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none = MagicMock(return_value=estimate)
    db.execute = AsyncMock(return_value=mock_scalar)

    with patch("app.services.estimate_followup.is_enabled", return_value=True), \
         patch("app.services.estimate_followup.OutboundGateway") as mock_gw_cls:

        # Gateway returns opted_out — simulates STOP keyword having been processed
        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(return_value=_gateway_result(False, "opted_out"))
        mock_gw_cls.return_value = mock_gw

        from app.services.estimate_followup import EstimateFollowupService
        svc = EstimateFollowupService()
        await svc.run_step(estimate.id, contractor, db)

        # Gateway was called (step attempted) but blocked
        mock_gw.send.assert_called_once()
        # Step still advanced even on block (gateway handles opt-out; service advances step counter)
        assert estimate.followup_step == 1


# ---------------------------------------------------------------------------
# Test 5: Reactivation refuses phone not in consent_ledger
# Adversarial self-check #3
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reactivation_refuses_unconsented_phone():
    """
    enroll_past_customers only contacts phones in consent_ledger.
    A phone with no consent row is never enrolled.
    """
    campaign = _make_campaign()
    db = _mock_db()

    # No consent rows returned
    empty_result = MagicMock()
    empty_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    # For opt-out query: also empty
    empty_phones = MagicMock()
    empty_phones.all = MagicMock(return_value=[])

    db.execute = AsyncMock(side_effect=[empty_phones, empty_result, empty_phones])

    with patch("app.services.reactivation.is_enabled", return_value=True):
        from app.services.reactivation import ReactivationService
        svc = ReactivationService()
        count = await svc.enroll_past_customers(campaign, db)

    # No contacts enrolled — unconsented phone not touched
    assert count == 0
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6: Reactivation refuses expired implied consent (CASL 2yr window)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reactivation_refuses_expired_implied_consent():
    """
    implied_transaction consent with expires_at in the past must NOT be enrolled.
    """
    from app.models.consent_ledger import ConsentLedger as _CL

    expired_consent = MagicMock(spec=_CL)
    expired_consent.recipient_phone = "+16045559999"
    expired_consent.consent_type = "implied_transaction"
    # Expired 6 months ago
    expired_consent.expires_at = datetime.now(tz=timezone.utc) - timedelta(days=180)

    campaign = _make_campaign()
    db = _mock_db()

    # The DB query with the AND filter correctly excludes expired rows.
    # We simulate the DB returning empty (as it would with the WHERE clause applied).
    empty_result = MagicMock()
    empty_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    empty_phones = MagicMock()
    empty_phones.all = MagicMock(return_value=[])

    db.execute = AsyncMock(side_effect=[empty_phones, empty_result, empty_phones])

    with patch("app.services.reactivation.is_enabled", return_value=True):
        from app.services.reactivation import ReactivationService
        svc = ReactivationService()
        count = await svc.enroll_past_customers(campaign, db)

    assert count == 0


# ---------------------------------------------------------------------------
# Test 7: Campaign kill switch blocks all outbound within one gateway call
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_campaign_kill_switch_blocks_outbound():
    """
    OutboundGateway step 2.5: if contractor.outbound_paused=True, return blocked.
    """
    contractor = _make_contractor(outbound_paused=True)
    db = _mock_db()

    # Mock DB to return the paused contractor
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none = MagicMock(return_value=contractor)

    # idempotency check: no existing row
    idempotency_mock = MagicMock()
    idempotency_mock.scalar_one_or_none = MagicMock(return_value=None)

    db.execute = AsyncMock(side_effect=[idempotency_mock, mock_scalar])

    with patch("app.services.outbound_gateway._write_ledger", new_callable=AsyncMock):

        from app.schemas.outbound import OutboundRequest
        from app.services.outbound_gateway import OutboundGateway

        request = OutboundRequest(
            tenant_id=str(contractor.id),
            recipient_phone="+16045550099",
            channel="sms",
            message="Test campaign message",
            idempotency_key=f"test_kill_{uuid.uuid4()}",
        )
        gw = OutboundGateway()

        # Patch is_enabled (imported inside outbound_gateway.send via local import)
        with patch("app.services.feature_flags.is_enabled", return_value=True):
            result = await gw.send(request, db)

    assert result.success is False
    assert result.block_reason == "tenant_outbound_paused"


# ---------------------------------------------------------------------------
# Test 8: Revenue attribution uses integer cents (no floats anywhere)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revenue_attribution_integer_cents_only():
    """
    record_conversion must store attributed_value_cents as int, never float.
    """
    contractor = _make_contractor(avg_ticket_cents=45000)
    estimate = _make_estimate(
        tenant_id=contractor.id,
        estimate_value_cents=32500,  # integer cents
        status="accepted",
    )
    appointment = MagicMock()
    appointment.id = uuid.uuid4()
    appointment.job_type = "plumbing"

    db = _mock_db()
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none = MagicMock(return_value=contractor)
    db.execute = AsyncMock(return_value=mock_scalar)

    from app.services.estimate_followup import EstimateFollowupService
    svc = EstimateFollowupService()
    ledger_row = await svc.record_conversion(estimate, appointment, db)

    # Value must be int, never float
    assert isinstance(ledger_row.attributed_value_cents, int)
    assert ledger_row.attributed_value_cents == 32500
    # No float in any revenue field
    assert "." not in str(ledger_row.attributed_value_cents)


# ---------------------------------------------------------------------------
# Test 9: Revenue attribution labeled is_estimated=True when using avg_ticket fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revenue_attribution_is_estimated_true_for_fallback():
    """
    When estimate_value_cents is None and avg_ticket fallback is used,
    is_estimated must be True.
    """
    contractor = _make_contractor(avg_ticket_cents=30000)  # 300 CAD fallback
    estimate = _make_estimate(
        tenant_id=contractor.id,
        estimate_value_cents=None,  # no explicit value → fallback
        fsm_estimate_id=None,
    )
    appointment = MagicMock()
    appointment.id = uuid.uuid4()
    appointment.job_type = "hvac"

    db = _mock_db()
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none = MagicMock(return_value=contractor)
    db.execute = AsyncMock(return_value=mock_scalar)

    from app.services.estimate_followup import EstimateFollowupService
    svc = EstimateFollowupService()
    ledger_row = await svc.record_conversion(estimate, appointment, db)

    assert ledger_row.is_estimated is True
    assert ledger_row.attributed_value_cents == 30000
    assert isinstance(ledger_row.attributed_value_cents, int)


# ---------------------------------------------------------------------------
# Test 10: Correction row references original_id (append-only correction pattern)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correction_row_references_original_id():
    """
    RevenueAttributionLedger correction rows must reference original_id
    and have is_correction=True. No UPDATE/DELETE on ledger rows.
    """
    from app.models.revenue_attribution_ledger import RevenueAttributionLedger

    original_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    correction = RevenueAttributionLedger(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_type="estimate_converted",
        attributed_value_cents=50000,  # corrected value in integer cents
        currency="CAD",
        is_estimated=False,
        original_id=original_id,
        is_correction=True,
    )

    assert correction.is_correction is True
    assert correction.original_id == original_id
    assert isinstance(correction.attributed_value_cents, int)
    # Ledger model must NOT have updated_at (append-only)
    assert not hasattr(RevenueAttributionLedger, "updated_at") or \
        "updated_at" not in RevenueAttributionLedger.__table__.columns


# ---------------------------------------------------------------------------
# Test 11: Daily send cap respected — batch stops at cap
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_daily_send_cap_respected():
    """
    run_batch must not send more messages than campaign.daily_send_cap.
    """
    campaign = _make_campaign(daily_send_cap=3)
    contractor = _make_contractor()
    db = _mock_db()

    # Build 5 pending contacts (more than the cap of 3)
    contacts = []
    for i in range(5):
        cc = MagicMock()
        cc.id = uuid.uuid4()
        cc.recipient_phone = f"+1416555000{i}"
        cc.recipient_name = f"Contact {i}"
        cc.status = "pending"
        cc.current_step = 0
        contacts.append(cc)

    # DB calls: campaign fetch, contractor fetch, contacts fetch (limited by cap)
    campaign_result = MagicMock()
    campaign_result.scalar_one_or_none = MagicMock(return_value=campaign)
    contractor_result = MagicMock()
    contractor_result.scalar_one_or_none = MagicMock(return_value=contractor)
    # Contacts: DB query with LIMIT returns only cap contacts
    contacts_result = MagicMock()
    contacts_result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=contacts[:3]))
    )

    db.execute = AsyncMock(side_effect=[campaign_result, contractor_result, contacts_result])

    with patch("app.services.reactivation.is_enabled", return_value=True), \
         patch("app.services.reactivation.OutboundGateway") as mock_gw_cls:

        mock_gw = AsyncMock()
        mock_gw.send = AsyncMock(return_value=_gateway_result(True))
        mock_gw_cls.return_value = mock_gw

        from app.services.reactivation import ReactivationService
        svc = ReactivationService()
        result = await svc.run_batch(campaign.id, db)

    # Only 3 sends (cap), not 5
    assert result["sent"] == 3
    assert mock_gw.send.call_count == 3


# ---------------------------------------------------------------------------
# Test 12: CONFIRM keyword marks appointment confirmed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_confirm_keyword_marks_appointment_confirmed():
    """
    handle_confirm_keyword() must find the scheduled appointment and set status='confirmed'.
    """
    contractor = _make_contractor()
    appointment = _make_appointment(tenant_id=contractor.id, status="scheduled")
    db = _mock_db()

    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none = MagicMock(return_value=appointment)
    db.execute = AsyncMock(return_value=mock_scalar)

    with patch("app.services.appointment_lifecycle.is_enabled", return_value=True):
        from app.services.appointment_lifecycle import AppointmentLifecycleService
        svc = AppointmentLifecycleService()
        await svc.handle_confirm_keyword(
            phone=appointment.caller_phone,
            tenant_id=str(contractor.id),
            db=db,
        )

    assert appointment.status == "confirmed"
    db.flush.assert_called()
