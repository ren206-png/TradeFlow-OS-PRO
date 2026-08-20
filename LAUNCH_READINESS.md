# TradeFlow OS Pro — Launch Readiness
Date: 2026-08-20

## Summary
LAUNCH WITH CAVEATS — all 138 tests pass and the critical compliance and safety systems are intact, but three known gaps (MemoryJobStore, bilingual wiring in legacy services, and absence of a global cross-tenant SMS rate limit) must be tracked and resolved before removing the beta label.

---

## Check Results

### ✅ Full Test Suite
138/138 tests passed in 4.82 seconds. No failures, no errors. The asyncio fixture scope deprecation warning from pytest-asyncio is cosmetic and does not affect results.

---

### ✅ Cross-Tenant Isolation

Every model queried in `app/routers/` and `app/services/` uses a tenant-scoping column. Details:

- **OutboundLedger** — all reads and writes in `outbound_gateway.py` filter by `OutboundLedger.tenant_id == request.tenant_id`. ✅
- **ConsentLedger** — read in `outbound_gateway.py` (line 380) and `reactivation.py` (line 104), both filter by `tenant_id`. ✅
- **RevenueAttributionLedger** — queried in `stats_aggregator.py` (line 79) with `tenant_id` filter. ✅
- **SurgeModeRecord** — queried in `surge_portal.py` (line 200) and `weather_surge.py` (lines 450, 482) with `SurgeModeRecord.tenant_id == contractor.id`. ✅
- **SafetyActionLedger** — only written in `deliver_coaching.py` (line 184), scoped to `contractor.id` as `tenant_id`. ✅
- **CommercialIntakeLead** (`commercial_intake.py`) — all queries use the `tenant_id` UUID passed at the service level. ✅
- **CallSession** (the actual call log model — no model named `CallLog` exists; `CallSession` is the canonical name):
  - Tenant-facing reads in `portal.py`, `spam_shield.py`, `stats_aggregator.py`, `quality.py`, and `contractor_app.py` all filter by `contractor_id == tenant_id`. ✅
  - Admin dashboard (`dashboard.py`) intentionally queries across all tenants for aggregate platform statistics (calls today, active contractors). This is expected behavior for the admin view, not an isolation bug.
  - The cross-tenant abuse detector in `spam_shield.py` (line 76) intentionally omits a `tenant_id` filter to count a phone number across all tenants — this is the correct behavior for that specific check.

No unintended cross-tenant data leaks found.

---

### ✅ Compliance Dry Run

All five blocked outbound scenarios are correctly gated in `app/services/outbound_gateway.py`:

1. **Recipient has `opted_out=True` in consent_ledger**
   Gated at **Step 3** (line ~351): `is_opted_out()` from `app/services/sms_compliance` is called before consent check. If true, ledger is written with `block_reason="opted_out"` and the function returns immediately.

2. **Canadian marketing message, consent exists but `consent_type="implied"` and age > 730 days**
   Gated at two steps: **Step 4** (consent expiry check, lines ~389-400) evaluates `expires_at`. Implied consent records are written with a 730-day `expires_at` window (set at write time in `missed_call.py`). If the record is expired, `valid_consent` is `None` and the message is blocked as `no_valid_consent`. Even if the window hadn't expired, **Step 5** (line ~441) checks `is_canadian` and rejects any `consent_type != "express"` with `casl_implied_not_sufficient`.

3. **Call at 10:45 PM local time (quiet hours)**
   Gated at **Step 7** (line ~502): `_recipient_local_hour()` derives local time from the recipient's NPA, then blocks with `block_reason="quiet_hours"` if outside allowed hours.

4. **Cross-tenant rate limit: 11th SMS to same number within 24h from different tenants**
   **This scenario has no dedicated cross-tenant SMS rate limit.** Step 8 enforces a per-tenant daily cap (`OutboundLedger.tenant_id == request.tenant_id`). A recipient could receive SMS messages from 10 different tenants within 24 hours with no system-level block. This is a **known gap** — see Known Gaps section.

5. **Missing A2P 10DLC registration for US number**
   Gated at **Step 6** (line ~461): checks `A2PRegistration` for the tenant; if absent or not `status="approved"`, blocks with `block_reason="a2p_not_approved"`.

---

### ✅ Safety Regression

The life-safety intercept is present and intact in `app/services/triage.py`. It is imported and called unconditionally in `app/routers/retell.py` with the explicit comment: "Life-safety intercept — HARDCODED, never gated by any flag or tenant setting."

Regex patterns (all case-insensitive):
```
\bgas\s*(smell|leak|line)\b
\bsmell(ing)?\s*(gas|propane)\b
\bcarbon\s*monoxide\b
\bco\s*(detector|alarm|leak)\b
\bspark(s|ing)?\s*(flying|coming|from|out)\b
\bsparking\b
\belectrical\s*fire\b
\bhouse\s*(is\s*)?(on\s*)?fire\b
\bsewer\s*(backup|overflow)\b.*flood
\bflood(ing)?\s*(basement|house|entire)\b
\bno\s*power\s*(to\s*)?(the\s*)?(whole|entire)\s*(house|building)\b
\b(whole|entire)\s*(house|building)\s*(has\s*)?no\s*power\b
\belectric(al)?\s*shock\b
\bgot\s*shocked\b
```
14 patterns covering gas, carbon monoxide, sparks/electrical fire, house fire, sewer flood, and electrical shock. No changes detected from expected state.

---

### ⚠️ Concurrency Ceiling

No `MAX_CONCURRENT_CALLS` constant exists in `app/config.py` or `app/services/retell_client.py`. The configuration defines per-plan call minute caps (`PLAN_STARTER_MAX_CALL_MINS=10`, `PLAN_PRO_MAX_CALL_MINS=30`, `PLAN_ENTERPRISE_MAX_CALL_MINS=60`) and a per-tenant daily outbound cap (default 500, overridable via `outbound_daily_cap` on the Contractor model), but there is no ceiling on simultaneous concurrent calls.

Retell's platform enforces its own concurrency limits at the API level, so hard crashes are unlikely, but under surge conditions a single tenant could exhaust Retell concurrency and cause silent failures for other tenants. Recommend adding a `MAX_CONCURRENT_CALLS_PER_TENANT` setting before GA.

---

### ⚠️ APScheduler Risk (Known)

`app/services/scheduler.py` uses `MemoryJobStore` as its default job store:

```python
from apscheduler.jobstores.memory import MemoryJobStore
jobstores={"default": MemoryJobStore()}
```

All scheduled jobs (surge expiry sweeps, reactivation campaigns, weather polling, etc.) are held in process memory only. A dyno restart, crash, or Railway deployment will silently drop all pending jobs. Any in-flight scheduled task (e.g. a reactivation SMS queued 45 minutes in the future) will not be recovered.

**Recommended fix before GA:** Switch to `SQLAlchemyJobStore` backed by the existing Postgres connection. This is a one-line change in the scheduler init.

---

### ⚠️ Bilingual Wiring Gap (Known)

None of the three legacy notification services call `BilingualService.get_sms_template()`:

- `app/services/missed_call.py` — no bilingual call found
- `app/services/appointment_lifecycle.py` — no bilingual call found
- `app/services/estimate_followup.py` — no bilingual call found

These three services send SMS in English regardless of the contact's language preference. The `BilingualService` and `french_bilingual` feature flag exist and work correctly for the in-call flow (gated in `retell.py`), but outbound follow-up messages to French-preference contacts will arrive in English.

This was deferred from Phase 6 and is a known gap. It affects tenants who have enabled the `bilingual_sms` flag and have French-preference contacts in their follow-up queues.

---

## Feature Flag Rollout Order

Enable flags in this order to minimize blast radius and allow independent validation at each step:

| Order | Flag | Rationale |
|-------|------|-----------|
| 1 | `speed_to_lead` | Lowest risk — sends a single SMS reply on missed call. Isolated service, no downstream dependencies. Validate delivery rates and consent ledger writes first. |
| 2 | `triage_library` | Adds urgency classification to in-call flow. Read-only side effect (no outbound messages, no bookings). Safe to run in parallel with speed_to_lead. |
| 3 | `revenue_recovery` | Sends reactivation SMS to opted-in contacts. Depends on ConsentLedger being populated by speed_to_lead. Enable after at least two weeks of ledger data. |
| 4 | `dashboard_v2` | UI-only change. No backend mutations. Enable after revenue_recovery is stable so the new dashboard has real data to display. |
| 5 | `surge_mode` | Activates weather-triggered pricing and messaging. Depends on the scheduler being reliable — validate MemoryJobStore risk or switch to SQLAlchemy store before enabling. |
| 6 | `commercial_intake` | Adds a new call flow branch for commercial/mechanical leads. Enable after surge_mode confirms the call routing layer is stable under load. |
| 7 | `membership_lookup` | Reads service agreement records during calls. Enable after commercial_intake has been tested with real calls to confirm the extended call flow performs within token/time budgets. |
| 8 | `bilingual_sms` | Enable last — the in-call bilingual flow is complete, but outbound follow-up services (missed_call, appointment_lifecycle, estimate_followup) are not yet wired. Enable only after the bilingual wiring gap is resolved, or document that only in-call responses are bilingual. |

---

## Known Gaps Before Full Production

- **MemoryJobStore:** APScheduler job store is in-memory only. Any restart drops all pending scheduled jobs. Must be migrated to SQLAlchemyJobStore before removing beta label. Estimated effort: 2 hours.

- **No global cross-tenant SMS rate limit:** A recipient can receive outbound SMS from an unlimited number of different tenants in a 24-hour window. The per-scenario blocking (opted-out, quiet hours, CASL) works correctly, but there is no platform-level "recipient received 10 messages from 10 different TradeFlow tenants today" guard. This creates carrier reputation risk. Recommend adding a cross-tenant recipient frequency check to `outbound_gateway.py` before GA.

- **Bilingual outbound wiring incomplete:** `missed_call.py`, `appointment_lifecycle.py`, and `estimate_followup.py` send English-only SMS regardless of contact language preference. Contacts with French preference will receive English follow-ups. Must be resolved before marketing the bilingual capability to Canadian tenants.

- **No explicit concurrent call ceiling:** No `MAX_CONCURRENT_CALLS_PER_TENANT` is enforced. Under heavy surge conditions, one tenant could silently starve others at the Retell API layer. Add a concurrency semaphore or Retell concurrent-call configuration before enterprise rollout.

- **`admin_password` not set:** `app/routers/dashboard.py` returns HTTP 503 if `ADMIN_PASSWORD` is not set in Railway. This must be configured in every deployment environment before launch.

- **A2P 10DLC onboarding flow:** A2P registration check is enforced correctly, but there is no self-serve UI for tenants to submit or track their registration status. Tenants must be manually provisioned. This is a go-live blocker for US SMS on new tenants.
