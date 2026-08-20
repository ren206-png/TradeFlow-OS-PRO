"""
Phase 6 tests — Surge Intelligence & Canada-First Differentiation.

Tests:
1.  Surge mode auto-expires at hard ceiling (12h max) — even if alert says 48h
2.  Surge overbooking multiplier capped at 1.5 — reject 2.0
3.  Weather API outage returns [] and does NOT activate surge or crash
4.  Surge greeting is fixed string, not LLM-generated
5.  Manual override deactivates surge regardless of alert expiry
6.  FR SMS template returns QC French text (not English)
7.  EN fallback when FR template missing
8.  Membership acknowledgment is confirmatory ("Are you calling about...") not assertive
9.  Membership match below confidence threshold (74) returns None — no acknowledgment
10. Service agreement match below 80 confidence → no acknowledgment
11. Commercial intake builds valid PipeField handoff payload (schema validation)
12. FSM timeout on membership lookup → fail open (standard flow, no crash)

Adversarial self-checks:
A.  test_api_outage_no_stuck_surge — httpx.ConnectTimeout → no SurgeModeRecord created
B.  test_overbooking_multiplier_hard_cap — multiplier=2.0 → ValueError raised
C.  test_membership_greeting_is_confirmatory — greeting contains "?", no assertive name greeting
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.bilingual import BilingualService, _TEMPLATES
from app.services.commercial_intake import CommercialIntakeService, _simple_fuzzy_score
from app.services.membership import MembershipService
from app.services.weather_surge import (
    WeatherAlertSchema,
    WeatherSurgeService,
    SurgeModeActivationRequest,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_contractor(
    surge_active: bool = False,
    multiplier: Decimal = Decimal("1.0"),
    postal_codes: Optional[list] = None,
    tenant_type: str = "residential",
    default_language: str = "en",
) -> MagicMock:
    c = MagicMock()
    c.id = uuid.uuid4()
    c.name = "Summit Plumbing & Heating"
    c.phone_number = "+16135550100"
    c.surge_mode_active = surge_active
    c.surge_overbooking_multiplier = multiplier
    c.service_area_postal_codes = postal_codes or ["K1A 0A1"]
    c.tenant_type = tenant_type
    c.default_language = default_language
    c.french_voice_id = None
    c.retell_agent_id = None
    return c


def _make_alert(
    surge_type: str = "storm",
    hours_from_now: float = 48.0,
) -> WeatherAlertSchema:
    now = datetime.now(tz=timezone.utc)
    return WeatherAlertSchema(
        alert_id="test_alert_001",
        surge_type=surge_type,
        title="Winter Storm Warning",
        effective_at=now,
        expires_at=now + timedelta(hours=hours_from_now),
        source="environment_canada",
        postal_codes=["K1A 0A1"],
        raw_payload={"title": "Winter Storm Warning"},
    )


def _make_async_db(scalar_one_or_none_value=None, scalars_all_value=None) -> MagicMock:
    """Create a mock async DB session with properly chained async execute result."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    # Build a sync result mock (execute is async, but the result methods are sync)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = scalar_one_or_none_value
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all_value if scalars_all_value is not None else []
    result_mock.scalars.return_value = scalars_mock

    db.execute = AsyncMock(return_value=result_mock)
    return db


# ---------------------------------------------------------------------------
# Test 1: Surge mode auto-expires at hard ceiling (12h max)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surge_expires_at_12h_hard_ceiling():
    """Even if alert says 48h, expires_at must be capped at now + 12h."""
    svc = WeatherSurgeService()
    contractor = _make_contractor()
    alert = _make_alert(hours_from_now=48.0)  # alert says 48h
    # scalar_one_or_none=None → no existing alert row
    db = _make_async_db(scalar_one_or_none_value=None)

    # Patch _notify_owner_activate to avoid full gateway setup
    with patch.object(svc, "_notify_owner_activate", new_callable=AsyncMock):
        record = await svc.activate_surge(contractor, alert, db)

    now = datetime.now(tz=timezone.utc)
    hard_ceiling = now + timedelta(hours=12)

    # expires_at must be at most 12h from now (allow 5s buffer for test execution time)
    assert record.expires_at <= hard_ceiling + timedelta(seconds=5), (
        f"expires_at={record.expires_at} exceeds 12h ceiling={hard_ceiling}"
    )

    # Specifically: a 48h alert should result in 12h expiry, not 48h
    max_possible_48h = now + timedelta(hours=48)
    assert record.expires_at < max_possible_48h - timedelta(hours=30), (
        "expires_at should be ~12h, not 48h"
    )


# ---------------------------------------------------------------------------
# Test 2: Surge overbooking multiplier capped at 1.5 — reject 2.0
# ---------------------------------------------------------------------------

def test_overbooking_multiplier_hard_cap():
    """Attempting to set multiplier=2.0 must raise ValueError."""
    with pytest.raises(Exception) as exc_info:
        SurgeModeActivationRequest(overbooking_multiplier=Decimal("2.0"))
    # Should raise pydantic ValidationError (which is a ValueError subclass or wraps it)
    assert "2.0" in str(exc_info.value) or "cap" in str(exc_info.value).lower() or "exceed" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_activate_surge_rejects_2x_multiplier():
    """activate_surge() with multiplier=2.0 raises ValueError, contractor unchanged."""
    svc = WeatherSurgeService()
    contractor = _make_contractor()
    original_multiplier = contractor.surge_overbooking_multiplier
    alert = _make_alert()
    db = _make_async_db(scalar_one_or_none_value=None)

    with pytest.raises(ValueError):
        await svc.activate_surge(contractor, alert, db, overbooking_multiplier=Decimal("2.0"))

    # Contractor multiplier must be unchanged
    assert contractor.surge_overbooking_multiplier == original_multiplier


# ---------------------------------------------------------------------------
# Adversarial B: owner notification states booked count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_notification_contains_booked_count():
    """Owner activation notification must include a booked count figure."""
    bilingual = BilingualService()
    template = bilingual.get_sms_template("surge_mode_owner_alert", "en")
    # Template has {booked_count} placeholder
    assert "{booked_count}" in template, "surge_mode_owner_alert template must include {booked_count}"
    # When formatted, the message must contain a numeric booked count
    formatted = template.format(
        company_name="Summit Plumbing",
        surge_type="storm",
        expires_at="14:00 UTC",
        booked_count=7,
    )
    assert "7" in formatted, "Formatted owner notification must contain booked count"


# ---------------------------------------------------------------------------
# Test 3: Weather API outage → [] returned, no SurgeModeRecord, no crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_outage_no_stuck_surge():
    """
    Adversarial self-check A:
    When httpx.ConnectTimeout fires, poll_alerts returns [],
    no SurgeModeRecord is created, and the poller exits gracefully.
    """
    import httpx

    svc = WeatherSurgeService()
    contractor = _make_contractor(postal_codes=["K1A 0A1"])
    db = _make_async_db()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))
        mock_client_cls.return_value = mock_client

        alerts = await svc.poll_alerts(contractor, db)

    # Must return empty list on API failure
    assert alerts == [], f"Expected [] on API outage, got {alerts}"

    # DB.add must NOT have been called (no SurgeModeRecord created)
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Surge greeting is fixed string, not LLM-generated
# ---------------------------------------------------------------------------

def test_surge_greeting_is_fixed_string():
    """get_surge_greeting returns a fixed string, never calls an LLM."""
    svc = WeatherSurgeService()
    contractor_name = "Summit Plumbing & Heating"

    for surge_type in ("extreme_cold", "heat", "storm"):
        greeting = svc.get_surge_greeting(surge_type, contractor_name)
        assert isinstance(greeting, str), "Greeting must be a string"
        assert len(greeting) > 10, "Greeting must be non-trivial"
        assert contractor_name in greeting, f"Contractor name must appear in {surge_type} greeting"

    # Unknown surge_type → safe fallback, still a string
    fallback = svc.get_surge_greeting("unknown_type", contractor_name)
    assert isinstance(fallback, str)
    assert contractor_name in fallback


# ---------------------------------------------------------------------------
# Test 5: Manual override deactivates surge regardless of alert expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manual_override_deactivates_surge():
    """deactivate_surge() stamps deactivated_at even if expiry is far future."""
    svc = WeatherSurgeService()
    contractor = _make_contractor(surge_active=True)
    db = _make_async_db()

    # Simulate one active SurgeModeRecord with far-future expiry
    from app.models.surge_mode_record import SurgeModeRecord
    mock_record = MagicMock(spec=SurgeModeRecord)
    mock_record.tenant_id = contractor.id
    mock_record.deactivated_at = None
    mock_record.expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=100)

    db = _make_async_db(scalars_all_value=[mock_record])

    with patch.object(svc, "_notify_owner_deactivate", new_callable=AsyncMock):
        await svc.deactivate_surge(contractor, db)

    # deactivated_at must be set (not None)
    assert mock_record.deactivated_at is not None
    # contractor must be set to not active
    assert contractor.surge_mode_active is False


# ---------------------------------------------------------------------------
# Test 6: FR SMS template returns QC French text (not English)
# ---------------------------------------------------------------------------

def test_fr_template_returns_french():
    """get_sms_template with language='fr' returns QC French, not English."""
    bilingual = BilingualService()

    for template_id in (
        "missed_call_textback",
        "appointment_confirmation",
        "appointment_reminder",
        "estimate_followup_day2",
        "estimate_followup_day10",
        "surge_mode_owner_alert",
    ):
        fr_text = bilingual.get_sms_template(template_id, "fr")
        en_text = bilingual.get_sms_template(template_id, "en")

        assert fr_text != en_text, (
            f"FR template for {template_id} must differ from EN template"
        )
        assert len(fr_text) > 10, f"FR template {template_id} must be non-empty"

        # Spot-check: FR templates should contain French characters or common French words
        fr_indicators = ["vous", "votre", "le ", "la ", "les ", "à ", "é", "è", "ê", "ô", "û"]
        has_french = any(ind in fr_text.lower() for ind in fr_indicators)
        assert has_french, (
            f"FR template {template_id} does not appear to be French: {fr_text[:100]}"
        )


# ---------------------------------------------------------------------------
# Test 7: EN fallback when FR template missing
# ---------------------------------------------------------------------------

def test_en_fallback_when_fr_missing():
    """If a template has no FR variant, get_sms_template falls back to EN."""
    bilingual = BilingualService()

    # Temporarily inject a template with only EN variant
    _TEMPLATES["_test_en_only_template"] = {
        "en": "Test EN only template for {company_name}.",
    }
    try:
        result = bilingual.get_sms_template("_test_en_only_template", "fr")
        assert result == "Test EN only template for {company_name}.", (
            "Should fall back to EN template when FR variant missing"
        )
    finally:
        del _TEMPLATES["_test_en_only_template"]


# ---------------------------------------------------------------------------
# Test 8: Membership acknowledgment is confirmatory, not assertive
# ---------------------------------------------------------------------------

def test_membership_greeting_is_confirmatory():
    """
    Adversarial self-check C:
    get_member_greeting_addition returns a question (contains "?"),
    and does NOT contain the lead's name as a standalone greeting token.
    """
    svc = MembershipService()

    mock_lead = MagicMock()
    mock_lead.caller_name = "John Smith"
    mock_lead.service_address = "123 Maple St, Ottawa ON"

    contractor = _make_contractor()

    # Confidence >= 75 → should return a greeting addition
    greeting = svc.get_member_greeting_addition(mock_lead, contractor, confidence=95)

    assert greeting is not None, "Should return greeting addition for confidence >= 75"
    assert "?" in greeting, f"Greeting must be a question (contain '?'): {greeting}"

    # Must NOT be an assertive greeting like "Hello John!" or "Hi John Smith!"
    # The rule: greeting must not contain the lead's name as a standalone assertion
    name = mock_lead.caller_name
    assertive_patterns = [
        f"Hello {name}",
        f"Hi {name}",
        f"Welcome {name}",
        f"Good morning {name}",
        f"Good afternoon {name}",
    ]
    for pattern in assertive_patterns:
        assert pattern not in greeting, (
            f"Greeting must not be assertive. Found '{pattern}' in: {greeting}"
        )

    # Must contain confirmatory language about the property/service
    assert "Are you calling about" in greeting or "calling about" in greeting.lower(), (
        f"Greeting must be confirmatory about service/property, got: {greeting}"
    )


# ---------------------------------------------------------------------------
# Test 9: Membership match below confidence threshold (74) → None
# ---------------------------------------------------------------------------

def test_membership_below_threshold_returns_none():
    """Confidence 74 is below threshold (75) → get_member_greeting_addition returns None."""
    svc = MembershipService()

    mock_lead = MagicMock()
    mock_lead.caller_name = "Jane Doe"
    mock_lead.service_address = "456 Oak Ave, Toronto ON"

    contractor = _make_contractor()

    greeting = svc.get_member_greeting_addition(mock_lead, contractor, confidence=74)
    assert greeting is None, (
        f"Confidence 74 < threshold 75 must return None, got: {greeting}"
    )

    # Also test exactly at threshold
    at_threshold = svc.get_member_greeting_addition(mock_lead, contractor, confidence=75)
    assert at_threshold is not None, "Confidence exactly 75 should trigger acknowledgment"

    # Test with no lead
    no_lead = svc.get_member_greeting_addition(None, contractor, confidence=95)
    assert no_lead is None, "None lead must return None"


# ---------------------------------------------------------------------------
# Test 10: Service agreement match below 80 confidence → no acknowledgment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_agreement_below_80_no_acknowledgment():
    """
    Service agreement fuzzy match below 80 must not return acknowledgment.
    match confidence >= 80 required.
    """
    svc = CommercialIntakeService()

    # fuzzy match score test
    score_exact = _simple_fuzzy_score("ABC Mechanical Ltd", "ABC Mechanical Ltd")
    assert score_exact == 100, f"Exact match should be 100, got {score_exact}"

    score_low = _simple_fuzzy_score("Smith Plumbing", "Acme HVAC Corporation")
    assert score_low < 80, f"Unrelated names should score < 80, got {score_low}"

    # Test with mocked DB returning a low-confidence agreement
    contractor = _make_contractor(tenant_type="commercial_mechanical")
    mock_agreement_low = MagicMock()
    mock_agreement_low.id = uuid.uuid4()
    mock_agreement_low.company_name = "Totally Different Corp XYZ"
    mock_agreement_low.agreement_number = "SA-001"
    mock_agreement_low.priority_routing = False
    mock_agreement_low.is_active = True

    db = _make_async_db(scalars_all_value=[mock_agreement_low])

    is_match, result = await svc.check_service_agreement(
        "Smith Plumbing",  # low similarity to "Totally Different Corp XYZ"
        contractor.id,
        db,
    )
    assert is_match is False, "Low confidence match must return is_match=False"
    assert result is None, "Low confidence match must return None result"


@pytest.mark.asyncio
async def test_service_agreement_at_80_acknowledgment():
    """Service agreement exact/near match >= 80 → confirmatory acknowledgment returned."""
    svc = CommercialIntakeService()
    contractor = _make_contractor(tenant_type="commercial_mechanical")

    mock_agreement = MagicMock()
    mock_agreement.id = uuid.uuid4()
    mock_agreement.company_name = "ABC Mechanical Services"
    mock_agreement.agreement_number = "SA-100"
    mock_agreement.priority_routing = True
    mock_agreement.is_active = True

    db = _make_async_db(scalars_all_value=[mock_agreement])

    is_match, result = await svc.check_service_agreement(
        "ABC Mechanical Services",  # exact match
        contractor.id,
        db,
    )
    assert is_match is True
    assert result is not None
    assert result.match_confidence >= 80

    acknowledgment = svc.get_confirmatory_acknowledgment(result)
    assert acknowledgment is not None
    assert "?" in acknowledgment, "Acknowledgment must be a question"
    assert "Are you calling about service for" in acknowledgment


# ---------------------------------------------------------------------------
# Test 11: Commercial intake builds valid PipeField handoff payload
# ---------------------------------------------------------------------------

def test_commercial_intake_pipefield_payload_schema():
    """build_pipefield_handoff_payload returns dict with all required keys."""
    svc = CommercialIntakeService()

    intake_data = {
        "service_address": "789 Industrial Blvd, Unit 12, Brampton ON",
        "building_id": "BLDG-42",
        "unit_id": "UNIT-12",
        "equipment_tag_id": "RTU-007",
        "caller_company": "ABC Mechanical Services",
        "po_number": "PO-2026-0042",
        "site_contact_name": "Mike Foreman",
        "site_contact_phone": "+19055550199",
        "account_contact_name": "Janet Owner",
        "urgency": "same_day",
    }

    from app.services.commercial_intake import ServiceAgreementMatchResult
    match_result = ServiceAgreementMatchResult(
        is_match=True,
        match_confidence=95,
        agreement_id=str(uuid.uuid4()),
        company_name="ABC Mechanical Services",
        agreement_number="SA-100",
        priority_routing=True,
    )

    payload = svc.build_pipefield_handoff_payload(
        lead_id=str(uuid.uuid4()),
        intake_data=intake_data,
        match_result=match_result,
    )

    # Required top-level keys
    required_keys = {"job_site", "equipment", "service_agreement", "contacts", "urgency"}
    assert required_keys.issubset(payload.keys()), (
        f"Payload missing keys. Expected {required_keys}, got {set(payload.keys())}"
    )

    # job_site sub-keys
    assert "address" in payload["job_site"]
    assert "building_id" in payload["job_site"]
    assert "unit_id" in payload["job_site"]

    # equipment sub-keys
    assert "equipment_tag_id" in payload["equipment"]

    # service_agreement sub-keys
    assert "agreement_id" in payload["service_agreement"]
    assert "company_name" in payload["service_agreement"]
    assert "po_number" in payload["service_agreement"]
    assert payload["service_agreement"]["priority_routing"] is True

    # contacts sub-keys
    assert "site_contact_name" in payload["contacts"]
    assert "site_contact_phone" in payload["contacts"]

    # urgency
    assert payload["urgency"] == "same_day"

    # Schema metadata
    assert payload["_schema_version"] == "1.0"
    assert payload["_integration_status"] == "pending"


# ---------------------------------------------------------------------------
# Test 12: FSM timeout on membership lookup → fail open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fsm_timeout_fail_open():
    """
    Adversarial self-check: FSM/DB timeout on membership lookup returns (None, 0).
    Standard flow continues, no crash.
    """
    svc = MembershipService()
    MembershipService.clear_cache()

    contractor = _make_contractor()
    db = _make_async_db()

    async def slow_lookup(*args, **kwargs):
        await asyncio.sleep(10)  # Simulate slow FSM (>3s timeout)
        return None, 0

    with patch.object(svc, "_db_lookup", side_effect=slow_lookup):
        lead, confidence = await svc.lookup_caller(
            "+16135559999", contractor, db
        )

    assert lead is None, "Timeout must return None lead (fail open)"
    assert confidence == 0, "Timeout must return 0 confidence"

    # Standard flow: greeting must be None (no acknowledgment)
    greeting = svc.get_member_greeting_addition(lead, contractor, confidence)
    assert greeting is None, "No acknowledgment on timeout"


# ---------------------------------------------------------------------------
# Additional: Surge multiplier boundary tests
# ---------------------------------------------------------------------------

def test_surge_multiplier_exactly_1_5_accepted():
    """Multiplier of exactly 1.5 must be accepted (it is the hard cap, not over it)."""
    req = SurgeModeActivationRequest(overbooking_multiplier=Decimal("1.5"))
    assert req.overbooking_multiplier == Decimal("1.5")


def test_surge_multiplier_1_0_default():
    """Default multiplier is 1.0 (surge off = no overbooking)."""
    req = SurgeModeActivationRequest()
    assert req.overbooking_multiplier == Decimal("1.0")


# ---------------------------------------------------------------------------
# Additional: Bilingual — all eight templates have FR variants
# ---------------------------------------------------------------------------

def test_all_phase6_templates_have_fr_variants():
    """All Phase 2/4/6 outbound templates must have FR variants."""
    required_templates = [
        "missed_call_textback",
        "appointment_confirmation",
        "appointment_reminder",
        "estimate_followup_day2",
        "estimate_followup_day10",
        "reactivation_seasonal_hvac_fall",
        "reactivation_seasonal_hvac_spring",
        "surge_mode_owner_alert",
    ]
    bilingual = BilingualService()
    for tid in required_templates:
        fr = bilingual.get_sms_template(tid, "fr")
        en = bilingual.get_sms_template(tid, "en")
        assert fr, f"FR template must exist for {tid}"
        assert en, f"EN template must exist for {tid}"
        # FR and EN must differ
        assert fr != en, f"FR and EN templates must differ for {tid}"
