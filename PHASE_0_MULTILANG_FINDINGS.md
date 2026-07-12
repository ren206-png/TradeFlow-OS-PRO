# PHASE 0 FINDINGS — Multi-Language Support Audit
**Date:** 2026-07-04
**Status:** READ-ONLY — no code changes made in this phase

---

## 0.1 — Retell Integration Surface

### Agent configuration method
Agents are **fully API-managed at runtime** — not dashboard-managed. Every new contractor
gets a Retell agent created by calling `POST /create-agent` during signup provisioning.

- Agent creation: `app/services/provisioning.py:100` — `client.create_agent(agent_config)`
- Agent config dict assembled: `app/services/provisioning.py:81–97`
- Language set at line: `app/services/provisioning.py:88` — `"language": "en-US"`
- Voice set at line: `app/services/provisioning.py:87` — `"voice_id": DEFAULT_VOICE_ID`
- `DEFAULT_VOICE_ID = "11labs-Adrian"` at `app/services/provisioning.py:26`

### Retell SDK
There is **no Retell Python SDK installed**. The codebase uses a hand-rolled `RetellClient`
class (`app/services/retell_client.py`) built on `httpx`.

- `httpx==0.28.1` (in `requirements.txt`)
- No `retell-sdk` or `retellai` package in `requirements.txt`
- All API calls go directly to `https://api.retellai.com` with `Authorization: Bearer` header

This means there is no SDK version gap to worry about. The `language` field is whatever
string we put in the agent config dict passed to Retell's REST API.

### Language parameter
- Current value: `"en-US"` — hardcoded string in `app/services/provisioning.py:88`
- Retell's API supports `"multi"` for dynamic multilingual detection. Since we use raw HTTP
  (not a versioned SDK), availability depends only on the Retell account plan — not code.
- **OQ-1 (MUST answer before Phase 1):** Is `language: "multi"` enabled on the Retell
  account? Some plans require upgrading or contacting Retell support. Please verify in the
  Retell dashboard or with Retell support.

### Voice configuration
- Current voice: `"11labs-Adrian"` (ElevenLabs, via Retell)
- **OQ-2 (MUST answer before Phase 1):** Does `11labs-Adrian` support Spanish and French
  synthesis via Retell's multilingual mode? ElevenLabs voices vary — some are English-only.
  This cannot be determined from code alone. Please test or check the Retell voice library.
  If it does not support Spanish/French, specify the replacement voice ID to use.

### Agent update path
`app/services/retell_client.py` already has `update_agent(agent_id, agent_config)` at
line ~196 (`PUT /update-agent/{id}`) — can patch `language` and `voice_id` on existing
agents without reprovisioning.

---

## 0.2 — Prompt / Instruction Pipeline

### How the system prompt is built
- Template: `app/prompts/master_prompt.py` — `MASTER_PROMPT_TEMPLATE` (large string template)
- Builder: `app/prompts/builder.py:7` — `build_system_prompt(contractor: Contractor) -> str`
- Called from: `app/services/claude_agent.py:28` — `self.system_prompt = build_system_prompt(contractor)`
- The prompt is built **per call, in memory**. Not stored in DB. Not tenant-overridable
  beyond 6 template variables: `AGENT_NAME`, `COMPANY_NAME`, `SERVICE_AREA`,
  `SUPPORTED_TRADES`, `DIAGNOSTIC_FEE_CLAUSE`, `FREE_ESTIMATE_CLAUSE`, `REVIEW_LINK`.

### English-only conflict check
The master prompt contains NO explicit "respond in English only" directive. All example
scripts are in English (e.g. Section 1: `"{AGENT_NAME} with {COMPANY_NAME}, how can I help
you today?"`), but there is no language enforcement statement. No conflict found that would
override a multilingual directive — the wrapper in Phase 2 appends cleanly.

### Per-tenant prompt overrides
**None exist today.** `build_system_prompt()` is fully template-driven with no DB lookup
for per-tenant instructions. Phase 2's `apply_language_directive()` wrapper appends to
the output of `build_system_prompt()` and is correct for all tenants automatically.

---

## 0.3 — Post-Call Webhook & Data Extraction

### Webhook route
- File: `app/routers/retell.py`, route `POST /retell/webhook` at line 346
- The `call_analyzed` event branch at line 425 triggers `_apply_analysis()` (line 695)
  and `PostCallAnalyser.analyse()`.
- **Current pattern:** Heavy post-call work runs synchronously inside the route handler
  before the 2xx is returned. This is the existing pattern. Phase 3 will NOT change this —
  translation runs in `asyncio.create_task()` fired AFTER the existing synchronous work
  and BEFORE `return {"status": "ok"}` (or equivalently, after it via task queue).

### Structured data extraction — what fields exist and how they're set
TradeFlow-OS does **not** use Retell's `Job_Type`/`Customer_Issue`/`Urgency` post-call
analysis fields. Structured data is captured via two mechanisms:

1. **During the call (real-time):** Claude calls the `create_lead_record` tool
   (`app/tools/create_lead.py`), writing: `caller_name`, `phone`, `trade`, `problem_summary`,
   `service_address`, `emergency_level`, `appointment_status`, scores, etc.

2. **Post-call:** `PostCallAnalyser.analyse()` (`app/services/post_call.py`) calls Claude
   with the transcript and extracts: `summary`, `sentiment`, `follow_up_recommended`,
   `review_recommended`, `notes` → written to `leads.ai_summary`, `leads.sentiment`.
   Additionally, `_apply_analysis()` at `app/routers/retell.py:695` reads
   `call_analysis.call_summary` and `call_analysis.user_sentiment` from Retell's payload.

### Downstream consumers — non-English risk assessment
| Field | Consumer | Non-English Risk |
|-------|----------|-----------------|
| `trade` | Dashboard grouping (`app/routers/dashboard.py:102`) | **MEDIUM** — non-English value creates orphan row in chart |
| `problem_summary` | Dashboard display only | LOW — not parsed programmatically |
| `caller_name` | SMS messages (`app/services/sms.py`) | LOW — names pass through |
| `ai_summary` / `notes` | Dashboard display only | LOW |
| `appointment_status` | Scheduler logic, quality scoring | NONE — enum values set by tool code |
| `emergency_level` | Quality scoring display | LOW — display only |

**Primary normalization target for Phase 3: `trade` field.** It drives grouping in
`app/routers/dashboard.py:102–108`. All other fields are either display-only or use enums.

### Background mechanism
Two patterns already in codebase:
1. `asyncio.create_task()` — used at `app/routers/retell.py:415` and `app/tools/create_lead.py:57`
2. FastAPI `BackgroundTasks` — available but not yet used in the webhook route

**Recommendation:** Use `asyncio.create_task()` (matching existing patterns). Fire after
the `call_analyzed` processing block in `retell_webhook()` before returning `{"status":"ok"}`.

### Existing schema for language metadata
No `detected_language`, `translation_status`, or original-values column exists on any table.
The `leads` table has `raw_transcript: JSON` (nullable) and `call_quality_flags: JSON`
(nullable), neither of which is semantically suitable for multilingual state.

**Additive migration required (1, permitted):**
```sql
ALTER TABLE leads ADD COLUMN IF NOT EXISTS detected_language VARCHAR(8) NULL;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS translation_status VARCHAR(16) NULL;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS original_field_values JSON NULL;
```
All nullable, no defaults, zero impact on existing rows.

---

## 0.4 — Configuration & Flags

### Config loading
`app/config.py` — `pydantic_settings.BaseSettings` reads from `.env` and Railway env vars.
Adding `multilang_enabled: bool = False` to the `Settings` class is all that's needed.
The `False` default guarantees byte-identical behavior when the flag is not set.

### Logging conventions
All modules use `logger = logging.getLogger(__name__)` with key=value pipe-separated style:
```python
logger.warning("multilang: translation failed | call_id=%s error=%s", call_id, exc)
```
New warnings must follow this pattern.

---

## Proposed Implementation Plan (Phases 1–3)

### Files to CREATE
| File | Purpose |
|------|---------|
| `app/prompts/multilang_wrapper.py` | `apply_language_directive(base_prompt) -> str` |
| `app/services/translation.py` | `normalize_lead_fields(lead_id, db)` — async, Claude-backed |
| `tests/test_multilang_wrapper.py` | Phase 2 tests |
| `tests/test_translation.py` | Phase 3 tests |

### Files to TOUCH (exact insertion points)
| File | Line(s) | Change |
|------|---------|--------|
| `app/config.py` | After `demo_max_call_mins` | Add `multilang_enabled: bool = False` |
| `app/services/provisioning.py` | Line 88 | Gate `"language"` value on flag |
| `app/services/provisioning.py` | Line 26 | Introduce `MULTILANG_VOICE_ID` constant (value TBD by OQ-2) |
| `app/prompts/builder.py` | Return statement | Wrap with `apply_language_directive()` |
| `app/routers/retell.py` | Line ~425 `call_analyzed` branch | Fire `asyncio.create_task(normalize_lead_fields(...))` |
| `app/models/lead.py` | After `original_field_values` | Add 3 nullable columns |

### Files NOT touched
- `app/prompts/master_prompt.py` — never edited in place (additive only per constraint)
- `app/services/claude_agent.py` — prompt injection handled in `builder.py`
- `app/tools/` — no changes
- `app/services/post_call.py` — no changes; translation is a separate pass
- Any SMS template file — no changes
- No `MULTILANG_DASHBOARD_CHECKLIST.md` needed — agents are fully API-managed

---

## Open Questions (must be answered before Phase 1 begins)

**OQ-1** — Is `language: "multi"` enabled on the Retell account?

**OQ-2** — Does `11labs-Adrian` support Spanish/French via Retell's multilingual mode?
If not, what replacement voice ID should be used?

---

## Risk List

| Risk | Severity | Mitigation |
|------|----------|-----------|
| `language: "multi"` not available on current Retell plan | HIGH | Flag defaults `false`; OQ-1 blocks Phase 1 |
| `11labs-Adrian` English-only in multilingual mode | MEDIUM | OQ-2 blocks Phase 1; fallback is keep English voice |
| `trade` field receives non-English value, breaks dashboard grouping | MEDIUM | Phase 3 normalization targets `trade` specifically |
| Translation LLM delays webhook 2xx | HIGH | Uses `asyncio.create_task()` — never blocks response |
| Non-GSM-7 chars in SMS on translation failure | LOW | Per spec: acceptable; logged as warning |
| Task silently drops if event loop closes before completion | LOW | DB write of `translation_status="failed"` on any exception |
