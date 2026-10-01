"""
Swap the demo phone number with another tenant's number (Decision D1 = A, full swap).

Dry-run by default; pass --apply to write. Example:

  SWAP_DATABASE_URL=<postgres public url> railway run --service tradeflow-api \\
    python3 scripts/swap_demo_number.py \\
      --demo-contractor-id <uuid> --other-contractor-id <uuid> \\
      --old-demo-number +1XXXXXXXXXX --new-demo-number +1XXXXXXXXXX [--apply]

Routing is resolved per call from contractors.phone_number (retell_inbound / llm-websocket),
so the DB swap is the routing change. Retell numbers must use the dynamic inbound webhook
(pre-flight aborts otherwise); only their cosmetic nicknames are updated. Idempotent.
DEMO_PHONE_NUMBER in Railway is changed separately (Phase 3).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from _demo_swap_common import (  # noqa: E402
    Abort, append_audit, check_retell_dynamic, connect, fetch_contractor, owners_of,
    require_e164, retell_numbers, retell_set_nickname, swap_numbers_in_txn,
)


async def main(args) -> int:
    old_n = require_e164("--old-demo-number", args.old_demo_number)
    new_n = require_e164("--new-demo-number", args.new_demo_number)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== swap_demo_number [{mode}]")

    conn = await connect()
    try:
        demo = await fetch_contractor(conn, args.demo_contractor_id)
        other = await fetch_contractor(conn, args.other_contractor_id)
        owners = await owners_of(conn, [old_n, new_n])
        print(f"   demo : {demo['name']:<24} phone={demo['phone_number']}  agent={demo['retell_agent_id']}")
        print(f"   other: {other['name']:<24} phone={other['phone_number']}  agent={other['retell_agent_id']}")

        already = demo["phone_number"] == new_n and other["phone_number"] == old_n
        expected = demo["phone_number"] == old_n and other["phone_number"] == new_n
        if already:
            print("   state: already swapped — nothing to do (idempotent)")
            return 0
        if not expected:
            raise Abort(f"pre-flight mismatch: expected demo={old_n} other={new_n}, found owners={owners}")
        if not (demo["is_active"] and other["is_active"] and demo["retell_agent_id"] and other["retell_agent_id"]):
            raise Abort("both contractors must be active and have a Retell agent")
        print("   pre-flight DB: OK (both rows match findings; UNIQUE swap via placeholder in one transaction)")

        numbers = retell_numbers()
        check_retell_dynamic(numbers, [old_n, new_n])
        print("   pre-flight Retell: OK (both numbers use the dynamic /retell/inbound webhook, no static agent)")

        demo_nick = f"{demo['name']} — demo line"
        other_nick = f"{other['name']} — test"
        plan = [
            f"DB  : {demo['name']}.phone_number {old_n} -> {new_n}",
            f"DB  : {other['name']}.phone_number {new_n} -> {old_n}",
            f"Retell nickname {new_n}: {numbers[new_n].get('nickname')!r} -> {demo_nick!r}",
            f"Retell nickname {old_n}: {numbers[old_n].get('nickname')!r} -> {other_nick!r}",
            "Provider webhooks: none (both numbers are Retell-managed; voice routes via /retell/inbound)",
        ]
        for line in plan:
            print("   plan: " + line)

        before = {
            "demo": {"id": demo["id"], "phone_number": demo["phone_number"]},
            "other": {"id": other["id"], "phone_number": other["phone_number"]},
            "nicknames": {old_n: numbers[old_n].get("nickname"), new_n: numbers[new_n].get("nickname")},
        }
        after = {
            "demo": {"id": demo["id"], "phone_number": new_n},
            "other": {"id": other["id"], "phone_number": old_n},
            "nicknames": {new_n: demo_nick, old_n: other_nick},
        }

        if not args.apply:
            print("   DRY-RUN: no changes made. Re-run with --apply to execute.")
            return 0

        await swap_numbers_in_txn(conn, demo["id"], new_n, other["id"], old_n)
        print("   DB: committed")
        nick_errors = []
        for number, nick in after["nicknames"].items():
            try:
                retell_set_nickname(number, nick)
            except Exception as exc:  # cosmetic only; routing already correct
                nick_errors.append(f"{number}: {exc}")
        print("   Retell nicknames: " + ("updated" if not nick_errors else f"WARN {nick_errors}"))
        append_audit({"action": "swap", "result": "applied", "before": before, "after": after,
                      "nickname_errors": nick_errors})
        print("   audit: appended to audit/demo_number_swap.jsonl")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--demo-contractor-id", required=True)
    p.add_argument("--other-contractor-id", required=True)
    p.add_argument("--old-demo-number", required=True)
    p.add_argument("--new-demo-number", required=True)
    p.add_argument("--apply", action="store_true", help="execute (default is dry-run)")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except Abort as exc:
        print(f"   ABORT: {exc}")
        sys.exit(2)
