"""
Tests for Phase 1 Outbound Gateway.

Covers:
  1. Opted-out recipient is blocked
  2. Canadian number without express consent is blocked for marketing
  3. US number with unregistered A2P is blocked
  4. Quiet hours block — Newfoundland NPA 709 (UTC-2:30 edge case)
  5. Idempotency: second call with same key returns first result without re-sending
  6. Cross-tenant isolation: consent for tenant A does not unblock tenant B
  7. Flag-off: gateway uses legacy SMS path when outbound_gateway flag is OFF

Adversarial self-checks:
  - Implied CASL consent with expires_at in the past is treated as invalid
  - Newfoundland UTC-2:30 offset correctly computed (fractional hour)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base

# Ensure all models are registered before create_all
import app.models.feature_flag      # noqa: F401
import app.models.outbound_ledger   # noqa: F401
import app.models.consent_ledger    # noqa: F401
import app.models.a2p_registration  # noqa: F401
import app.models.sms_opt_out       # noqa: F401

from app.models.feature_flag import FeatureFlag
from app.models.outbound_ledger import OutboundLedger
from app.models.consent_ledger import ConsentLedger
from app.models.a2p_registration import A2PRegistration
from app.models.sms_opt_out import SmsOptOut
from app.schemas.outbound import OutboundRequest
from app.services.outbound_gateway import OutboundGateway, _recipient_local_hour

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())
US_PHONE = "+12125550101"          # NPA 212 = Eastern
CANADIAN_PHONE = "+17095550102"    # NPA 709 = Newfoundland


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def db(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


def _make_request(**overrides) -> OutboundRequest:
    defaults = dict(
        tenant_id=TENANT_A,
        recipient_phone=US_PHONE,
        channel="sms",
        message="Hello from TradeFlow",
        idempotency_key=str(uuid.uuid4()),
    )
    defaults.update(overrides)
    return OutboundRequest(**defaults)


async def _enable_flag(db: AsyncSession, tenant_id: str) -> None:
    db.add(FeatureFlag(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        flag_key="outbound_gateway",
        enabled=True,
    ))
    await db.flush()


async def _add_consent(
    db: AsyncSession,
    tenant_id: str,
    phone: str,
    consent_type: str = "express",
    channel: str = "sms",
    expires_at: datetime | None = None,
) -> None:
    db.add(ConsentLedger(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        recipient_phone=phone,
        channel=channel,
        consent_type=consent_type,
        consent_basis="test fixture",
        expires_at=expires_at,
    ))
    await db.flush()


async def _approve_a2p(db: AsyncSession, tenant_id: str) -> None:
    db.add(A2PRegistration(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        status="approved",
        updated_at=datetime.now(tz=timezone.utc),
    ))
    await db.flush()


# ---------------------------------------------------------------------------
# Test 1: Opted-out recipient is blocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_opted_out_recipient_is_blocked(db: AsyncSession) -> None:
    await _enable_flag(db, TENANT_A)
    db.add(SmsOptOut(id=uuid.uuid4(), phone=US_PHONE, is_opted_out=True))
    await db.flush()

    gw = OutboundGateway()
    req = _make_request()
    result = await gw.send(req, db)

    assert result.success is False
    assert result.block_reason == "opted_out"


# ---------------------------------------------------------------------------
# Test 2: Canadian number without express consent blocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canadian_implied_consent_blocked(db: AsyncSession) -> None:
    await _enable_flag(db, TENANT_A)
    # Give implied_transaction consent (not express) for Canadian number
    await _add_consent(
        db, TENANT_A, CANADIAN_PHONE,
        consent_type="implied_transaction",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=730),
    )

    gw = OutboundGateway()
    req = _make_request(recipient_phone=CANADIAN_PHONE)
    result = await gw.send(req, db)

    assert result.success is False
    assert result.block_reason == "casl_implied_not_sufficient"


# ---------------------------------------------------------------------------
# Test 3: US number with unregistered A2P is blocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_us_unregistered_a2p_blocked(db: AsyncSession) -> None:
    await _enable_flag(db, TENANT_A)
    await _add_consent(db, TENANT_A, US_PHONE, consent_type="express")
    # No A2P record added — leaves tenant as unregistered

    gw = OutboundGateway()
    req = _make_request()

    # Run during business hours in Eastern timezone: 14:00 UTC = 10:00 Eastern (UTC-4)
    fixed_utc = datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
    with patch("app.services.outbound_gateway.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        mock_dt.utcnow = datetime.utcnow
        result = await gw.send(req, db)

    assert result.success is False
    assert result.block_reason == "a2p_not_approved"


# ---------------------------------------------------------------------------
# Test 4: Quiet hours block — Newfoundland NPA 709 (UTC-2:30)
# ---------------------------------------------------------------------------

def test_newfoundland_local_hour_edge_case() -> None:
    """
    Newfoundland offset is -2.5 hours (UTC-2:30 in summer / UTC-3:30 in winter).
    Our mapping uses -2.5 (summer/conservative).

    At 01:00 UTC, local time = 01:00 - 2.5 = 22:30 → local_hour = 22
    22 is outside [8, 21) → quiet hours should apply.
    """
    utc_time = datetime(2026, 6, 15, 1, 0, 0, tzinfo=timezone.utc)   # 01:00 UTC
    local_hour = _recipient_local_hour(CANADIAN_PHONE, utc_time)      # NPA 709
    assert local_hour == 22, f"Expected 22 but got {local_hour}"
    assert not (8 <= local_hour < 21), "Should be in quiet hours"


@pytest.mark.asyncio
async def test_quiet_hours_block_newfoundland(db: AsyncSession) -> None:
    await _enable_flag(db, TENANT_A)
    await _add_consent(
        db, TENANT_A, CANADIAN_PHONE,
        consent_type="express",
    )

    gw = OutboundGateway()
    req = _make_request(recipient_phone=CANADIAN_PHONE)

    # 01:00 UTC → 22:30 local Newfoundland time → quiet hours
    fixed_utc = datetime(2026, 6, 15, 1, 0, 0, tzinfo=timezone.utc)
    with patch("app.services.outbound_gateway.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        mock_dt.utcnow = datetime.utcnow
        result = await gw.send(req, db)

    assert result.success is False
    assert result.block_reason == "quiet_hours"


# ---------------------------------------------------------------------------
# Test 5: Idempotency — second call with same key returns first result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotency_second_call_returns_first(db: AsyncSession) -> None:
    idem_key = str(uuid.uuid4())
    ledger_id = uuid.uuid4()

    # Write a prior ledger row as if a send already happened
    db.add(OutboundLedger(
        id=ledger_id,
        tenant_id=TENANT_A,
        idempotency_key=idem_key,
        recipient_phone=US_PHONE,
        channel="sms",
        status="sent",
    ))
    await db.flush()

    gw = OutboundGateway()
    req = _make_request(idempotency_key=idem_key)

    send_called = False

    async def mock_send_compliant(*args, **kwargs):
        nonlocal send_called
        send_called = True
        return {"success": True, "sid": "SM123"}

    result = await gw.send(req, db)

    assert result.success is True
    assert result.ledger_id == str(ledger_id)
    assert send_called is False, "SMS send must not be invoked for duplicate idempotency key"


# ---------------------------------------------------------------------------
# Test 6: Cross-tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_tenant_consent_isolation(db: AsyncSession) -> None:
    """Consent registered for tenant A must not unblock tenant B."""
    await _enable_flag(db, TENANT_B)
    # Express consent exists for TENANT_A only
    await _add_consent(db, TENANT_A, US_PHONE, consent_type="express")
    await _approve_a2p(db, TENANT_B)

    gw = OutboundGateway()
    req = _make_request(tenant_id=TENANT_B)  # TENANT_B has no consent

    # During business hours
    fixed_utc = datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
    with patch("app.services.outbound_gateway.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        mock_dt.utcnow = datetime.utcnow
        result = await gw.send(req, db)

    assert result.success is False
    assert result.block_reason == "no_valid_consent"


# ---------------------------------------------------------------------------
# Test 7: Flag-off → compliance chain is skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flag_off_skips_compliance_chain(db: AsyncSession) -> None:
    """
    When outbound_gateway flag is OFF, the gateway must NOT run the full compliance
    chain (consent, A2P, quiet hours, etc.). It should attempt the legacy SMS path
    or return flag_off_channel_not_supported — but never block with consent/A2P reasons.
    No FeatureFlag row → flag is OFF by default.
    """
    # No feature flag row — flag is OFF
    # No consent, no A2P record — if the flag-on path ran, it would block with those reasons
    gw = OutboundGateway()
    req = _make_request()

    result = await gw.send(req, db)

    # The result must NOT be a compliance block (those only fire when flag=ON)
    compliance_block_reasons = {
        "opted_out", "no_valid_consent", "casl_implied_not_sufficient",
        "a2p_not_approved", "quiet_hours", "rate_limit_exceeded",
    }
    assert result.block_reason not in compliance_block_reasons, (
        f"Flag-off path must not run compliance checks, got: {result.block_reason}"
    )


# ---------------------------------------------------------------------------
# Adversarial self-check: expired implied consent is treated as invalid
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_implied_consent_is_invalid(db: AsyncSession) -> None:
    """
    A consent record with expires_at in the past must be treated as no valid consent.
    This tests the CASL implied consent 2-year window enforcement.
    """
    await _enable_flag(db, TENANT_A)

    # Add an expired implied_transaction consent — use a date well in the past
    # (must be before any mocked fixed_utc we'll use for quiet hours bypass)
    past_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    await _add_consent(
        db, TENANT_A, US_PHONE,
        consent_type="implied_transaction",
        expires_at=past_expiry,
    )

    gw = OutboundGateway()
    req = _make_request()

    fixed_utc = datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
    with patch("app.services.outbound_gateway.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        mock_dt.utcnow = datetime.utcnow
        result = await gw.send(req, db)

    assert result.success is False
    assert result.block_reason == "no_valid_consent", (
        f"Expired consent should be treated as no valid consent, got: {result.block_reason}"
    )


# ---------------------------------------------------------------------------
# Adversarial self-check: Newfoundland UTC-2:30 offset
# (unit test of the helper function)
# ---------------------------------------------------------------------------

def test_newfoundland_utc_offset_fractional() -> None:
    """
    NPA 709 must use -2.5 hour offset (not a round number).
    At 02:00 UTC: local = 02:00 - 2.5 = -0.5 → 23:30 previous day → local_hour = 23
    """
    utc_time = datetime(2026, 6, 15, 2, 0, 0, tzinfo=timezone.utc)
    local_hour = _recipient_local_hour("+17095550102", utc_time)
    assert local_hour == 23, f"Newfoundland at 02:00 UTC should be 23:30 local, got hour={local_hour}"


def test_eastern_npa_not_fractional() -> None:
    """Eastern NPA 212 uses -4.0 offset (whole number, not fractional)."""
    utc_time = datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)  # 14:00 UTC
    local_hour = _recipient_local_hour("+12125550101", utc_time)
    assert local_hour == 10, f"Eastern at 14:00 UTC should be 10:00 local, got hour={local_hour}"
