# Phase 0 — Demo Number Swap: Read-only Findings

Date: 2026-10-01. Read-only: SELECT queries, Retell GET/list, Twilio GET, and code search only. No writes anywhere.

## Summary

| | Current demo number | New demo number |
|---|---|---|
| Number | `+17756183748` (775, Nevada) | `+15878001544` (587, Alberta) |
| DB owner today | Summit Plumbing Demo (`22cadf46-…7562`) | Renco Enterprise (`7af9c930-…6ede`) |
| Retell agent (DB) | `agent_ddb4b2b8…0647` "Summit Plumbing Demo — Alex" | `agent_7432c3df…5ec4` "Renco Enterprise — Alex" |
| Provisioned in | Retell (`retell-twilio`) | Retell (`retell-twilio`) |

**The swap is almost entirely a database change.** Both inbound routing and the live-call AI resolve the business from the dialed number (`to_number` → `contractors.phone_number`). Retell has no fixed agent bound to either number, so no Retell rebinding is required.

**Critical side finding:** outbound SMS is currently non-functional. `TWILIO_FROM_NUMBER=+15878001544` is a Retell-owned number that does not exist in TradeFlow's own Twilio account. The account has no numbers, no Messaging Services, and no messages ever sent.

## 1. Where the current demo number is defined

- Railway env `DEMO_PHONE_NUMBER=+17756183748`, read by `app/config.py:56` (`demo_phone_number`).
- Railway env `DEMO_CONTRACTOR_ID=22cadf46-67f8-4458-a557-fe9d47f27562`, read by `app/config.py:57`.
- Railway env `DEMO_AGENT_ID=agent_ddb4b2b81abad2a8b8f5c70647`. Nothing in `app/` reads this variable.
- DB `contractors.phone_number` of the Summit Plumbing Demo row.

## 2. Occurrences of both numbers in the repo

Searched with `git grep` for E.164, 10/11-digit, and `(xxx) xxx-xxxx`, `xxx-xxx-xxxx`, `xxx.xxx.xxxx`, `xxx xxx xxxx` forms:

| Number | Hits |
|---|---|
| `7756183748` (any format) | **none**. The value lives only in env and DB. |
| `5878001544` (any format) | `scripts/seed_renco_demo.py:15` (`RENCO_PHONE`, one-time seed script) |

How the demo number reaches users:
- `app/main.py:190` passes `settings.demo_phone_number` to `landing.html` as `demo_phone`.
- `app/templates/landing.html:151-153` and `:242-252` render a `tel:` link and the raw value. The page shows E.164 unformatted (`+17756183748`), which matches the earlier landing screenshot.
- `app/main.py:264-266` exposes `GET` `{"phone_number": settings.demo_phone_number}`.
- `app/routers/dashboard.py:631` shows it on the admin settings page.
- `app/main.py:74-75` logs a startup warning if it's unset.

Not found: email templates, SMS templates, `.env.example` value (placeholder only, line 61), tests, CI, JSON/YAML. No cold-outreach assets exist in this repo. Emails or texts already sent from other tools (for example the "Tradepal outreach" project) can't be searched from here.

## 3. Database

- Mapping table: `contractors.phone_number` (single column, `app/models/contractor.py`).
- **Constraint `contractors_phone_number_key UNIQUE (phone_number)`.** A swap must go through a temporary placeholder inside one transaction.

| id | name | phone_number | retell_agent_id |
|---|---|---|---|
| `22cadf46-…7562` | Summit Plumbing Demo | `+17756183748` | `agent_ddb4b2b8…0647` |
| `7af9c930-…6ede` | Renco Enterprise (email `rencoenterprise25@gmail.com`, 13 leads) | `+15878001544` | `agent_7432c3df…5ec4` |

Other active rows, for reference: TradeFlow Pro Demo `+15550001234`, CoolAir `+14035550999`, Fix-It Fast `+14035550101`, SparkRight `+14035550303`. None of these collide.

## 4. Retell (GET only)

| Number | Type | Nickname | Inbound | Agent binding |
|---|---|---|---|---|
| `+17756183748` | retell-twilio | "Summit Plumbing Demo — Alex" | webhook `https://api.tradesflowos.com/retell/inbound` | none (dynamic) |
| `+15878001544` | retell-twilio | "Renco Enterprise — Alex" | same webhook | none (dynamic) |

- Both agents use the same `custom-llm` WebSocket (`wss://api.tradesflowos.com/llm-websocket/{call_id}`), voice `11labs-Adrian`.
- The demo agent is `agent_ddb4b2b8…0647`. Renco's agent is `agent_7432c3df…5ec4`.
- The nicknames are cosmetic. They'd be misleading after the swap, so updating them is optional.

## 5. Telephony provider

- Voice: both numbers are Retell-managed (Retell's Twilio sub-account). Voice goes to Retell, then to the webhook above, which is TradeFlow's endpoint. `api.tradesflowos.com` resolves to Railway. No number-specific voice webhook lives on TradeFlow's side.
- SMS: Retell-managed numbers have **no SMS webhook** under TradeFlow's control. TradeFlow's own Twilio account (`TWILIO_ACCOUNT_SID`) holds **0 numbers and 0 Messaging Services, and has 0 messages in its history**.

## 6. Inbound routing logic

- **Call setup:** `app/routers/retell.py:376-447` (`retell_inbound`) looks up `Contractor.phone_number == to_number` and returns that contractor's `retell_agent_id`.
  - **Fallback (`:441-447`):** an unknown number routes to the *first active contractor with an agent*. That's arbitrary, and it's a misattribution risk if the DB and Retell ever disagree.
  - The endpoint is unauthenticated. A `POST {}` from outside returned 200 during discovery.
- **Live call / AI persona:** `app/routers/retell.py:126-129` uses `_get_contractor_by_phone(to_number)`.
- **Missed-call textback:** `app/routers/retell.py:494-511` looks up the contractor by `to_number`.
- **Inbound callback flows:** `app/routers/retell.py:611-613` uses `_get_contractor_by_phone`.
- **Demo behaviour** (cap, demo greeting, demo-call logging) is keyed on contractor **id**, not number: `app/services/demo.py:29-31` (`is_demo_call`), used at `retell.py:142-144`, `:181`, `:801-802`.
- **SMS replies:** `app/routers/twilio_sms.py` `_resolve_tenant_from_to` matches by number, then by last outbound text, then by last lead.

**Consequence:** if the DB rows are swapped, calls to 587 resolve to the Summit Plumbing Demo row. That gives the demo agent, demo persona, demo cap and demo logging, with no Retell change needed.

## 7. SMS and compliance

- The demo number itself (`+17756183748`) is **not used to send SMS**.
- `+15878001544` is the **global outbound SMS sender** for all tenants (`TWILIO_FROM_NUMBER`, used via `app/services/sms_provider.py`). It isn't in TradeFlow's Twilio account, so every send attempt is rejected by Twilio (inferred from 0 messages ever recorded). No missed-call textback, reminder, estimate or reactivation text has ever been delivered.
- Recipients: tenants are Alberta-based, so mostly Canadian recipients. US recipients would also be possible.
- Registration: none. There's no Twilio Messaging Service, no 10DLC, and Telnyx isn't set up yet.
- For `+15878001544` to send SMS, it would have to be SMS-enabled and owned by the sending account. A Retell-managed number can't be used from TradeFlow's Twilio account. Whether a Canadian long code texting **US** recipients is filtered without registration **needs verification with the provider.** Don't assume it's exempt.
- **The swap does not change SMS behaviour either way.** Fixing SMS is a separate decision: Telnyx or a Twilio number owned by your account. It should be a different number from the voice demo number.

## 8. Blast radius

| System | Change | Owner | Rollback |
|---|---|---|---|
| `contractors.phone_number` (2 rows) | swap values | DB | yes, reverse swap from the audit record |
| Railway `DEMO_PHONE_NUMBER` | `+17756183748` → `+15878001544` | Railway env | yes, set it back (triggers a redeploy) |
| Landing page, `/api` demo endpoint, admin page | show the new number automatically | code via env | yes, follows env |
| Retell numbers | none required; nicknames optional | Retell | yes |
| Retell agents | none | Retell | n/a |
| `TWILIO_FROM_NUMBER` | none in this task (already broken) | Railway env | n/a |
| Renco Enterprise callers | depends on D1 | DB / external | yes |
| Already-sent outreach containing 775 | can't change | external | no |
| `scripts/seed_renco_demo.py` | stale literal; update or annotate | code | yes |

## Decision D1

- **A — Full swap.** Renco Enterprise gets `+17756183748`. Both numbers stay live, nothing is lost, and it's one transaction. The downside: anyone calling the old demo number from old outreach reaches *Renco Enterprise's* receptionist instead of the demo.
- **B — Reassign only.** Renco gets no number. It currently has 13 leads and an agent, so it would stop receiving calls unless it has another number. Not recommended unless Renco is a test tenant you're retiring.
- **C — Park.** `+17756183748` stays on the demo tenant for a transition period, so both 775 and 587 reach the demo. Because `phone_number` is a single UNIQUE column, the demo row can hold only one number. C would need code support for a second (alias) number per tenant, or a routing override in `retell_inbound`. Renco would also end up with no number, as in B.

**Recommendation: A**, provided Renco Enterprise is your own business or test account. Its email `rencoenterprise25@gmail.com` and the "demo contractor" wording in `seed_renco_demo.py` suggest it is, and the landing page is the only in-repo place the old number appears. If 775 went out in cold outreach to real prospects, prefer **C**, with a small code change to support an alias number. **Please confirm what Renco Enterprise is, and whether 775 was used in outreach.**

## Adversarial self-checks (Phase 0)

1. **Stale binding.** Not applicable to Retell, since routing is fully dynamic from the DB. The real risk is the reverse: the DB swap *is* the routing change, so a half-applied DB change misroutes immediately. That's handled by the single transaction in Phase 2.
2. **Misattribution.** Leads, sessions and usage attach by contractor id, resolved from `to_number` at call time. Calls in progress during the swap keep their resolved tenant. Calls after it attribute correctly. The fallback at `retell.py:441` could misattribute if a number briefly matches no row. Inside one transaction no window exists, but the fallback itself is a latent risk worth flagging.
3. **Half-swap.** The UNIQUE constraint requires a placeholder. The whole swap must run in one transaction so the placeholder is never visible outside it.
4. **Missed literal.** No literals of 775 anywhere in the repo. The only 587 literal is in a one-time seed script. The landing page has no caching layer in-app. A CDN or browser cache may briefly show the old number.
5. **Dead outreach.** Under A, old 775 outreach reaches Renco rather than going dead. Under B, it reaches Renco until reassignment, then nothing.
6. **SMS filtering.** Moot today, since SMS is already non-functional (section 7). It needs its own fix.
7. **Emergency rule.** The 911 intercept lives in `app/services/triage.py` and the WebSocket path. It's agent- and number-independent and applies to every tenant including the demo, so the swap doesn't touch it.
