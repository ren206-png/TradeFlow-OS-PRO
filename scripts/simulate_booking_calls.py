"""
Simulated booking calls: drive TradeFlow's real call agent (real Claude + real tools) with an
AI "customer", against a throwaway in-memory database. No phone calls, texts or emails leave
the process — SMS and email are captured and checked instead.

Run (uses the production ANTHROPIC_API_KEY; costs a few cents per run):

  railway run --service tradeflow-api env DATABASE_URL=sqlite+aiosqlite:// \\
      python3 scripts/simulate_booking_calls.py [--only standard_booking] [--report out.json]

Exits non-zero if any scenario fails.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Callable
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("DATABASE_URL", "").startswith("sqlite"):
    sys.exit("Refusing to run: set DATABASE_URL=sqlite+aiosqlite:// so nothing touches the real database.")

import anthropic  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.main  # noqa: E402,F401  (registers every model on Base.metadata)
from app.database import Base  # noqa: E402
from app.models.call import CallSession  # noqa: E402
from app.models.contractor import Contractor  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.routers.retell import _pending_transfers, _should_end_call  # noqa: E402
from app.services.claude_agent import ClaudeAgent  # noqa: E402
from app.services.triage import LIFE_SAFETY_RESPONSE, classify_life_safety  # noqa: E402

CALLER_MODEL = "claude-haiku-4-5-20251001"
MAX_TURNS = 16
OWNER_MOBILE = "+14035550188"
TRANSFER_TO = "+14035550177"
CALLER_PHONE = "+14035550142"


@dataclass
class Scenario:
    name: str
    persona: str
    checks: list[tuple[str, Callable[["Result"], bool]]]


@dataclass
class Result:
    transcript: list[tuple[str, str]] = field(default_factory=list)
    leads: list = field(default_factory=list)
    sms: list[tuple[str, str]] = field(default_factory=list)
    emails: list[tuple[str, str]] = field(default_factory=list)
    transfer_to: str | None = None
    ended_by: str = ""
    empty_replies: int = 0

    @property
    def lead(self):
        return self.leads[0] if self.leads else None

    def sms_to(self, number: str, kind_word: str = "") -> bool:
        return any(to == number and kind_word.lower() in body.lower() for to, body in self.sms)


def _has(value) -> bool:
    return bool(value and str(value).strip())


SCENARIOS = [
    Scenario(
        "standard_booking",
        "You are Jordan Test. Your kitchen faucet has been dripping for two days; not an emergency. "
        f"Your phone is {CALLER_PHONE[2:5]}-{CALLER_PHONE[5:8]}-{CALLER_PHONE[8:]}. "
        "Your address is 123 Test Street NW, Calgary, postal code T2N 1A1, a house. "
        "You want the earliest available appointment and accept the first time offered. Answer what you're asked.",
        [
            ("lead saved with caller name", lambda r: r.lead is not None and _has(r.lead.caller_name)),
            ("lead has phone", lambda r: r.lead is not None and _has(r.lead.phone)),
            ("lead has service address", lambda r: r.lead is not None and _has(r.lead.service_address)),
            ("appointment booked", lambda r: r.lead is not None and r.lead.appointment_status == "booked"),
            ("owner got new-lead or booking text", lambda r: r.sms_to(OWNER_MOBILE)),
            ("no text sent to the AI line", lambda r: not r.sms_to("+15875550100")),
        ],
    ),
    Scenario(
        "emergency_burst_pipe",
        "You are Sam Test. A pipe burst in your basement and water is pouring out right now; you're panicking. "
        f"Phone {CALLER_PHONE[2:5]}-{CALLER_PHONE[5:8]}-{CALLER_PHONE[8:]}. Address 45 Flood Ave SW, Calgary, T2N 1A1. "
        "You want someone immediately. If they offer to connect you to someone, say yes.",
        [
            ("lead saved", lambda r: r.lead is not None),
            ("marked urgent/emergency", lambda r: r.lead is not None and (
                str(r.lead.priority_level or "").lower() in {"high", "urgent", "emergency", "critical"}
                or str(r.lead.emergency_level or "").lower() not in {"", "none", "low", "standard"}
                or r.transfer_to is not None)),
            ("owner alerted or call transferred", lambda r: r.sms_to(OWNER_MOBILE) or r.transfer_to == TRANSFER_TO),
        ],
    ),
    Scenario(
        "no_address_callback",
        "You are Pat Test. Your toilet keeps running. You do NOT want to give your address on the phone and "
        f"refuse politely if asked; you just want someone to call you back at {CALLER_PHONE[2:5]}-{CALLER_PHONE[5:8]}-{CALLER_PHONE[8:]}.",
        [
            ("lead saved with phone", lambda r: r.lead is not None and _has(r.lead.phone)),
            ("not booked without an address", lambda r: r.lead is None or r.lead.appointment_status != "booked"),
            ("owner alerted", lambda r: r.sms_to(OWNER_MOBILE)),
        ],
    ),
    Scenario(
        "wants_a_human",
        "You are Lee Test calling about a water heater quote. You live at 9 Oak Road NW, Calgary, T2N 1A1. "
        "First ask whether you are talking to a real person or an automated system. Then insist on speaking to a real "
        "person and keep asking until they transfer you.",
        [
            ("transfer to the configured number", lambda r: r.transfer_to == TRANSFER_TO),
            ("admits being an AI when asked", lambda r: any(
                ("ai" in t.lower().replace(",", " ").split() or "automated" in t.lower() or "virtual" in t.lower())
                for who, t in r.transcript if who == "agent")),
        ],
    ),
    Scenario(
        "gas_smell_safety",
        "You are Robin Test. Open with: 'I smell gas in my kitchen, it's really strong.' Then follow instructions.",
        [
            ("911 safety response given", lambda r: any(LIFE_SAFETY_RESPONSE[:40] in text for who, text in r.transcript if who == "agent")),
            ("call ended immediately", lambda r: r.ended_by == "safety"),
        ],
    ),
]


VOICE_CHECKS = [
    ("no empty replies (dead air)", lambda r: r.empty_replies == 0),
    ("no markdown/emoji in speech", lambda r: not any(
        any(ch in text for ch in "*#`") or any(ord(ch) > 0x2600 for ch in text)
        for who, text in r.transcript if who == "agent")),
    ("customer phone stored in +1 format", lambda r: r.lead is None or not r.lead.phone or str(r.lead.phone).startswith("+1")),
    ("customer texts use +1 numbers", lambda r: all(to.startswith("+1") for to, _ in r.sms)),
    ("at most one booking confirmation", lambda r: sum("confirmed for" in b for _, b in r.sms) <= 1),
    ("confirmation text has a date and time", lambda r: all(" for  at " not in b for _, b in r.sms)),
]
for _sc in SCENARIOS:
    if _sc.name != "gas_smell_safety":
        _sc.checks = _sc.checks + VOICE_CHECKS


async def _caller_reply(client: anthropic.AsyncAnthropic, persona: str, transcript: list[tuple[str, str]]) -> str:
    messages = []
    for who, text in transcript:
        role = "user" if who == "agent" else "assistant"
        text = text.strip() or "(silence)"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + text
        else:
            messages.append({"role": role, "content": text})
    resp = await client.messages.create(
        model=CALLER_MODEL,
        max_tokens=150,
        system=(
            "You are a customer on a phone call with a home-services company's receptionist. "
            f"{persona} Speak like a real caller: one or two short sentences, no stage directions. "
            "When the receptionist has wrapped up (said goodbye, confirmed the booking, or transferred you), "
            "reply with exactly [HANGUP]."
        ),
        messages=messages,
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def run_scenario(sc: Scenario, client: anthropic.AsyncAnthropic) -> Result:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    res = Result()

    async def fake_send_sms(to, body, message_type="sms"):
        res.sms.append((to, body))
        return {"success": True, "sid": f"sim-{len(res.sms)}"}

    def fake_send_sms_sync(to, body, message_type="sms"):
        res.sms.append((to, body))
        return {"success": True, "sid": f"sim-{len(res.sms)}"}

    def fake_email(to, subject, html, text):
        res.emails.append((to, subject))
        return True

    async with Session() as db:
        contractor = Contractor(
            id=uuid.uuid4(), name="Summit Plumbing Sim", agent_name="Jordan",
            phone_number="+15875550100", owner_phone=OWNER_MOBILE, email="owner@example.com",
            api_key=uuid.uuid4().hex, trades=["plumbing"], service_areas=["Calgary", "T2N", "T2P"],
            timezone="America/Edmonton", diagnostic_fee=99.0, free_estimate=False,
            calendar_provider="manual", calendar_config={"transfer_number": TRANSFER_TO},
            sms_enabled=True, is_active=True, plan="pro",
        )
        call_id = f"call_sim_{uuid.uuid4().hex[:12]}"
        session = CallSession(retell_call_id=call_id, contractor_id=contractor.id, status="active",
                              conversation_history=[])
        db.add_all([contractor, session])
        await db.commit()

        with patch("app.services.sms_provider.send_sms", fake_send_sms), \
             patch("app.services.sms.send_sms", fake_send_sms), \
             patch("app.services.sms.send_sms_sync", fake_send_sms_sync), \
             patch("app.services.notifications._send_email", fake_email), \
             patch("app.services.scheduler.schedule_lead_followup", lambda *a, **k: None), \
             patch("app.services.scheduler.schedule_appointment_reminder", lambda *a, **k: None, create=True), \
             patch("app.services.scheduler.schedule_review_request", lambda *a, **k: None, create=True):
            agent = ClaudeAgent(contractor=contractor, call_session=session, db=db)
            agent._tool_context["caller_phone"] = CALLER_PHONE  # caller ID, as on a real call
            await agent.initialise_async_prompt()
            res.transcript.append(("agent", await agent.process_turn("__call_started__")))

            for _ in range(MAX_TURNS):
                said = await _caller_reply(client, sc.persona, res.transcript)
                if not said or "[HANGUP]" in said:
                    res.ended_by = "caller"
                    break
                res.transcript.append(("caller", said))
                if classify_life_safety(said):
                    res.transcript.append(("agent", LIFE_SAFETY_RESPONSE))
                    res.ended_by = "safety"
                    break
                reply = await agent.process_turn(said)
                if not reply.strip():
                    res.empty_replies += 1
                res.transcript.append(("agent", reply))
                transfer = _pending_transfers.pop(call_id, None)
                if transfer:
                    res.transfer_to = transfer
                    res.ended_by = "transfer"
                    break
                if _should_end_call(agent):
                    res.ended_by = "agent"
                    break
            else:
                res.ended_by = "max_turns"

            await db.commit()
            await asyncio.sleep(1.5)  # let fire-and-forget notifications finish
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if pending:
                await asyncio.wait(pending, timeout=10)

        res.leads = (await db.execute(select(Lead).where(Lead.contractor_id == contractor.id))).scalars().all()
    await engine.dispose()
    return res


async def main(args) -> int:
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    scenarios = [s for s in SCENARIOS if not args.only or s.name in args.only]
    report, failures = [], 0
    for sc in scenarios:
        print(f"\n=== {sc.name} ===")
        try:
            res = await run_scenario(sc, client)
        except Exception as exc:  # report and continue with the other scenarios
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            failures += len(sc.checks)
            report.append({"scenario": sc.name, "error": str(exc)})
            continue
        for who, text in res.transcript:
            print(f"  {who:>6}: {text}")
        print(f"  ended by: {res.ended_by} | transfer: {res.transfer_to} | texts: {len(res.sms)} | emails: {len(res.emails)} | empty replies: {res.empty_replies}")
        for to, body in res.sms:
            print(f"  sms -> {to}: {body[:70]!r}")
        if res.lead:
            l = res.lead
            print(f"  lead: name={l.caller_name!r} phone={l.phone!r} address={l.service_address!r} "
                  f"status={l.appointment_status!r} priority={l.priority_level!r} emergency={l.emergency_level!r}")
        checks = []
        for label, fn in sc.checks:
            ok = bool(fn(res))
            failures += not ok
            checks.append({"check": label, "passed": ok})
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        report.append({"scenario": sc.name, "ended_by": res.ended_by, "checks": checks,
                       "transcript": res.transcript, "sms": res.sms, "transfer_to": res.transfer_to})
    if args.report:
        with open(args.report, "w") as f:
            json.dump(report, f, indent=2, default=str)
    total = sum(len(s.checks) for s in scenarios)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="scenario names to run")
    ap.add_argument("--report", help="write a JSON report here")
    sys.exit(asyncio.run(main(ap.parse_args())))
