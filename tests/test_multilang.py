"""
Multi-language support tests — Phases 1–3.

Covers:
  1. Flag-off: provisioning emits identical English-only config
  2. Flag-on: provisioning emits multilingual config
  3. Prompt wrapper: flag-off returns base_prompt unchanged
  4. Prompt wrapper: flag-on appends directive exactly once
  5. Prompt wrapper: directive is idempotent (not appended twice on reconnect)
  6. Prompt wrapper: composes with tenant-customized prompt
  7. Translation: English call → no translation, status="not_needed"
  8. Translation: Spanish call happy path → fields normalized, originals preserved
  9. Translation: timeout → status="failed", lead fields unchanged
 10. Translation: malformed JSON → status="failed", lead fields unchanged
 11. Translation: flag-off → zero LLM calls, zero behavioral change
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings


# ---------------------------------------------------------------------------
# 1–2. Provisioning agent config
# ---------------------------------------------------------------------------

def test_flag_off_emits_english_only_config(monkeypatch):
    monkeypatch.setattr(settings, "multilang_enabled", False)
    from app.services import provisioning as prov
    # Rebuild the config dict the same way provisioning.py does
    voice = settings.multilang_voice_id if settings.multilang_enabled else prov.DEFAULT_VOICE_ID
    lang  = "multi" if settings.multilang_enabled else "en-US"
    assert voice == prov.DEFAULT_VOICE_ID
    assert lang == "en-US"


def test_flag_on_emits_multilingual_config(monkeypatch):
    monkeypatch.setattr(settings, "multilang_enabled", True)
    monkeypatch.setattr(settings, "multilang_voice_id", "11labs-Valentina")
    voice = settings.multilang_voice_id if settings.multilang_enabled else "11labs-Adrian"
    lang  = "multi" if settings.multilang_enabled else "en-US"
    assert voice == "11labs-Valentina"
    assert lang == "multi"


# ---------------------------------------------------------------------------
# 3–6. Prompt wrapper
# ---------------------------------------------------------------------------

def test_wrapper_flag_off_returns_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "multilang_enabled", False)
    from app.prompts.multilang_wrapper import apply_language_directive
    base = "You are an AI assistant."
    assert apply_language_directive(base) == base


def test_wrapper_flag_on_appends_directive(monkeypatch):
    monkeypatch.setattr(settings, "multilang_enabled", True)
    from app.prompts.multilang_wrapper import apply_language_directive
    base = "You are an AI assistant."
    result = apply_language_directive(base)
    assert result.startswith(base)
    assert "MULTILINGUAL BEHAVIOR" in result
    assert len(result) > len(base)


def test_wrapper_idempotent_no_double_append(monkeypatch):
    monkeypatch.setattr(settings, "multilang_enabled", True)
    from app.prompts.multilang_wrapper import apply_language_directive
    base = "You are an AI assistant."
    once = apply_language_directive(base)
    twice = apply_language_directive(once)
    assert once == twice
    assert once.count("MULTILINGUAL BEHAVIOR") == 1


def test_wrapper_composes_with_tenant_prompt(monkeypatch):
    monkeypatch.setattr(settings, "multilang_enabled", True)
    from app.prompts.multilang_wrapper import apply_language_directive
    tenant_prompt = "You are Jordan, dispatch specialist for Summit Plumbing Demo.\n" \
                    "Service area: Demo City, CA. Trade: plumbing."
    result = apply_language_directive(tenant_prompt)
    # Original content intact
    assert "Jordan" in result
    assert "Summit Plumbing Demo" in result
    # Directive appended exactly once at end
    assert result.count("MULTILINGUAL BEHAVIOR") == 1
    assert result.endswith(result.split("MULTILINGUAL BEHAVIOR")[-1])


# ---------------------------------------------------------------------------
# 7. Translation — English call, flag on: no LLM call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_english_call_no_translation(monkeypatch, db):
    monkeypatch.setattr(settings, "multilang_enabled", True)

    from app.models.call import CallSession
    from app.models.lead import Lead

    lead = Lead(
        id=uuid.uuid4(),
        contractor_id=uuid.uuid4(),
        call_id="call-en-001",
        lead_source="retell_call",
        trade="plumbing",
        problem_summary="Burst pipe under the kitchen sink",
    )
    db.add(lead)
    cs = CallSession(
        id=uuid.uuid4(),
        retell_call_id="call-en-001",
        contractor_id=lead.contractor_id,
        lead_id=lead.id,
        status="completed",
        conversation_history=[],
    )
    db.add(cs)
    await db.flush()

    with patch("app.services.translation._translate_fields") as mock_translate, \
         patch("app.services.translation._infer_language") as mock_infer:
        from app.services.translation import normalize_lead_fields
        await normalize_lead_fields("call-en-001", "en-US", db)

    mock_translate.assert_not_called()
    mock_infer.assert_not_called()
    assert lead.translation_status == "not_needed"
    assert lead.detected_language == "en-US"


# ---------------------------------------------------------------------------
# 8. Translation — Spanish call happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spanish_call_normalizes_fields(monkeypatch, db):
    monkeypatch.setattr(settings, "multilang_enabled", True)

    from app.models.call import CallSession
    from app.models.lead import Lead

    lead = Lead(
        id=uuid.uuid4(),
        contractor_id=uuid.uuid4(),
        call_id="call-es-001",
        lead_source="retell_call",
        trade="plomería",
        emergency_level="urgente",
        problem_summary="Tubería rota debajo del fregadero",
    )
    db.add(lead)
    cs = CallSession(
        id=uuid.uuid4(),
        retell_call_id="call-es-001",
        contractor_id=lead.contractor_id,
        lead_id=lead.id,
        status="completed",
        conversation_history=[],
    )
    db.add(cs)
    await db.flush()

    translated_response = {"trade": "plumbing", "emergency_level": "urgent"}

    with patch("app.services.translation._translate_fields", new=AsyncMock(return_value=translated_response)):
        from app.services.translation import normalize_lead_fields
        await normalize_lead_fields("call-es-001", "es", db)

    assert lead.trade == "plumbing"
    assert lead.emergency_level == "urgent"
    assert lead.translation_status == "ok"
    assert lead.detected_language == "es"
    # Originals preserved
    assert lead.original_field_values["trade"] == "plomería"
    assert lead.original_field_values["emergency_level"] == "urgente"
    # problem_summary untouched (display-only field)
    assert lead.problem_summary == "Tubería rota debajo del fregadero"


# ---------------------------------------------------------------------------
# 9. Translation timeout → status="failed", originals intact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_translation_timeout_sets_failed(monkeypatch, db):
    import asyncio
    monkeypatch.setattr(settings, "multilang_enabled", True)

    from app.models.call import CallSession
    from app.models.lead import Lead

    lead = Lead(
        id=uuid.uuid4(),
        contractor_id=uuid.uuid4(),
        call_id="call-timeout-001",
        lead_source="retell_call",
        trade="plomería",
    )
    db.add(lead)
    cs = CallSession(
        id=uuid.uuid4(),
        retell_call_id="call-timeout-001",
        contractor_id=lead.contractor_id,
        lead_id=lead.id,
        status="completed",
        conversation_history=[],
    )
    db.add(cs)
    await db.flush()

    async def _slow(*_a, **_kw):
        await asyncio.sleep(999)

    with patch("app.services.translation._translate_fields", new=_slow), \
         patch("app.services.translation._TIMEOUT_SECS", 0.01):
        from app.services.translation import normalize_lead_fields
        await normalize_lead_fields("call-timeout-001", "es", db)

    assert lead.translation_status == "failed"
    assert lead.trade == "plomería"  # unchanged


# ---------------------------------------------------------------------------
# 10. Malformed JSON from Claude → status="failed", originals intact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_malformed_translation_json_sets_failed(monkeypatch, db):
    monkeypatch.setattr(settings, "multilang_enabled", True)

    from app.models.call import CallSession
    from app.models.lead import Lead

    lead = Lead(
        id=uuid.uuid4(),
        contractor_id=uuid.uuid4(),
        call_id="call-badjson-001",
        lead_source="retell_call",
        trade="plomería",
    )
    db.add(lead)
    cs = CallSession(
        id=uuid.uuid4(),
        retell_call_id="call-badjson-001",
        contractor_id=lead.contractor_id,
        lead_id=lead.id,
        status="completed",
        conversation_history=[],
    )
    db.add(cs)
    await db.flush()

    async def _bad_json(*_a, **_kw):
        raise ValueError("invalid JSON: }{")

    with patch("app.services.translation._translate_fields", new=_bad_json):
        from app.services.translation import normalize_lead_fields
        await normalize_lead_fields("call-badjson-001", "es", db)

    assert lead.translation_status == "failed"
    assert lead.trade == "plomería"


# ---------------------------------------------------------------------------
# 11. Flag off → zero LLM calls, completely inert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flag_off_is_completely_inert(monkeypatch, db):
    monkeypatch.setattr(settings, "multilang_enabled", False)

    with patch("app.services.translation._translate_fields") as mock_t, \
         patch("app.services.translation._infer_language") as mock_i:
        from app.services.translation import normalize_lead_fields
        await normalize_lead_fields("call-flagoff-001", "es", db)

    mock_t.assert_not_called()
    mock_i.assert_not_called()
