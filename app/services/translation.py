"""
Post-call field normalization — Phase 3.

normalize_lead_fields(call_id, db)
  - Reads the lead linked to a completed call
  - Detects the call's language from Retell post-call payload (if available)
    or infers it from the problem_summary text
  - If MULTILANG_ENABLED=true AND language != English:
      - Translates key extracted fields to English via Claude
      - Preserves original values in original_field_values JSON
      - Writes translation_status = "ok" | "failed"
  - If language is English or flag is off: writes detected_language, sets
    translation_status = "not_needed", returns immediately (zero extra LLM calls)

Design invariants:
  - NEVER blocks a webhook 2xx — always called via asyncio.create_task()
  - NEVER raises — all paths wrapped in try/except
  - NEVER drops or overwrites original_field_values once written
  - Translation timeout: 15 seconds hard cap
"""
from __future__ import annotations

import json
import logging
import re
import asyncio
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# Fields to normalize into English — these are the ones consumed by
# downstream logic (dashboard grouping, quality metrics).
# All others (ai_summary, notes, problem_summary) are display-only — left as-is.
_NORMALIZE_FIELDS = ["trade", "emergency_level", "service_category"]

# Languages considered English (no translation needed)
_ENGLISH_CODES = {"en", "en-US", "en-GB", "en-AU", "en-CA"}

_TRANSLATION_PROMPT = """\
You are a data normalization assistant for a home-services contractor CRM.
The following JSON object contains values extracted from a phone call conducted in {language}.
Translate ONLY the values (not the keys) into English.
Return ONLY valid JSON with the exact same keys. No explanation, no code fences.
If a value is already in English or is null, return it unchanged.
"""

_INFER_LANGUAGE_PROMPT = """\
Detect the language of the following text and return a JSON object with one key "language"
whose value is a BCP-47 language code (e.g. "en", "es", "fr", "pt", "zh").
Return ONLY valid JSON. No explanation.

Text: {text}
"""

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_INFER_MODEL = "claude-3-5-haiku-20241022"
_TRANSLATE_MODEL = "claude-3-5-haiku-20241022"
_TIMEOUT_SECS = 15.0


async def normalize_lead_fields(
    call_id: str,
    retell_language: Optional[str],
    db: AsyncSession,
) -> None:
    """
    Entry point called via asyncio.create_task() from the retell webhook.
    Never raises.
    """
    if not settings.multilang_enabled:
        return  # flag off — zero behavioral change

    try:
        await _run(call_id, retell_language, db)
    except Exception as exc:
        logger.warning(
            "multilang: normalize_lead_fields unhandled error | call_id=%s error=%s",
            call_id, exc,
        )


async def _run(call_id: str, retell_language: Optional[str], db: AsyncSession) -> None:
    from app.models.call import CallSession
    from app.models.lead import Lead

    # 1. Find the call session and its linked lead
    cs_result = await db.execute(
        select(CallSession).where(CallSession.retell_call_id == call_id)
    )
    call_session = cs_result.scalar_one_or_none()
    if not call_session or not call_session.lead_id:
        logger.debug("multilang: no call session or lead for call_id=%s — skipping", call_id)
        return

    lead_result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
    lead = lead_result.scalar_one_or_none()
    if not lead:
        return

    # Idempotency — skip if already processed
    if lead.translation_status in ("ok", "failed"):
        return

    # 2. Determine the language
    lang = _clean_lang(retell_language)
    if not lang:
        # Retell didn't supply language — infer from problem_summary
        sample = lead.problem_summary or lead.notes or ""
        if sample.strip():
            lang = await _infer_language(sample)
        if not lang:
            lang = "en"  # can't determine → assume English

    lead.detected_language = lang

    # 3. English (or unknown) → mark not_needed, flush, done
    if lang in _ENGLISH_CODES or lang.startswith("en"):
        lead.translation_status = "not_needed"
        await db.flush()
        logger.debug("multilang: lang=%s — no translation needed | call_id=%s", lang, call_id)
        return

    # 4. Non-English → collect fields that have values
    fields_to_translate = {
        k: getattr(lead, k)
        for k in _NORMALIZE_FIELDS
        if getattr(lead, k, None)
    }
    if not fields_to_translate:
        lead.translation_status = "not_needed"
        await db.flush()
        return

    # 5. Preserve originals before overwriting
    if not lead.original_field_values:
        lead.original_field_values = fields_to_translate.copy()

    # 6. Translate
    try:
        translated = await asyncio.wait_for(
            _translate_fields(fields_to_translate, lang),
            timeout=_TIMEOUT_SECS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "multilang: translation timed out after %ds | call_id=%s lang=%s",
            _TIMEOUT_SECS, call_id, lang,
        )
        lead.translation_status = "failed"
        await db.flush()
        return
    except Exception as exc:
        logger.warning(
            "multilang: translation failed | call_id=%s lang=%s error=%s",
            call_id, lang, exc,
        )
        lead.translation_status = "failed"
        await db.flush()
        return

    # 7. Validate and write translated values
    try:
        for key, value in translated.items():
            if key in _NORMALIZE_FIELDS and hasattr(lead, key) and isinstance(value, str):
                setattr(lead, key, value)
        lead.translation_status = "ok"
        await db.flush()
        logger.info(
            "multilang: fields normalized | call_id=%s lang=%s fields=%s",
            call_id, lang, list(translated.keys()),
        )
    except Exception as exc:
        logger.warning(
            "multilang: DB write failed after translation | call_id=%s error=%s",
            call_id, exc,
        )
        lead.translation_status = "failed"
        await db.flush()


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

async def _translate_fields(fields: dict, language: str) -> dict:
    """Ask Claude to translate the field values to English. Returns parsed dict."""
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": _TRANSLATE_MODEL,
        "max_tokens": 256,
        "system": _TRANSLATION_PROMPT.format(language=language),
        "messages": [{"role": "user", "content": json.dumps(fields)}],
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECS) as client:
        response = await client.post(_ANTHROPIC_URL, headers=headers, json=body)
        response.raise_for_status()

    raw = response.json()["content"][0]["text"]
    parsed = _parse_json(raw)

    # Defensive: reject extra keys, ensure all returned keys were in input
    return {k: v for k, v in parsed.items() if k in fields}


async def _infer_language(text: str) -> Optional[str]:
    """Ask Claude to detect the language of a short text. Returns BCP-47 code or None."""
    try:
        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": _INFER_MODEL,
            "max_tokens": 32,
            "messages": [{
                "role": "user",
                "content": _INFER_LANGUAGE_PROMPT.format(text=text[:300]),
            }],
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(_ANTHROPIC_URL, headers=headers, json=body)
            response.raise_for_status()
        raw = response.json()["content"][0]["text"]
        parsed = _parse_json(raw)
        return _clean_lang(parsed.get("language"))
    except Exception as exc:
        logger.debug("multilang: language inference failed: %s", exc)
        return None


def _clean_lang(lang: Optional[str]) -> Optional[str]:
    """Normalise a language string: strip whitespace, lowercase base tag."""
    if not lang:
        return None
    lang = lang.strip()
    # Accept "en-US" → "en", "es" → "es", but keep full tag for matching
    return lang if lang else None


def _parse_json(text: str) -> dict:
    """Parse JSON from Claude response, stripping optional code fences."""
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(text.strip())
