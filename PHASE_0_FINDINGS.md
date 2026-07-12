# PHASE 0 FINDINGS — TradeFlow-OS Pro
## Read-Only Reconnaissance
*Commit: main (post Phase 4 analytics build)*

---

## 1. CALL PIPELINE MAP

```
PSTN caller dials contractor's Retell number
        │
        ▼
POST /retell/inbound                              retell.py:324
  DB lookup: Contractor WHERE phone_number = to_number AND is_active
  Returns {"agent_id": contractor.retell_agent_id}
  Fallback: first active agent if no match       retell.py:359–368
        │
        ▼  Retell opens WebSocket
WS  /llm-websocket/{call_id}                      retell.py:55
  Auth: Authorization: Bearer <retell_api_key>    retell.py:80–85
  Sends config {auto_reconnect, call_details}     retell.py:97–103
        │
        │  [interaction_type = call_details]
        ▼
  _get_contractor_by_phone(to_number)             retell.py:604
  CallSession INSERT + flush                      retell.py:131–139
  Demo daily cap check (is_demo_call)             retell.py:143–157
  BillingService.check_usage_limit("calls")       retell.py:160–177
  Per-plan max_call_mins stamped                  retell.py:180–186
  SMS consent recorded (sms_compliance)           retell.py:189–194
  ClaudeAgent(contractor, call_session, db)       retell.py:196–201
  broadcast_call_event("call_started")            retell.py:203–210
  agent.process_turn("__call_started__")          retell.py:213
    → build_system_prompt(contractor)             prompts/builder.py:6
    → _client.messages.create(model, tools, …)   claude_agent.py:84
    → first assistant text returned as greeting
  WS response_type="response" sent               retell.py:214–220
        │
        │  [interaction_type = response_required per utterance]
        ▼
  agent.process_turn(user_message)               retell.py:248
    Appends user message to history              claude_agent.py:50
    while iteration < MAX_TOOL_ITERATIONS(5):    claude_agent.py:53
      Claude API call (AsyncAnthropic)           claude_agent.py:84–91
      if tool_use blocks:
        execute_tool(name, input, context)       tools/handlers.py:22
          ├── check_availability                tools/check_availability.py
          ├── book_appointment                  tools/book_appointment.py
          │     └── asyncio.ensure_future(      book_appointment.py:91
          │           notify_appointment_booked) ← calls sync SMSService ⚠️
          ├── validate_service_area             tools/validate_address.py
          ├── send_sms                          tools/send_sms.py
          ├── create_lead_record                tools/create_lead.py
          │     └── asyncio.ensure_future(      create_lead.py:84
          │           notify_new_lead)           ← calls sync SMSService ⚠️
          └── transfer_call                     tools/transfer_call.py
                reads contractor.calendar_config["transfer_number"]
                calls queue_transfer(call_id, number) retell.py:541
    Conversation history → DB flush             claude_agent.py:78–79
  _pending_transfers.pop(call_id) checked       retell.py:251
  WS payload with optional transfer_number sent retell.py:254–265
        │
        │  [WebSocketDisconnect or end_call=True]
        ▼
  _finalise_session(call_id, call_info, db)     retell.py:637
    CallSession.status = "completed"
    duration_seconds stamped
    BillingService.increment_usage("calls")     retell.py:664
    Demo call logged if demo tenant             retell.py:667–673
    Lead: recording_url, transcript_url,
          raw_transcript from call_info         retell.py:680–686
    score_call() quality flags                  retell.py:689–692
    db.flush()
  broadcast_call_event("call_ended")            retell.py:697–701
        │
POST /retell/webhook  event=call_ended          retell.py:402–433
  _finalise_session() (idempotent)
  _schedule_post_call_jobs()                    retell.py:725
    schedule_appointment_reminder / review / followup
  If duration < 10s: asyncio.create_task(       retell.py:424
    send_missed_call_sms)  ← sync Twilio ⚠️
        │
POST /retell/webhook  event=call_analyzed       retell.py:435
  _apply_analysis() → Lead.customer_sentiment   retell.py:704
  PostCallAnalyser.analyse(transcript)          retell.py:447
    httpx POST to Anthropic (haiku model)       post_call.py:161
    Updates Lead.ai_summary, sentiment
    Optionally sends review request SMS         post_call.py:89–107
  normalize_lead_fields() translation pass      retell.py:453
```

---

## 2. RETELL INTEGRATION SURFACE

| Feature | Status | File:Line |
|---|---|---|
| Custom LLM WebSocket | ✅ FULL | retell.py:55 |
| Inbound routing webhook | ✅ FULL | retell.py:324 |
| Lifecycle webhooks (ended, analyzed) | ✅ FULL | retell.py:377 |
| Webhook HMAC-SHA256 signature verification | ✅ FULL | retell.py:557 |
| Heartbeat ping-pong | ✅ FULL | retell.py:114 |
| Auto-reconnect | ✅ FULL | retell.py:99 |
| Warm transfer (caller stays connected while human is rung) | ⚠️ PARTIAL | transfer_call.py:42 — `transfer_number` injected in WS response; Retell bridges the call. **No `transfer_no_answer` / `transfer_failed` webhook handler exists.** If tech doesn't answer, caller is silently disconnected. |
| update-live-call (mid-call context injection) | ❌ UNUSED | retell_client.py mentions endpoint; no call-path usage |
| Dynamic variables (retell_llm_dynamic_variables) | ⚠️ OUTBOUND ONLY | retell_client.py:64 |
| Retell function calling / custom tools | ❌ N/A | TradeFlow uses Custom LLM WS; Retell function calling is a Retell LLM feature only |
| Agent config / provisioning | ✅ FULL | provisioning.py:81–104 |
| Post-call analysis (Retell native sentiment) | ✅ USED | retell.py:436 → lead.customer_sentiment |
| Agent prompt config | ✅ FULL | prompts/builder.py:6, prompts/master_prompt.py |

---

## 3. TENANT MODEL

**Model:** `app/models/contractor.py:14` — `contractors` table

Key per-tenant business config fields:

| Field | Type | Used in call path |
|---|---|---|
| `name` | String | System prompt (COMPANY_NAME) |
| `agent_name` | String | System prompt (AGENT_NAME) |
| `trades` | JSON list | System prompt + Retell boosted_keywords |
| `service_areas` | JSON list | System prompt + validate_service_area |
| `timezone` | String | Scheduling only |
| `diagnostic_fee` | Float | System prompt clause |
| `free_estimate` | Bool | System prompt clause |
| `calendar_config` | JSON dict | `{"transfer_number": "+1..."}` only key used today |
| `sms_enabled` | Bool | Gates all outbound SMS |
| `plan` | String | Plan limits lookup |
| `retell_agent_id` | String | Inbound routing |
| `phone_number` | String | Inbound routing key |

**Per-tenant call scripting:** All tenants share one `MASTER_PROMPT_TEMPLATE` (master_prompt.py:1). Per-tenant variables interpolated at call start in `build_system_prompt()` (builder.py:34–43). **No per-tenant intake questions, urgency overrides, or emergency definitions exist.**

---

## 4. POST-CALL STRUCTURED DATA

Extracted by Claude's `create_lead_record` tool during the call, then enriched post-call.

**Extracted real-time (in-call, via Claude tool):**
- `caller_name`, `phone`, `email`, `service_address`, `city`, `province_state`, `postal_zip`
- `property_type` (residential/commercial)
- `trade`, `service_category`
- `problem_summary`
- `emergency_level` — **free-text string, no validated taxonomy** (lead.py:34)
- `life_safety_risk` — Boolean, **LLM-set only**, no code-level enforcement (lead.py:35)
- `appointment_status`, `appointment_time`
- `human_transfer_required`, `transfer_reason`
- `emergency_score` (1–10), `revenue_score` (1–10), `close_probability` (1–10)
- `priority_level` (Low/Medium/High/Critical)
- `customer_sentiment`, `notes`

**Extracted post-call (webhook-triggered):**
- `recording_url`, `transcript_url`, `raw_transcript` — from `call_ended` webhook
- `ai_summary`, `sentiment`, `follow_up_recommended` — from `PostCallAnalyser` (haiku model)
- `customer_sentiment` — also set by Retell's native analysis
- `call_quality_flags` — from `quality.py:score_call()`
- `detected_language`, `translation_status` — from `translation.py` (MULTILANG_ENABLED)

---

## 5. FLAG INFRASTRUCTURE

**Global flags** — `app/config.py:31`, `Settings(BaseSettings)`:
```python
# Feature flags (added during UI overhaul)
trust_v2: bool = False        # config.py:76
mobile_hero_v2: bool = False  # config.py:77
live_metrics: bool = False    # config.py:78
multilang_enabled: bool = False  # config.py:82
```
Pattern: pydantic-settings bool, default False, overridden by Railway env var.

**Per-tenant flags:** NONE exist today. The four new flags (`EMERGENCY_TRIAGE`, `LIVE_TRANSFER`, `FSM_SYNC`, `INTAKE_FLOWS_V2`) require:
1. Global `Settings` bool (feature enabled for any tenant)
2. New `Boolean` columns on `Contractor` model (tenant opted in)

---

## 6. CONCURRENCY REALITY

**Deployment:** Railway Nixpacks, no explicit `Procfile`. Likely single `uvicorn` worker. `asyncio`-based throughout.

**Async correctly used:**
- SQLAlchemy: `AsyncSession` everywhere ✅
- Claude API: `anthropic.AsyncAnthropic` ✅
- Retell REST: `httpx.AsyncClient` with timeouts ✅
- WebSocket: native FastAPI async ✅

### ⚠️ BLOCKING I/O — CRITICAL

**`SMSService._send()` — sms.py:42 is synchronous:**
```python
message = self._get_client().messages.create(**params)  # sync Twilio SDK
```
Blocks the uvicorn event loop for ~200–500ms per Twilio API call. Called from:
- `notify_appointment_booked()` via `asyncio.ensure_future()` — book_appointment.py:91
- `notify_new_lead()` via `asyncio.ensure_future()` — create_lead.py:84
- `send_missed_call_sms()` via `asyncio.create_task()` — retell.py:424
- `retell.py:517` — sync call directly in webhook handler (worst case)

**Effect:** 10 concurrent bookings firing SMS = 10 × 500ms of event loop blocking = potential 5s total, exceeding Retell's 5s ping-pong timeout → call drops.

**In-memory state:**
- `_active_agents: dict` — retell.py:44. Lost on restart; mitigated by `_rebuild_agent()`.
- `_pending_transfers: dict` — retell.py:48. Lost on restart with zero recovery path.

**No per-tenant Claude concurrency limit.** 500 simultaneous calls across tenants = 500 simultaneous `anthropic.AsyncAnthropic` calls. Anthropic rate limits could throttle/error.

---

## 7. EXISTING INTEGRATIONS

| System | Status |
|---|---|
| Retell AI | ✅ Production-ready |
| Twilio SMS | ✅ Wired (sync client is the bug) |
| Anthropic Claude | ✅ Production-ready |
| Google Calendar | ⚠️ Code exists (services/calendar.py:345 lines), not verified in production |
| Stripe | ⚠️ Code exists (services/billing.py), no live keys |
| Mailchimp | ⚠️ Code exists (services/mailchimp.py), no live keys |
| Jobber | ❌ Zero code |
| Housecall Pro | ❌ Zero code |
| ServiceTitan | ❌ Zero code |

---

## 8. RISKS

### 🔴 Critical (block live calls)

**R1 — Life-safety has no hardcoded intercept**
The "gas smell / CO / sparking" 911 redirect lives in master_prompt.py:92–113 as prompt text. A confused model response, tenant with customized prompt, or edge-case phrasing can miss it. Build spec requires a code-level intercept above LLM control that cannot be disabled by any flag or tenant setting.

**R2 — Sync Twilio SDK blocks the event loop**
sms.py:42 — `self._get_client().messages.create()`. Every SMS call during an active session competes with all concurrent WebSocket heartbeats and Claude API calls. Under load this causes Retell to detect a dead WebSocket and terminate calls.

**R3 — Transfer has no failure handling**
transfer_call.py:42–48 — if `transfer_number` is not configured, a warning is logged but the call continues with `transfer_queued=False`. The lead is already marked `transferred`. If the tech doesn't answer: no fallback, no owner notification, caller disconnected.

### 🟡 High (data integrity / reliability)

**R4 — `emergency_level` is free-text**
lead.py:34 — no enum, no validation. Downstream severity routing is impossible without a taxonomy.

**R5 — Duplicate `call_analyzed` webhook appends duplicate notes**
`_apply_analysis()` (retell.py:704) appends `"[Retell summary] ..."` to `Lead.notes` with no idempotency check. Retell guarantees at-least-once delivery.

**R6 — `_pending_transfers` in-memory**
retell.py:48 — process restart mid-call silently drops queued transfer.

**R7 — MAX_TOOL_ITERATIONS = 5 is tight**
claude_agent.py:16 — a booking flow can use: validate_service_area → check_availability → book_appointment → send_sms → create_lead_record = 5 tools exactly. Any retry or extra step silently caps.

### 🟢 Low

**R8 — No Anthropic rate limit handling**
`_call_claude()` (claude_agent.py:83) has no retry on 429/529. Under burst load, Claude API errors return degraded responses ("I'm experiencing a technical issue").

---

## SUMMARY

| Feature to Build | Gap |
|---|---|
| Emergency triage | Need: validated urgency taxonomy, hardcoded life-safety code intercept, per-tenant emergency definitions |
| Live transfer + fallback | Need: transfer failure webhook handler, fallback chain, on-call schedule model |
| FSM write-back | Need: full build from zero — adapter interface, Jobber + HCP adapters, OAuth, retry queue |
| Concurrency | Need: async Twilio client (httpx), Anthropic retry, remove in-memory transfer state |
| Trade-specific intake flows | Need: template data model, per-tenant selection, agent build integration |

---

`PHASE 0 COMPLETE — AWAITING: APPROVED: PHASE 0`
