"""
Multilingual prompt wrapper — Phase 2.

apply_language_directive(base_prompt) appends a language-behavior directive
to an existing system prompt when MULTILANG_ENABLED=true.

With the flag off the function is a no-op: it returns base_prompt unchanged.
The directive is always appended (never prepended) so it cannot overwrite
existing instructions — it clarifies and extends them.
"""
from __future__ import annotations

_LANGUAGE_DIRECTIVE = """
---

## MULTILINGUAL BEHAVIOR (CRITICAL — READ LAST, HIGHEST PRIORITY)

You are a native-level multilingual receptionist. Greet callers in English by default.
If the caller speaks or responds in another language — most commonly Spanish or French —
switch to that language immediately and seamlessly. Conduct the entire remaining
conversation, including all questions, data collection, confirmations, and appointment
booking, in the caller's language, maintaining the same warm, professional tone and
following the same script structure defined above.

If you cannot confidently understand the caller's language, politely continue in English.
Never mix languages within a single sentence. All tool calls (create_lead_record,
book_appointment, etc.) must still use English field values regardless of the spoken
language — translate values to English before passing them to any tool.
"""


def apply_language_directive(base_prompt: str) -> str:
    """
    Return base_prompt with the multilingual directive appended when the flag is on.
    Returns base_prompt unchanged when MULTILANG_ENABLED=false.
    Never raises — on any error returns base_prompt unmodified.
    """
    try:
        from app.config import settings
        if not settings.multilang_enabled:
            return base_prompt
        # Guard: never append twice (idempotent — safe on WebSocket reconnects)
        if "MULTILINGUAL BEHAVIOR" in base_prompt:
            return base_prompt
        return base_prompt + _LANGUAGE_DIRECTIVE
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "multilang: apply_language_directive failed, using English-only prompt | error=%s", exc
        )
        return base_prompt
