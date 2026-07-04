# PHASE 0 FINDINGS — TradeFlow OS Pro Read-Only Audit

**Audit Date:** 2026-07-04  
**Scope:** Read-only. Zero changes made.  
**Status:** AWAITING APPROVAL before Phase 1

---

## Summary Table (Sorted by Severity)

| # | Finding | Severity | File:Line |
|---|---------|----------|-----------|
| 1 | No per-call minutes cap; Starter quota counts calls not minutes | CRITICAL | `provisioning.py:92`, `billing.py:124` |
| 2 | Outbound missed-call recovery bypasses usage check entirely | CRITICAL | `scheduler.py:61-91` |
| 3 | Stripe webhook signature verification skipped when secret not set | CRITICAL | `billing.py:141-144` |
| 4 | No A2P Messaging Service SID; raw long code for all US SMS | CRITICAL | `sms.py:36`, `config.py:18` |
| 5 | Monthly reset uses `updated_at` as proxy — fragile race condition | HIGH | `billing.py:150-158` |
| 6 | Retell `call_ended` webhook not idempotent; retry double-counts calls | HIGH | `retell.py:357`, `retell.py:587` |
| 7 | Missed-call webhook not idempotent; duplicate leads/SMS/calls on retry | HIGH | `retell.py:425-471` |
| 8 | No signature verification on `/retell/inbound` | HIGH | `retell.py:278-324` |
| 9 | WebSocket endpoint `/llm-websocket/{call_id}` has no authentication | HIGH | `retell.py:54-77` |
| 10 | `PostCallAnalyser` sends SMS without usage limit check | HIGH | `post_call.py:95-108` |
| 11 | Notification SMS to contractor bypasses usage counter | HIGH | `notifications.py:71-86, 206-219` |
| 12 | No opt-out database; scheduled jobs send SMS without consent check | HIGH | `scheduler.py:127-158, 186-213, 237-261` |
| 13 | No per-tenant Retell minutes or Twilio segment tracking | HIGH | `billing.py:145-164` |
| 14 | No Stripe event ID deduplication | MEDIUM | `billing.py:176-233` |
| 15 | SMS consent not recorded on Lead record | MEDIUM | `models/lead.py` |
| 16 | No inbound SMS webhook — STOP/HELP/customer replies silently dropped | MEDIUM | entire codebase |
| 17 | No failure flags for hang-up type, transfer failure, AI confusion | MEDIUM | `retell.py:251-253, 405-410` |
| 18 | No historical usage audit log (only live counters on contractor row) | MEDIUM | `billing.py:145-164` |
| 19 | `CallSession` has no `hangup_reason` or quality flag field | LOW | `models/call.py` |
| 20 | `_active_agents` in-memory dict lost on process restart | LOW | `retell.py:43` |

---

## 1. FREE-TIER COST EXPOSURE

### Plan Definitions — `app/config.py:3-7`
```
starter:    100 calls / 200 SMS
pro:        500 calls / 1000 SMS
enterprise: 9999 / 9999
```

### Where Counts Increment
- **Calls:** `app/routers/retell.py:587` inside `_finalise_session()` on `call_ended`
- **SMS (AI tool):** `app/tools/send_sms.py:70` after each send
- **SMS (missed-call):** `app/routers/retell.py:456`
- **SMS (scheduler follow-up):** `app/services/scheduler.py:322`

### Where Limit is Checked
- **Calls:** `app/routers/retell.py:132-148` — `check_usage_limit()` during WebSocket `call_details`. Hard stop via `end_call: True`.
- **SMS:** `app/tools/send_sms.py:33-35` — checked before AI tool SMS only.

### CRITICAL — No Minutes Cap
`app/services/provisioning.py:92` sets `max_call_duration_ms: 1800000` (30 min) at Retell agent creation. The quota counts *calls*, not *minutes*. A Starter tenant making 5 calls of 29 minutes each uses 5/100 of their call quota but consumes 145 minutes of Retell telephony cost. **No minutes budget exists.**

### CRITICAL — Outbound Recovery Calls Uncapped
`app/services/scheduler.py:61-91` — `_missed_call_recovery_job` fires an outbound Retell call with no usage check and no `calls_this_month` increment. Every missed call = unbounded Retell cost.

### HIGH — Monthly Reset Race Condition
`app/services/billing.py:150-158` — Resets counters when `updated_at.month != now.month`. `updated_at` is set by any field change (Stripe webhook, name change, etc.), not just usage changes. A Stripe webhook firing on month rollover can zero both counters mid-billing-period. No `billing_period_start` column exists.

### PostCallAnalyser SMS Bypass — `app/services/post_call.py:95-108`
`PostCallAnalyser.analyse()` calls `SMSService.send_review_request()` directly, bypassing `check_usage_limit()`. Second path to exceed SMS cap.

---

## 2. COST INSTRUMENTATION

### What IS tracked
- `calls_this_month` integer on contractor row
- `sms_this_month` integer on contractor row
- Incremented by `BillingService.increment_usage()` at `app/services/billing.py:145`

### What is NOT tracked (HIGH)
- **Retell minutes consumed** — no column, no event, no log
- **Twilio SMS segments** — counter increments by 1 per send regardless of message length; long messages split into multiple segments billed separately
- **Claude/Anthropic token cost** — `PostCallAnalyser` at `app/services/post_call.py:59` makes Anthropic API calls with no per-tenant accounting
- **Outbound recovery calls** — never counted against quota
- **Notification SMS to contractor** — `app/services/notifications.py:71-86` and `206-219` send SMS to the contractor's own number; `increment_usage()` never called

### No Audit Log (MEDIUM)
No structured usage event table exists. Usage is only reflected as live integers on the contractor row. No way to reconstruct historical usage for billing disputes.

---

## 3. A2P 10DLC STATUS

### CRITICAL — No Messaging Service SID
Every SMS is sent `from_=settings.twilio_from_number` (a raw long code):
- `app/services/sms.py:36`
- `app/services/missed_call.py:26`
- `app/services/notifications.py:83`
- `app/services/notifications.py:217`

US A2P 10DLC requires a registered Messaging Service SID. Sending from a raw long code without A2P registration causes carrier filtering and Twilio account suspension risk.

### HIGH — No In-App Opt-Out Database
All SMS templates include "Reply STOP to opt out" as plain text but STOP handling is delegated entirely to Twilio. No in-app opt-out list. Scheduled jobs (`_appointment_reminder_job`, `_review_request_job`, `_lead_followup_job`) send SMS without checking any consent state.

### MEDIUM — Consent Not Recorded
Caller-initiated contact establishes implied TCPA consent for transactional messages. However, `sms_consent_given` is never recorded. No timestamp, no source call ID, no audit trail.

### MEDIUM — No Inbound SMS Handler
No route for `/twilio/sms` or equivalent. STOP, HELP, UNSTOP, and customer replies are silently dropped by the application.

---

## 4. CALL-QUALITY OBSERVABILITY

### What IS Stored Per Call
- `CallSession`: `retell_call_id`, `conversation_history` (full JSON), `started_at`, `ended_at`, `duration_seconds`
- `Lead`: `recording_url`, `transcript_url`, `raw_transcript`, `ai_summary`, `sentiment`, `customer_sentiment`, lead scoring fields

### Failure Detection Gaps (MEDIUM)
- **No hangup_reason field** — `WebSocketDisconnect` marks session `completed` regardless of early hang-up or normal completion
- **No AI confusion flag** — `MAX_TOOL_ITERATIONS` guard logs warning but sets no DB flag (`claude_agent.py:16`)
- **Transfer failures** — only logged at INFO level (`retell.py:405-410`), no DB record
- **No quality flag** on `CallSession` to distinguish successful leads from abandoned calls

---

## 5. WEBHOOK RESILIENCE

### Retell `/retell/webhook` — `app/routers/retell.py:331-418`
- ✅ Signature verification present and correct
- ❌ **Not idempotent (HIGH)** — `call_ended` retry re-calls `_finalise_session()` and double-increments `calls_this_month`

### Retell `/retell/missed-call` — `app/routers/retell.py:425-471`
- ✅ Signature verification present
- ❌ **Not idempotent (HIGH)** — duplicate event creates second Lead, second SMS, second outbound call. `call_id` on Lead is indexed but not UNIQUE

### Retell `/retell/inbound` — `app/routers/retell.py:278-324`
- ❌ **No signature verification (HIGH)** — unauthenticated POST can obtain agent IDs or trigger fallback agent

### WebSocket `/llm-websocket/{call_id}` — `app/routers/retell.py:54-77`
- ❌ **No authentication (HIGH)** — any client can open a WebSocket, forge `call_details`, create a `CallSession`, and increment usage

### Stripe `/billing/webhook` — `app/routers/billing.py:132-153`
- ❌ **Signature skipped when secret empty (CRITICAL)** — default `stripe_webhook_secret=""` means anyone can POST forged events
- ❌ **No Stripe event ID deduplication (MEDIUM)**

---

## Phase 1 Readiness — Recommended Execution Order

1. Add `billing_period_start` column to contractors — fix reset race condition
2. Add `calls_this_month` increment + usage check to outbound recovery calls
3. Add minutes cap enforcement (configurable per plan)
4. Make PLAN_LIMITS data-driven (env-configurable)
5. Add idempotency keys to `call_ended` and `missed-call` webhook handlers
6. Route PostCallAnalyser SMS through usage check

**PHASE 0 COMPLETE. Awaiting your approval to proceed to Phase 1.**
