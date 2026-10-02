"""
Weekly real-voice test. A Retell "test caller" agent phones a TradeFlow test tenant's AI line, books a
plumbing appointment, and this script verifies what actually happened in production: the call itself,
the conversation, and the lead it created. Costs ~35 cents and sends the test tenant's owner one text.

Environment (all required):
  RETELL_API_KEY        Retell API key
  TEST_CALLER_AGENT_ID  Retell agent that plays the customer
  FROM_NUMBER           Retell number the test caller dials from (a number that is NOT the target's)
  TO_NUMBER             the test tenant's AI line
  TARGET_AGENT_ID       the Retell agent that answers TO_NUMBER (for finding the inbound call)
  TENANT_ID             test tenant's contractor id
  TENANT_API_KEY        test tenant's TradeFlow API key (read-only use: lists its leads)
  BASE_URL              default https://tradesflowos.com

Exits non-zero if any check fails. Writes weekly-voice-report.json.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

RETELL = "https://api.retellai.com"
MAX_WAIT_SECONDS = 330
TENANT_TZ = "America/Edmonton"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"Missing required environment variable {name}")
    return value


def main() -> int:
    retell_key = env("RETELL_API_KEY")
    base_url = env("BASE_URL", "https://tradesflowos.com").rstrip("/")
    caller_agent, from_number, to_number = env("TEST_CALLER_AGENT_ID"), env("FROM_NUMBER"), env("TO_NUMBER")
    target_agent, tenant_id, tenant_key = env("TARGET_AGENT_ID"), env("TENANT_ID"), env("TENANT_API_KEY")
    rh = {"Authorization": f"Bearer {retell_key}"}

    checks: list[dict] = []
    notes: list[str] = []

    def check(name: str, ok: bool, detail: str = "", soft: bool = False) -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail, "soft": soft})
        mark = "PASS" if ok else ("WARN" if soft else "FAIL")
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail and not ok else ""))

    print("== weekly real-voice test")
    health = httpx.get(f"{base_url}/health", timeout=15).json()
    check("app healthy before the call", health.get("status") == "ok" and health.get("db") == "ok", str(health))

    started_ms = int(time.time() * 1000)
    started_iso = datetime.now(timezone.utc).isoformat()
    resp = httpx.post(f"{RETELL}/v2/create-phone-call", headers=rh, timeout=30, json={
        "from_number": from_number, "to_number": to_number, "override_agent_id": caller_agent,
        "metadata": {"call_type": "weekly_voice_test"},
    })
    if resp.status_code >= 300:
        check("test call placed", False, f"{resp.status_code} {resp.text[:200]}")
        return finish(checks, notes, None)
    caller_call_id = resp.json()["call_id"]
    print(f"  test call placed: {caller_call_id}")

    # Find the inbound leg (the tenant's side) and wait for it to finish.
    inbound = None
    deadline = time.time() + MAX_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(10)
        calls = httpx.post(f"{RETELL}/v2/list-calls", headers=rh, timeout=30,
                           json={"limit": 10, "sort_order": "descending"}).json()
        for c in calls:
            if (c.get("agent_id") == target_agent and (c.get("start_timestamp") or 0) >= started_ms - 10_000
                    and c.get("call_status") in ("ended", "error")):
                inbound = c
                break
        if inbound:
            break
    check("the call reached the tenant's AI and finished", inbound is not None,
          f"no finished inbound call within {MAX_WAIT_SECONDS}s")
    if not inbound:
        return finish(checks, notes, None)

    transcript = inbound.get("transcript") or ""
    agent_lines = [l[7:] for l in transcript.splitlines() if l.startswith("Agent:")]
    duration_s = int((inbound.get("duration_ms") or 0) / 1000)
    cost_cents = float((inbound.get("call_cost") or {}).get("combined_cost") or 0)
    reason = inbound.get("disconnection_reason") or ""
    notes.append(f"inbound call {inbound['call_id']}: {duration_s}s, {cost_cents:.0f} cents, ended by {reason}")

    check("call ended normally, not by an error", inbound.get("call_status") == "ended" and not reason.startswith("error"),
          f"status={inbound.get('call_status')} reason={reason}")
    check("AI greeted the caller", bool(agent_lines) and len(agent_lines[0].strip()) > 10)
    check("call length is sane (20-240s)", 20 <= duration_s <= 240, f"{duration_s}s")
    check("cost is sane (< 100 cents)", cost_cents < 100, f"{cost_cents:.0f} cents")
    spoken = " ".join(agent_lines)
    check("AI confirmed a booking out loud",
          bool(re.search(r"\b(confirmed|all set|booked|you're set)\b", spoken, re.I))
          and bool(re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|"
                             r"may|june|july|august|september|october|november|december)\b", spoken, re.I)))
    check("AI never talks after the goodbye ('standing by')",
          not re.search(r"standing by|next call|ready for the next", spoken, re.I))
    check("AI hung up by itself", reason == "agent_hangup", f"ended by {reason}", soft=True)
    check("no markdown or emoji spoken", not re.search(r"[*#`]|[\U0001F000-\U0001FAFF☀-➿]", spoken))

    # The lead, as the tenant's owner sees it.
    time.sleep(15)  # let call_ended / call_analyzed webhooks finish
    leads_resp = httpx.get(f"{base_url}/contractors/{tenant_id}/leads", timeout=30,
                           headers={"X-API-Key": tenant_key},
                           params={"date_from": (datetime.fromisoformat(started_iso) - timedelta(minutes=1)).isoformat(),
                                   "page_size": 50})
    check("tenant leads API reachable", leads_resp.status_code == 200, f"{leads_resp.status_code}")
    leads = [l for l in (leads_resp.json().get("leads", []) if leads_resp.status_code == 200 else [])
             if l.get("call_id") == inbound["call_id"]]
    check("exactly one lead was created for the call", len(leads) == 1, f"found {len(leads)}")
    if len(leads) == 1:
        lead = leads[0]
        check("lead is booked", lead.get("appointment_status") == "booked", str(lead.get("appointment_status")))
        check("lead has the caller's name", bool((lead.get("caller_name") or "").strip()))
        check("lead phone is E.164 (+1...)", re.fullmatch(r"\+1\d{10}", lead.get("phone") or "") is not None,
              str(lead.get("phone")))
        check("lead has a service address", bool((lead.get("service_address") or "").strip()))
        raw_time = lead.get("appointment_time")
        if raw_time:
            when = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            local = when.astimezone(ZoneInfo(TENANT_TZ))
            check("appointment is in the future", when > datetime.now(timezone.utc), raw_time)
            check("appointment is inside business hours in the tenant's timezone", 8 <= local.hour < 18,
                  f"{local:%Y-%m-%d %H:%M} {TENANT_TZ}")
            notes.append(f"appointment: {local:%A %b %d %I:%M %p} {TENANT_TZ}")
        else:
            check("appointment has a time", False, "appointment_time is empty")
        check("customer confirmation text was sent", bool(lead.get("sms_confirmation_sent")))
    return finish(checks, notes, inbound)


def finish(checks: list[dict], notes: list[str], inbound: dict | None) -> int:
    failures = [c for c in checks if not c["passed"] and not c["soft"]]
    warnings = [c for c in checks if not c["passed"] and c["soft"]]
    for n in notes:
        print("  note:", n)
    print(f"\n{len(checks) - len(failures) - len(warnings)}/{len(checks)} checks passed"
          f" ({len(warnings)} warning(s), {len(failures)} failure(s))")
    with open("weekly-voice-report.json", "w") as f:
        json.dump({"checks": checks, "notes": notes,
                   "transcript": (inbound or {}).get("transcript"),
                   "call_id": (inbound or {}).get("call_id")}, f, indent=2)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
