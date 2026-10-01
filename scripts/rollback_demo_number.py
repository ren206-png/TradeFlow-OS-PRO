"""
Restore the exact pre-swap state recorded by the last applied swap in audit/demo_number_swap.jsonl.

Dry-run by default; pass --apply to write. Example:

  SWAP_DATABASE_URL=<postgres public url> railway run --service tradeflow-api \\
    python3 scripts/rollback_demo_number.py [--apply]

Aborts unless the live DB still matches that swap's "after" state (nothing else changed since).
Idempotent: if the DB already matches the "before" state, it does nothing.
Remember to also set DEMO_PHONE_NUMBER back in Railway.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from _demo_swap_common import (  # noqa: E402
    Abort, append_audit, connect, fetch_contractor, last_applied_swap, retell_set_nickname, swap_numbers_in_txn,
)


async def main(args) -> int:
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== rollback_demo_number [{mode}]")
    rec = last_applied_swap()
    if rec is None:
        print("   no applied swap in audit log — nothing to roll back")
        return 0
    if rec["action"] == "rollback":
        print(f"   last applied action is already a rollback ({rec['at']}) — nothing to do (idempotent)")
        return 0

    before, after = rec["before"], rec["after"]
    print(f"   target: swap applied at {rec['at']} by {rec['by']}")
    conn = await connect()
    try:
        demo = await fetch_contractor(conn, before["demo"]["id"])
        other = await fetch_contractor(conn, before["other"]["id"])
        live = (demo["phone_number"], other["phone_number"])
        if live == (before["demo"]["phone_number"], before["other"]["phone_number"]):
            print("   state: already at pre-swap values — nothing to do (idempotent)")
            return 0
        if live != (after["demo"]["phone_number"], after["other"]["phone_number"]):
            raise Abort(f"live state {live} does not match the swap's 'after' state — refusing to guess")
        print("   pre-flight DB: OK (live state matches the recorded post-swap state)")

        print(f"   plan: DB  : {demo['name']}.phone_number {demo['phone_number']} -> {before['demo']['phone_number']}")
        print(f"   plan: DB  : {other['name']}.phone_number {other['phone_number']} -> {before['other']['phone_number']}")
        for number, nick in before["nicknames"].items():
            print(f"   plan: Retell nickname {number} -> {nick!r}")

        if not args.apply:
            print("   DRY-RUN: no changes made. Re-run with --apply to execute.")
            return 0

        await swap_numbers_in_txn(conn, demo["id"], before["demo"]["phone_number"],
                                  other["id"], before["other"]["phone_number"])
        print("   DB: committed")
        nick_errors = []
        for number, nick in before["nicknames"].items():
            try:
                retell_set_nickname(number, nick or "")
            except Exception as exc:
                nick_errors.append(f"{number}: {exc}")
        print("   Retell nicknames: " + ("restored" if not nick_errors else f"WARN {nick_errors}"))
        append_audit({"action": "rollback", "result": "applied", "before": after, "after": before,
                      "rolls_back": rec["at"], "nickname_errors": nick_errors})
        print("   audit: appended")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="execute (default is dry-run)")
    try:
        sys.exit(asyncio.run(main(p.parse_args())))
    except Abort as exc:
        print(f"   ABORT: {exc}")
        sys.exit(2)
