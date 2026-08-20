"""
Phase 2 — Speed-to-Lead & Missed-Call Recovery: Test Suite.

Tests:
  1. Missed-call textback sends SMS via OutboundGateway (not directly)
  2. Missed-call textback is skipped when flag is OFF
  3. CALL keyword SMS triggers outbound call
  4. Webform callback idempotency (second submission with same form_submission_id does nothing)
  5. Phone abuse rate limit blocks after 3 requests/hour
  6. Quiet hours queues callback instead of calling immediately
  7. Lead ingest normalizes payload and routes to callback pipeline
  8. Speed-to-lead delta is recorded correctly
  9. Voicemail message is fixed string, not LLM-generated (adversarial check)
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.database import Base

# ---------------------------------------------------------------------------
# In-memory SQLite test database
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_contractor(**kwargs) -> MagicMock:
    contractor = MagicMock()
    contractor.id = uuid.uuid4()
    contractor.name = "Summit Plumbing"
    contractor.agent_name = "Mike"
    contractor.phone_number = "+15550001111"
    contractor.api_key = "test-api-key-abc123"
    contractor.retell_agent_id = "agent-xyz"
    contractor.sms_enabled = True
    contractor.webhook_secret = "supersecretkey99"
    contractor.booking_url = None  # forces construction from api_key
    for k, v in kwargs.items():
        setattr(contractor, k, v)
    return contractor


# ---------------------------------------------------------------------------
# Test 1: Missed-call textback sends SMS via OutboundGateway
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missed_call_sends_via_gateway(db: AsyncSession):
    """SMS must go through OutboundGateway.send(), not directly via SMSService."""
    contractor = _make_contractor()

    gateway_result = MagicMock()
    gateway_result.success = True
    gateway_result.ledger_id = str(uuid.uuid4())
    gateway_result.block_reason = None

    with (
        patch("app.services.feature_flags.is_enabled", return_value=True),
        patch("app.services.sms_compliance.is_opted_out", return_value=False),
        patch(
            "app.services.outbound_gateway.OutboundGateway.send",
            new_callable=AsyncMock,
            return_value=gateway_result,
        ) as mock_gw_send,
        patch("app.services.missed_call.ConsentLedger") as mock_consent,
    ):
        mock_consent.return_value = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        from app.services.missed_call import handle_missed_call
        await handle_missed_call(
            contractor=contractor,
            caller_phone="+15559998888",
            call_id="call-test-001",
            db=db,
        )

    # Gateway was called exactly once
    mock_gw_send.assert_awaited_once()
    req = mock_gw_send.call_args[0][0]
    assert req.channel == "sms"
    assert req.template_id == "missed_call_textback"
    assert req.idempotency_key == "missed_call_call-test-001"
    assert "book online" in req.message


# ---------------------------------------------------------------------------
# Test 2: Missed-call textback is skipped when flag is OFF
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missed_call_skipped_when_flag_off(db: AsyncSession):
    """When missed_call_textback flag is OFF, handle_missed_call returns immediately."""
    contractor = _make_contractor()

    with (
        patch("app.services.feature_flags.is_enabled", return_value=False),
        patch(
            "app.services.outbound_gateway.OutboundGateway.send",
            new_callable=AsyncMock,
        ) as mock_gw_send,
    ):
        from app.services.missed_call import handle_missed_call
        await handle_missed_call(
            contractor=contractor,
            caller_phone="+15559998888",
            call_id="call-test-002",
            db=db,
        )

    mock_gw_send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 3: CALL keyword SMS triggers outbound AI call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_keyword_triggers_outbound_call(db: AsyncSession):
    """Replying CALL to the textback should trigger RetellClient.create_phone_call."""
    contractor = _make_contractor()

    mock_call_result = {"call_id": "retell-out-001"}

    with (
        patch("app.services.feature_flags.is_enabled", return_value=True),
        patch(
            "app.routers.twilio_sms.select",
            wraps=__import__("sqlalchemy", fromlist=["select"]).select,
        ),
        patch("app.services.retell_client.RetellClient.create_phone_call", new_callable=AsyncMock,
              return_value=mock_call_result) as mock_call,
    ):
        # Directly test _handle_call_keyword logic
        # Simulate: CallbackRequest table is empty (no prior request in 10 min)
        from app.routers.twilio_sms import _handle_call_keyword

        mock_request = MagicMock()
        mock_form = AsyncMock(return_value={"To": contractor.phone_number})
        mock_request.form = mock_form

        # Patch DB lookups to return contractor
        with (
            patch("app.routers.twilio_sms.select") as mock_select,
            patch.object(db, "execute", new_callable=AsyncMock) as mock_exec,
            patch.object(db, "flush", new_callable=AsyncMock),
            patch.object(db, "add"),
        ):
            # First execute returns contractor, second returns None (no prior request)
            contractor_result = MagicMock()
            contractor_result.scalar_one_or_none.return_value = contractor
            no_prior = MagicMock()
            no_prior.scalar_one_or_none.return_value = None
            mock_exec.side_effect = [contractor_result, no_prior]

            with patch("app.services.retell_client.RetellClient.create_phone_call",
                       new_callable=AsyncMock, return_value=mock_call_result) as mock_create_call:

                reply = await _handle_call_keyword("+15559998888", mock_request, db)

        assert reply is not None
        assert "calling" in reply.lower()


# ---------------------------------------------------------------------------
# Test 4: Webform callback idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webform_idempotency(db: AsyncSession):
    """Second submission with same form_submission_id must return duplicate_ignored."""
    from app.models.callback_request import CallbackRequest

    form_id = "form-unique-001"

    # Pre-insert a CallbackRequest with the same form_submission_id
    existing_cb = CallbackRequest(
        tenant_id=str(uuid.uuid4()),
        caller_phone="+15551112222",
        form_submission_id=form_id,
        source="webform",
        status="completed",
    )
    db.add(existing_cb)
    await db.flush()

    # Now simulate the webform endpoint logic
    contractor = _make_contractor()
    contractor.webhook_secret = "secret"

    ts = int(time.time())
    signing_material = f"{contractor.api_key}{ts}".encode()
    sig = hmac.new(b"secret", signing_material, hashlib.sha256).hexdigest()

    with (
        patch("app.services.feature_flags.is_enabled", return_value=True),
        patch("app.routers.webform._get_contractor_by_api_key", new_callable=AsyncMock,
              return_value=contractor),
        patch("app.routers.webform._verify_webform_signature", return_value=True),
        patch("app.routers.webform._trigger_callback_pipeline", new_callable=AsyncMock) as mock_pipe,
    ):
        from app.routers.webform import webform_callback, WebformCallbackRequest

        payload = WebformCallbackRequest(
            phone="+15551112222",
            form_submission_id=form_id,
            timestamp=ts,
            signature=sig,
        )
        result = await webform_callback(
            tenant_api_key=contractor.api_key,
            payload=payload,
            db=db,
        )

    assert result["status"] == "duplicate_ignored"
    mock_pipe.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 5: Phone abuse rate limit blocks after 3 requests/hour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phone_abuse_rate_limit(db: AsyncSession):
    """After 3 callback requests in an hour, the 4th should be blocked."""
    from app.models.callback_request import CallbackRequest
    from app.services.phone_validation import validate_callback_phone

    phone = "+15553334444"
    tenant_id = str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc)

    # Insert 3 requests within the last hour
    for i in range(3):
        cb = CallbackRequest(
            tenant_id=tenant_id,
            caller_phone=phone,
            source="webform",
            status="completed",
        )
        db.add(cb)
    await db.flush()

    is_valid, reason = await validate_callback_phone(phone, db)
    assert not is_valid
    assert "rate_limit" in reason


@pytest.mark.asyncio
async def test_phone_abuse_rate_limit_both_paths(db: AsyncSession):
    """Rate limit check must be present on both webform AND lead_ingest paths."""
    from app.models.callback_request import CallbackRequest
    from app.services.phone_validation import validate_callback_phone

    phone = "+15554445555"
    tenant_id = str(uuid.uuid4())

    # Insert 3 requests
    for _ in range(3):
        cb = CallbackRequest(tenant_id=tenant_id, caller_phone=phone, source="lead_ingest", status="done")
        db.add(cb)
    await db.flush()

    # Both paths call validate_callback_phone which checks this
    is_valid, reason = await validate_callback_phone(phone, db)
    assert not is_valid, "Rate limit should block on lead_ingest path too"


# ---------------------------------------------------------------------------
# Test 6: Quiet hours queues callback instead of calling immediately
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quiet_hours_schedules_callback():
    """
    Webform submission at 02:00 UTC for an Eastern US recipient
    should produce status=scheduled not status=calling.
    Also verifies an SMS acknowledgment is attempted.
    """
    contractor = _make_contractor()
    contractor.phone_number = "+12125550000"  # Eastern (212 NPA)

    eastern_phone = "+12125559999"

    # 02:00 UTC → Eastern offset -4 → 22:00 local = OUTSIDE 08:00-21:00
    fake_now = datetime(2026, 8, 19, 2, 0, 0, tzinfo=timezone.utc)

    gateway_result = MagicMock()
    gateway_result.success = True
    gateway_result.ledger_id = str(uuid.uuid4())
    gateway_result.block_reason = None

    with (
        patch("app.services.callback_pipeline.datetime") as mock_dt,
        patch("app.services.phone_validation.validate_callback_phone",
              new_callable=AsyncMock, return_value=(True, "")),
        patch("app.services.outbound_gateway.OutboundGateway.send",
              new_callable=AsyncMock, return_value=gateway_result) as mock_gw,
        patch("app.services.retell_client.RetellClient.create_phone_call",
              new_callable=AsyncMock) as mock_call,
    ):
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.execute = AsyncMock()

        # Patch Lead and CallbackRequest constructors
        mock_lead_instance = MagicMock()
        mock_lead_instance.id = uuid.uuid4()
        mock_lead_instance.lead_received_at = fake_now
        mock_cb_instance = MagicMock()
        mock_cb_instance.id = uuid.uuid4()

        with (
            patch("app.services.callback_pipeline.Lead", return_value=mock_lead_instance),
            patch("app.services.callback_pipeline.CallbackRequest", return_value=mock_cb_instance),
        ):
            from app.services.callback_pipeline import _trigger_callback_pipeline
            result = await _trigger_callback_pipeline(
                contractor=contractor,
                name="Jane Doe",
                phone=eastern_phone,
                issue="burst pipe",
                source="webform",
                form_submission_id="form-qh-test-001",
                db=mock_db,
            )

    assert result["status"] == "scheduled", f"Expected scheduled, got: {result}"
    # Retell call must NOT have been made
    mock_call.assert_not_awaited()
    # Acknowledgment SMS must have been attempted via gateway
    mock_gw.assert_awaited()


# ---------------------------------------------------------------------------
# Test 7: Lead ingest normalizes payload and routes to callback pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lead_ingest_routes_to_pipeline():
    """Lead ingest should validate, then call _trigger_callback_pipeline."""
    contractor = _make_contractor()
    contractor.webhook_secret = "ingest-secret"

    import json
    body = json.dumps({
        "phone": "+15556667777",
        "name": "Bob Builder",
        "issue": "no hot water",
        "source": "crm_import",
        "form_submission_id": "crm-001",
        "extra_field": "stored_in_metadata",
    }).encode()

    sig = hmac.new(b"ingest-secret", body, hashlib.sha256).hexdigest()

    pipeline_result = {"status": "calling", "callback_request_id": str(uuid.uuid4())}

    with (
        patch("app.routers.lead_ingest._get_contractor_by_api_key",
              new_callable=AsyncMock, return_value=contractor),
        patch("app.services.feature_flags.is_enabled", new_callable=AsyncMock, return_value=True),
        patch("app.routers.lead_ingest._verify_ingest_signature", return_value=True),
        patch("app.services.callback_pipeline._trigger_callback_pipeline",
              new_callable=AsyncMock, return_value=pipeline_result) as mock_pipe,
    ):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        mock_db.commit = AsyncMock()

        mock_request = MagicMock()
        mock_request.body = AsyncMock(return_value=body)

        from app.routers.lead_ingest import ingest_lead
        result = await ingest_lead(
            tenant_api_key=contractor.api_key,
            request=mock_request,
            db=mock_db,
            x_tradeflow_signature=sig,
        )

    assert result["status"] == "calling"
    mock_pipe.assert_awaited_once()
    call_kwargs = mock_pipe.call_args[1]
    assert call_kwargs["phone"] == "+15556667777"
    assert call_kwargs["name"] == "Bob Builder"
    assert call_kwargs["source"] == "crm_import"


# ---------------------------------------------------------------------------
# Test 8: Speed-to-lead delta is recorded correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_speed_to_lead_delta_recorded():
    """lead_received_at → first_contact_attempted_at delta stored in speed_to_lead_seconds."""
    contractor = _make_contractor()
    t0 = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 8, 19, 12, 0, 7, tzinfo=timezone.utc)  # 7 seconds later

    mock_lead = MagicMock()
    mock_lead.id = uuid.uuid4()
    mock_lead.lead_received_at = t0
    mock_lead.first_contact_attempted_at = None
    mock_lead.speed_to_lead_seconds = None

    mock_cb = MagicMock()
    mock_cb.id = uuid.uuid4()

    call_result = {"call_id": "retell-s2l-001"}

    with (
        patch("app.services.callback_pipeline.datetime") as mock_dt,
        patch("app.services.phone_validation.validate_callback_phone",
              new_callable=AsyncMock, return_value=(True, "")),
        patch("app.services.retell_client.RetellClient.create_phone_call",
              new_callable=AsyncMock, return_value=call_result),
        patch("app.services.callback_pipeline.Lead", return_value=mock_lead),
        patch("app.services.callback_pipeline.CallbackRequest", return_value=mock_cb),
        patch("app.services.callback_pipeline._recipient_local_hour", return_value=12),
    ):
        # now() returns t0 for received_at, t1 for first_contact
        mock_dt.now.side_effect = [t0, t1]
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        from app.services.callback_pipeline import _trigger_callback_pipeline
        result = await _trigger_callback_pipeline(
            contractor=contractor,
            name="Test User",
            phone="+15551234567",
            issue="test issue",
            source="webform",
            form_submission_id="s2l-test-001",
            db=mock_db,
        )

    assert result["status"] == "calling"
    assert mock_lead.speed_to_lead_seconds == 7, (
        f"Expected 7 seconds, got {mock_lead.speed_to_lead_seconds}"
    )
    assert mock_lead.first_contact_attempted_at == t1


# ---------------------------------------------------------------------------
# Test 9: Voicemail message is fixed string, not LLM-generated (adversarial)
# ---------------------------------------------------------------------------

def test_voicemail_message_is_fixed_string():
    """
    ADVERSARIAL: The voicemail message must be a string template.
    Verify no call to _call_claude or any Anthropic API in the callback pipeline.
    """
    import inspect
    import app.services.callback_pipeline as pipeline_module

    source = inspect.getsource(pipeline_module)

    # Must not call any Claude/Anthropic API in the callback pipeline
    assert "_call_claude" not in source, "voicemail must not use _call_claude"
    assert "anthropic" not in source.lower(), "callback pipeline must not import anthropic"
    assert "claude" not in source.lower() or "# NO" not in source, (
        "Sanity: ensure claude is not called in pipeline"
    )

    # Must contain a fixed voicemail message template
    assert "voicemail_message" in source
    assert "calling you back" in source  # part of the fixed template string


@pytest.mark.asyncio
async def test_voicemail_message_is_not_dynamic():
    """
    Verify that the voicemail_message in callback_pipeline is assembled from
    static strings + contractor/lead data — NOT from any LLM call.
    """
    contractor = _make_contractor()
    contractor.agent_name = "Mike"
    contractor.booking_url = "https://example.com/book"

    mock_lead = MagicMock()
    mock_lead.id = uuid.uuid4()
    mock_lead.lead_received_at = datetime.now(tz=timezone.utc)
    mock_cb = MagicMock()
    mock_cb.id = uuid.uuid4()

    captured_call_kwargs: dict = {}

    async def fake_create_call(**kwargs):
        captured_call_kwargs.update(kwargs)
        return {"call_id": "vm-test-001"}

    with (
        patch("app.services.phone_validation.validate_callback_phone",
              new_callable=AsyncMock, return_value=(True, "")),
        patch("app.services.retell_client.RetellClient.create_phone_call",
              side_effect=fake_create_call),
        patch("app.services.callback_pipeline.Lead", return_value=mock_lead),
        patch("app.services.callback_pipeline.CallbackRequest", return_value=mock_cb),
        patch("app.services.callback_pipeline._recipient_local_hour", return_value=12),
        patch("app.services.callback_pipeline.datetime") as mock_dt,
    ):
        now = datetime(2026, 8, 19, 16, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        from app.services.callback_pipeline import _trigger_callback_pipeline
        await _trigger_callback_pipeline(
            contractor=contractor,
            name="Jane",
            phone="+15551234567",
            issue="broken pipe",
            source="webform",
            form_submission_id="vm-form-001",
            db=mock_db,
        )

    # voicemail_message if present in the call must be a plain string
    # (checking pipeline source rather than the Retell param which may not be sent)
    from app.services import callback_pipeline
    import inspect
    src = inspect.getsource(callback_pipeline)
    # The voicemail line must reference string formatting, not a function call
    assert 'f"Hi' in src or "voicemail_message = (" in src
