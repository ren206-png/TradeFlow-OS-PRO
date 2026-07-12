# PHASE 1 PLAN — TradeFlow-OS Pro: Competitive Parity Build
## Design Document (No Writes)

---

## FEATURE 1 — EMERGENCY TRIAGE

### 1.1 Urgency Taxonomy

Five levels, validated as an enum everywhere (DB, tool schema, prompt):

| Level | Code | Examples | Action |
|---|---|---|---|
| Life-safety | `life_safety` | Gas smell, CO alarm, sparking panel, flooding with trapped person | **Hardcoded 911/utility intercept — above LLM, cannot be disabled** |
| Emergency | `emergency` | Active water leak, no heat ≤ 0°C, no AC ≥ 40°C, power out affecting safety | Book-now slot or immediate transfer |
| Urgent | `urgent` | No hot water, HVAC failure (moderate weather), garage stuck, lockout | Same-day or next-day slot |
| Routine | `routine` | Intermittent issue, non-essential repair, maintenance request | Standard availability |
| Sales/Other | `sales_other` | Quote only, future project, wrong number | Lead record, no slot offered |

### 1.2 Hardcoded Life-Safety Intercept (non-negotiable, cannot be flagged off)

**Where:** New function `classify_life_safety(text: str) -> bool` in `app/services/triage.py`.

**Trigger keywords (regex, case-insensitive):**
```
gas smell | smell gas | gas leak | carbon monoxide | CO alarm | CO detector
sparking | spark(s)? | electrical fire | smoke from (outlet|panel|wire)
outlet (is )?on fire | panel (is )?smoking | wire(s)? (are )?burning
person trapped | trapped in garage | can't get out
```

**Where it fires:** `retell.py:248` — BEFORE `agent.process_turn(user_message)`.

```python
# Pseudocode — retell.py response_required handler
if classify_life_safety(user_message):
    await websocket.send_text(LIFE_SAFETY_RESPONSE)
    # Still create lead with life_safety_risk=True, then continue call
    # (do NOT end call — caller may need further help)
```

**`LIFE_SAFETY_RESPONSE`** (hardcoded constant, not in any prompt template):
```
"This sounds like an emergency. Please hang up right now and call 911 or your 
gas/utility emergency line. Do not stay in the building if you smell gas or 
see sparking. Once you and everyone else are safe, call us back and we will 
get someone out to you immediately."
```

This fires unconditionally regardless of `EMERGENCY_TRIAGE` flag, tenant config, or system prompt content. It is a safety rule, not a feature.

### 1.3 Per-Tenant Emergency Definitions

New JSON column on `Contractor`: `emergency_config: dict`

```json
{
  "emergency_thresholds": {
    "no_ac_temp_f": 95,
    "no_heat_temp_f": 32
  },
  "emergency_trades": ["plumbing", "hvac", "electrical"],
  "priority_sms_number": "+14035550001"
}
```

Default (if null): `no_ac_temp_f=95`, `no_heat_temp_f=32`, all trades.

The agent tool `classify_urgency` (new tool) accepts the weather context the caller provides and compares against the tenant's thresholds.

### 1.4 In-Conversation Classification

New Claude tool: `classify_urgency`

```json
{
  "name": "classify_urgency",
  "description": "Classify the urgency of this call based on what the caller described. Call this as soon as you understand the nature of the problem.",
  "input_schema": {
    "properties": {
      "urgency_level": {"type": "string", "enum": ["life_safety","emergency","urgent","routine","sales_other"]},
      "reason": {"type": "string"},
      "life_safety_risk": {"type": "boolean"}
    },
    "required": ["urgency_level", "reason"]
  }
}
```

Tool handler writes `Lead.emergency_level` (now validated enum) and `Lead.life_safety_risk`. Returns routing instruction to Claude: `{"action": "book_now"|"transfer"|"standard_flow"|"lead_only"}`.

### 1.5 Post-Classification Routing

| Urgency | Routing outcome |
|---|---|
| `life_safety` | Life-safety script fired (hardcoded), then `emergency` path |
| `emergency` | `check_availability(urgency="emergency")` → if no slot within 2h → `transfer_call(reason="emergency_dispatch")` → if transfer fails → take message + **priority SMS** to `emergency_config.priority_sms_number` |
| `urgent` | `check_availability(urgency="same_day")` |
| `routine` | Standard flow |
| `sales_other` | `create_lead_record` only, no availability check |

### 1.6 Schema Changes

- `Lead.emergency_level`: change from free-text `String` to `String` with application-level enum validation in the tool handler (DB stays String for backward compat, validated on write)
- `Contractor.emergency_config`: new `JSON` column, nullable, default null

### 1.7 SMS Format (urgency-tagged)

```
🔴 EMERGENCY — burst pipe, water ON — 123 Main St
Caller: Mike D. | +14035550100
Booked: Tue Jan 7, 9:00 AM  OR  ⚠️ Transfer failed — call back NOW
TradeFlow | reply STOP to opt out
```

Format: `{urgency_emoji} {URGENCY_LABEL} — {problem_summary} — {service_address}`

| Urgency | Emoji + Label |
|---|---|
| `life_safety` | 🚨 LIFE SAFETY |
| `emergency` | 🔴 EMERGENCY |
| `urgent` | 🟠 URGENT |
| `routine` | 🟢 Routine |

### 1.8 Feature Flag

- Global: `emergency_triage: bool = False` in `Settings` (config.py)
- Per-tenant: `Contractor.emergency_triage_enabled: bool = False`
- Flag-off path: `classify_urgency` tool not added to TRADEFLOW_TOOLS, life-safety intercept fires regardless

---

## FEATURE 2 — LIVE TRANSFER + HUMAN FALLBACK

### 2.1 Current State Gap

`transfer_call` tool (transfer_call.py:42) queues a number in `_pending_transfers`. Retell bridges the call. No handler for transfer failure. No on-call schedule. No fallback chain.

### 2.2 On-Call Schedule Model

New table: `on_call_schedules`

```python
class OnCallSchedule(Base):
    __tablename__ = "on_call_schedules"
    id: UUID primary key
    contractor_id: UUID FK → contractors
    label: String(100)          # e.g. "Weeknight", "Weekend"
    phone_number: String(30)    # number to ring
    days_of_week: JSON list     # [0,1,2,3,4] = Mon–Fri (0=Mon)
    start_time: String(5)       # "HH:MM" in contractor's timezone
    end_time: String(5)         # "HH:MM"
    is_active: bool default True
    created_at: DateTime
```

New service: `app/services/on_call.py`
- `get_active_on_call_number(contractor, now_utc) -> str | None` — queries schedule, converts to contractor timezone, returns active phone or falls back to `contractor.calendar_config["transfer_number"]`

Portal UI: simple CRUD table in `/portal/settings` under a new "On-Call Schedule" card (Phase 2).

### 2.3 Warm Transfer Flow

**Step 1:** `transfer_call` tool fires → `get_active_on_call_number()` → number queued in `_pending_transfers`.

**Step 2:** WebSocket handler injects `transfer_number` in response → Retell dials the on-call tech while keeping caller on hold (warm transfer — Retell keeps caller connected).

**Step 3:** Retell fires one of:
- `transfer_bridged` — tech answered, done ✅
- `transfer_cancelled` — caller hung up before answer
- `transfer_ended` — call ended after bridge
- *(no explicit "no answer" event — detected by `transfer_ended` with short duration or via timeout)*

### 2.4 Fallback Chain

New webhook handler for `transfer_ended` with duration-based no-answer detection:

```
transfer_ended received
    └── duration < 20s → assume no answer
          ├── Lead.transfer_status = "no_answer"
          ├── Retell: send "I wasn't able to reach our on-call tech" response
          │     → offer: leave message OR try again later
          ├── If caller leaves message:
          │     └── recording_url → owner SMS with 🚨 MISSED TRANSFER marker
          └── Owner SMS fires immediately:
                "🚨 MISSED TRANSFER — {caller_name} | {problem} | {number}
                 AI took message. Recording: {url}"
```

**Stuck-detection triggers** (AI → transfer path):
1. Caller says "human", "person", "real person", "agent", "speak to someone" (3× in transcript)
2. Same question answered differently 3× in a row (detected via tool `classify_stuck`, fires internally)
3. Call duration > 8 minutes with no booking (auto-escalate)

All three path through the same `transfer_call` tool with `reason="caller_requested"`.

### 2.5 Schema Changes

- New table: `on_call_schedules` (above)
- `Lead`: new field `transfer_status: String(30)` — values: `pending | bridged | no_answer | cancelled`
- `Contractor.calendar_config["transfer_number"]` remains as fallback when no schedule matches

### 2.6 Feature Flag

- Global: `live_transfer: bool = False` in `Settings`
- Per-tenant: `Contractor.live_transfer_enabled: bool = False`
- Flag-off: `transfer_call` tool keeps existing behavior (queues `calendar_config["transfer_number"]`, no schedule lookup, no fallback chain)

---

## FEATURE 3 — FSM WRITE-BACK (Jobber + Housecall Pro)

### 3.1 Real API Capabilities (from public docs)

#### Jobber (GraphQL API v2024-01)
- **OAuth 2.0:** Authorization Code flow. Scopes: `read_clients write_clients read_quotes write_quotes read_requests write_requests`
- **What exists:** `createRequest` mutation (service request, no tech assigned yet). `createClient` mutation.
- **What does NOT exist:** Direct job creation — jobs in Jobber are created from quotes or by converting a request. The correct object for a new inbound lead = **Request** (not Job).
- **Idempotency:** No native idempotency key. We embed our `lead_id` in the request `title` or `note`; before writing, query `requests(filter: {note_contains: lead_id})`. If found, skip.
- **Rate limits:** 10 requests/second, 500/minute per token.

#### Housecall Pro (REST API v1)
- **OAuth 2.0:** Authorization Code flow. Scopes: `jobs.read jobs.create customers.read customers.create`
- **What exists:** `POST /v1/jobs` — creates a job directly (no quote step needed). `POST /v1/customers`.
- **What does NOT exist:** Lead/request object. New calls map to **Job** with status `unscheduled`.
- **Idempotency:** Pass `X-Idempotency-Key: {lead_id}` header — HCP deduplicates on this key for 24h.
- **Rate limits:** 100 requests/minute.

### 3.2 Adapter Architecture

```
app/services/fsm/
├── __init__.py
├── base.py           # FSMAdapter abstract base class
├── jobber.py         # JobberAdapter(FSMAdapter)
├── housecall.py      # HousecallProAdapter(FSMAdapter)
├── registry.py       # get_adapter(contractor) → FSMAdapter | None
└── retry_queue.py    # FSMRetryQueue — persist and retry failed writes
```

**`FSMAdapter` interface (base.py):**
```python
class FSMAdapter(ABC):
    @abstractmethod
    async def create_or_update_customer(self, lead: Lead) -> str:
        """Returns vendor customer_id. Upserts on phone/email match."""

    @abstractmethod
    async def create_request_or_job(self, lead: Lead, customer_id: str) -> str:
        """Returns vendor job/request id."""

    @abstractmethod
    async def attach_note(self, job_id: str, note: str) -> None:
        """Attach call summary + recording link as a note."""

    @abstractmethod
    async def refresh_token_if_needed(self, contractor) -> None:
        """Check expiry; refresh OAuth token; save to DB."""
```

All vendor HTTP calls: `httpx.AsyncClient(timeout=15.0)`, 3 retries with exponential backoff (1s, 2s, 4s).

### 3.3 OAuth Flow Per Tenant

New table: `fsm_credentials`

```python
class FSMCredential(Base):
    __tablename__ = "fsm_credentials"
    id: UUID PK
    contractor_id: UUID FK → contractors
    vendor: String(30)          # "jobber" | "housecall_pro"
    access_token_enc: String    # encrypted at rest (Fernet, key from ENCRYPTION_KEY env)
    refresh_token_enc: String   # encrypted at rest
    token_expires_at: DateTime
    scope: String(512)
    vendor_account_id: String(128)  # Jobber account ID / HCP company ID
    connected_at: DateTime
    last_synced_at: DateTime nullable
    is_active: bool default True
```

**Encryption:** `cryptography.fernet.Fernet` with key from `ENCRYPTION_KEY` env var (new Railway secret). Token fields encrypted before INSERT, decrypted only when the adapter needs to call the vendor API. Never logged.

**Portal OAuth flow:**
1. `GET /portal/integrations/jobber/connect` → redirect to Jobber OAuth
2. `GET /portal/integrations/jobber/callback?code=...` → exchange code → encrypt → save `FSMCredential`
3. Token refresh: each adapter's `refresh_token_if_needed()` called before every API call; refreshed token re-encrypted and saved

### 3.4 Write-Back Flow (post-call)

Fires from `_schedule_post_call_jobs()` (retell.py:725) as a new job:

```
call_ended webhook
  └── _schedule_post_call_jobs()
        └── schedule_fsm_sync(lead_id, contractor_id)  [new, APScheduler]
              └── fsm_sync_job(lead_id, contractor_id)
                    ├── get_adapter(contractor)  → None if no credential / flag off
                    ├── adapter.refresh_token_if_needed()
                    ├── customer_id = adapter.create_or_update_customer(lead)
                    ├── job_id = adapter.create_request_or_job(lead, customer_id)
                    ├── adapter.attach_note(job_id, summary + recording_url)
                    ├── Lead.fsm_job_id = job_id
                    ├── Lead.fsm_synced_at = now
                    └── Lead.fsm_sync_status = "synced"
                    
              On any exception:
                    └── FSMRetryQueue.enqueue(lead_id, contractor_id, attempt=N)
                          Retry schedule: 5m, 15m, 1h, 4h, 24h (5 attempts)
                          After 5 failures: Lead.fsm_sync_status = "failed"
                                            alert email to contractor
```

### 3.5 Idempotency

- **Jobber:** Before `createRequest`, query `requests` filtered by note containing `tf:lead:{lead_id}`. If found, return existing id, skip write.
- **Housecall Pro:** Pass `X-Idempotency-Key: tf-{lead_id}` on all POST calls. HCP deduplicates for 24h.
- **Retry queue:** `FSMCredential` table has `last_synced_at` + `Lead.fsm_job_id`. If `fsm_job_id` is set, skip — already synced.

### 3.6 Retry Queue

New table: `fsm_retry_queue`

```python
class FSMRetryQueue(Base):
    __tablename__ = "fsm_retry_queue"
    id: UUID PK
    lead_id: UUID FK
    contractor_id: UUID FK
    vendor: String(30)
    attempt: Integer default 0
    next_retry_at: DateTime
    last_error: Text
    status: String(20)  # "pending" | "synced" | "failed"
    created_at: DateTime
```

APScheduler polls every 5 minutes for `status="pending" AND next_retry_at <= now`.

### 3.7 Schema Changes

- New table: `fsm_credentials`
- New table: `fsm_retry_queue`
- `Lead`: new fields: `fsm_job_id String(128) nullable`, `fsm_sync_status String(20) nullable`, `fsm_synced_at DateTime nullable`
- New Railway env: `ENCRYPTION_KEY` (Fernet key, 32 bytes base64-urlsafe), `JOBBER_CLIENT_ID`, `JOBBER_CLIENT_SECRET`, `HOUSECALL_PRO_CLIENT_ID`, `HOUSECALL_PRO_CLIENT_SECRET`

### 3.8 Feature Flag

- Global: `fsm_sync: bool = False` in `Settings`
- Per-tenant: `Contractor.fsm_sync_enabled: bool = False`
- Flag-off: `get_adapter()` returns None immediately, no DB writes, zero overhead

---

## FEATURE 4 — CONCURRENCY VERIFICATION

### 4.1 Blocking I/O Fixes (Phase 0 Risk R2)

**Fix: async Twilio SMS client**

Replace `SMSService._send()` (sms.py:42) sync `twilio.rest.Client` with `httpx.AsyncClient` posting directly to the Twilio Messages API:

```
POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json
Auth: Basic(SID, AUTH_TOKEN)
Body: form-encoded {To, From|MessagingServiceSid, Body}
```

This is byte-for-byte equivalent behaviour, fully async, no Twilio SDK needed in the hot path. Keep the sync client for non-call-path uses (scheduler jobs run in thread pool via APScheduler, so sync is fine there).

New method: `SMSService._send_async(to, body, message_type) -> dict` — used in all tools and webhook handlers. `_send()` (sync) kept for scheduler compatibility.

**Fix: `_send_email` in notifications (sms.py:19) — sync SMTP**

`notify_new_lead()` and `notify_appointment_booked()` call `_send_email()` which uses `smtplib` (blocking). These are already wrapped in `asyncio.ensure_future()` but the function is sync. Fix: wrap `_send_email` with `asyncio.get_event_loop().run_in_executor(None, ...)` in the async notification functions.

**Fix: `_pending_transfers` dict**

Keep in-memory for now (single worker on Railway). Add a `# FIXME: replace with Redis for multi-worker` comment. When Railway scales to multiple workers, this must move to Redis. Out of scope for this phase but document clearly.

### 4.2 Anthropic Rate Limit Handling

`claude_agent.py:84` — `_call_claude()` has no retry. Add:
- Catch `anthropic.RateLimitError` (429) and `anthropic.APIStatusError` (529)
- Exponential backoff: 3 retries, delays 1s / 2s / 4s
- On final failure: return graceful "I'm having trouble connecting" message, do NOT raise

### 4.3 Load Test Plan

**Tool:** `locust` (Python, async-compatible) or `pytest-asyncio` with `asyncio.gather()` for 500 concurrent webhook calls.

**Scenario A — 50 concurrent calls, single tenant:**
```
50 × simultaneous POST /retell/webhook {event: call_ended}
  Each triggers: _finalise_session + _schedule_post_call_jobs + SMS (async)
Pass criteria:
  - p95 response time < 500ms (well under Retell's webhook 10s timeout)
  - Zero DB constraint violations
  - Zero dropped bookings (all 50 leads in DB with status=completed)
```

**Scenario B — 500 concurrent calls, 10 tenants (50/tenant):**
```
Same as A, spread across 10 contractor IDs
Pass criteria: same as A
Additional: per-tenant call counters accurate (calls_this_month += 50 per tenant)
```

**Scenario C — WebSocket concurrency:**
```
50 × simultaneous WebSocket connections (using websockets library)
Each sends: call_details → 5× response_required → disconnect
Pass criteria:
  - All 50 ping-pong heartbeats answered within 5s
  - All 50 ClaudeAgent instances independent (no conversation bleed)
```

**Baseline (pre-fix):** Run Scenario A with sync Twilio SMS. Record p95. Confirm it degrades under load. Then fix and re-run. Show the delta.

---

## FEATURE 5 — TRADE-SPECIFIC INTAKE FLOWS

### 5.1 Template Data Model

Templates stored as DB rows, not prompt code. Non-engineers can edit via portal UI.

New table: `intake_templates`

```python
class IntakeTemplate(Base):
    __tablename__ = "intake_templates"
    id: UUID PK
    trade: String(50)           # "plumbing" | "hvac" | "electrical" | "general" | ...
    name: String(100)           # "Standard Plumbing Intake v1"
    version: Integer default 1
    is_active: bool default True
    is_system: bool default True    # True = TradeFlow default; False = tenant custom
    contractor_id: UUID nullable FK  # null = system template; set = tenant override
    questions: JSON             # ordered list (see schema below)
    created_at: DateTime
    updated_at: DateTime
```

**`questions` JSON schema:**
```json
[
  {
    "id": "water_active",
    "question": "Is water actively running or flowing right now?",
    "urgency_trigger": {"if_answer_contains": ["yes","yeah","it is"], "set_urgency": "emergency"},
    "required": true
  },
  {
    "id": "shutoff_located",
    "question": "Do you know where your main water shutoff valve is?",
    "urgency_trigger": null,
    "required": false
  }
]
```

### 5.2 System Templates (seed data)

**Plumbing:**
1. Is water actively running or flowing? (yes → `emergency`)
2. Do you know where your main shutoff is?
3. Is this hot water, cold water, or both?
4. Roughly how long has this been happening?

**HVAC:**
1. Is the issue heating, cooling, or both?
2. What's the temperature inside the building right now?
3. How old is the system approximately?
4. What does the thermostat display say?

**Electrical:**
1. Are there any sparks, burning smell, or flickering? (yes → `life_safety` if sparking/burning, `emergency` if flickering)
2. Is this a specific outlet/switch or multiple areas?
3. Has the breaker tripped? Did resetting it help?
4. Is power out completely or just partially?

**Garage Door:**
1. Is the door completely stuck, or partially open/closed?
2. Did you hear a loud bang before it stopped? (yes → broken spring, `urgent`)
3. Is a vehicle or person trapped? (yes → `life_safety`)

**General / Default:**
1. Can you describe what's happening?
2. How urgent is this — is it affecting your ability to use the space?

### 5.3 Integration into Agent Build

`build_system_prompt(contractor)` (prompts/builder.py:6) gains a new step:

```python
# When INTAKE_FLOWS_V2 flag is on for this tenant:
intake_section = await get_intake_section(contractor, db)
# Appended after Section 4 (Information Capture) in master prompt:
# "## SECTION 4B — TRADE-SPECIFIC INTAKE QUESTIONS
# For calls about {trade}, ask the following questions in order: ..."
```

New service: `app/services/intake.py`
- `get_active_template(contractor_id, trade, db) -> IntakeTemplate | None`
  - First: look for tenant override (`contractor_id=contractor.id, trade=trade`)
  - Fallback: system template (`is_system=True, trade=trade`)
  - Final fallback: general template
- `render_intake_section(template: IntakeTemplate) -> str` — formats questions as numbered prompt text

The `classify_urgency` tool (Feature 1) is informed by intake answers: if `water_active=yes` → tool should set `urgency=emergency` automatically.

### 5.4 Electrical Sparking → Life-Safety Path

This is where intake flows and the life-safety intercept intersect. If the intake question "are there sparks or burning smell?" is answered yes:

1. `classify_urgency` tool fires immediately with `urgency=life_safety`
2. The hardcoded intercept in retell.py fires the 911 instruction (above the LLM)
3. Call continues — agent stays on to take message and book for after the emergency is resolved

### 5.5 Feature Flag

- Global: `intake_flows_v2: bool = False` in `Settings`
- Per-tenant: `Contractor.intake_flows_v2_enabled: bool = False`
- Flag-off: `build_system_prompt()` skips the intake section entirely; behavior byte-identical to today

---

## MIGRATION & ROLLOUT PLAN

### DB Migrations (Alembic, in order)

```
0001_add_contractor_feature_flags.py
  ADD COLUMN emergency_triage_enabled BOOLEAN DEFAULT FALSE
  ADD COLUMN live_transfer_enabled BOOLEAN DEFAULT FALSE
  ADD COLUMN fsm_sync_enabled BOOLEAN DEFAULT FALSE
  ADD COLUMN intake_flows_v2_enabled BOOLEAN DEFAULT FALSE
  ADD COLUMN emergency_config JSON
  
0002_update_lead_emergency_level.py
  (no schema change — application-level validation only)
  ADD COLUMN transfer_status VARCHAR(30)
  ADD COLUMN fsm_job_id VARCHAR(128)
  ADD COLUMN fsm_sync_status VARCHAR(20)
  ADD COLUMN fsm_synced_at TIMESTAMPTZ

0003_create_on_call_schedules.py
  CREATE TABLE on_call_schedules (...)

0004_create_intake_templates.py
  CREATE TABLE intake_templates (...)
  INSERT system templates (seed data)

0005_create_fsm_credentials.py
  CREATE TABLE fsm_credentials (...)

0006_create_fsm_retry_queue.py
  CREATE TABLE fsm_retry_queue (...)
```

### Build Order (Phase 2)

1. **Concurrency fixes first** — async Twilio, Anthropic retry. No flags needed; improves stability for everyone immediately.
2. **Intake flows** — lowest risk; purely additive to prompt builder. Pilot: Renco Enterprise.
3. **Emergency triage** — life-safety intercept goes in unconditionally. Triage tool added behind `EMERGENCY_TRIAGE` flag.
4. **Live transfer** — on-call schedule model + fallback chain. Behind `LIVE_TRANSFER` flag.
5. **FSM adapters** — Jobber first (GraphQL, more complex), then Housecall Pro. Behind `FSM_SYNC` flag.

### Pilot Tenant List

| Phase | Tenant | Flags ON |
|---|---|---|
| Concurrency + intake | Renco Enterprise | `INTAKE_FLOWS_V2` |
| Emergency triage | Renco Enterprise + Summit Demo | `EMERGENCY_TRIAGE` |
| Live transfer | Renco Enterprise | `LIVE_TRANSFER` |
| FSM | New opt-in tenant with Jobber account | `FSM_SYNC` |

### New Railway Environment Variables

| Var | Purpose |
|---|---|
| `EMERGENCY_TRIAGE` | Global feature flag |
| `LIVE_TRANSFER` | Global feature flag |
| `FSM_SYNC` | Global feature flag |
| `INTAKE_FLOWS_V2` | Global feature flag |
| `ENCRYPTION_KEY` | Fernet key for FSM token encryption (generate: `Fernet.generate_key()`) |
| `JOBBER_CLIENT_ID` | Jobber OAuth app credentials |
| `JOBBER_CLIENT_SECRET` | Jobber OAuth app credentials |
| `HOUSECALL_PRO_CLIENT_ID` | HCP OAuth credentials |
| `HOUSECALL_PRO_CLIENT_SECRET` | HCP OAuth credentials |

---

## RISK REGISTER

| Risk | Mitigation |
|---|---|
| Life-safety intercept keyword misses novel phrasing | Regex is broad; PostCallAnalyser still runs; human fallback path always available |
| Jobber API changes break adapter | Adapter versioned; vendor API version pinned in URL (`v2024-01`) |
| FSM API down at booking time | Retry queue with 5 attempts over 24h; lead never lost in TradeFlow DB |
| Duplicate FSM records on retry | Idempotency: query-before-write (Jobber) + X-Idempotency-Key (HCP) |
| On-call schedule misconfigured (no match at midnight) | Fallback: `calendar_config["transfer_number"]`; if that's empty, AI takes message |
| Encryption key lost | `ENCRYPTION_KEY` must be in Railway secrets with backup; losing it invalidates all FSM tokens (re-OAuth required, data not lost) |
| Alembic migration fails on Railway | All migrations additive-only (ADD COLUMN, CREATE TABLE); safe to apply without downtime |

---

`PHASE 1 COMPLETE — AWAITING: APPROVED: PHASE 1`
