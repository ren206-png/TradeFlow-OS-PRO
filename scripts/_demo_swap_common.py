"""Shared helpers for swap_demo_number.py / rollback_demo_number.py. Dry-run unless --apply."""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import re
import socket
import uuid
from datetime import datetime, timezone

import asyncpg
import httpx

AUDIT_PATH = pathlib.Path(os.environ.get("SWAP_AUDIT_PATH")
                          or pathlib.Path(__file__).resolve().parents[1] / "audit" / "demo_number_swap.jsonl")
RETELL_BASE = "https://api.retellai.com"
E164 = re.compile(r"^\+1\d{10}$")


class Abort(Exception):
    pass


def require_e164(label: str, value: str) -> str:
    if not E164.match(value or ""):
        raise Abort(f"{label} must be E.164 NANP (+1XXXXXXXXXX), got {value!r}")
    return value


def db_url() -> str:
    url = os.environ.get("SWAP_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        raise Abort("Set SWAP_DATABASE_URL (Postgres public URL) or DATABASE_URL")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(db_url())


async def fetch_contractor(conn, contractor_id: str) -> dict:
    row = await conn.fetchrow(
        "SELECT id::text, name, phone_number, retell_agent_id, is_active FROM contractors WHERE id = $1::uuid",
        contractor_id,
    )
    if row is None:
        raise Abort(f"contractor {contractor_id} not found")
    return dict(row)


async def owners_of(conn, numbers: list[str]) -> dict[str, str]:
    rows = await conn.fetch("SELECT phone_number, id::text FROM contractors WHERE phone_number = ANY($1)", numbers)
    return {r["phone_number"]: r["id"] for r in rows}


async def swap_numbers_in_txn(conn, a_id: str, a_new: str, b_id: str, b_new: str) -> None:
    """Swap two contractors' phone_number values atomically despite UNIQUE(phone_number)."""
    placeholder = f"swap-tmp-{uuid.uuid4().hex[:12]}"
    async with conn.transaction():
        await conn.execute("UPDATE contractors SET phone_number = $1 WHERE id = $2::uuid", placeholder, a_id)
        await conn.execute("UPDATE contractors SET phone_number = $1 WHERE id = $2::uuid", b_new, b_id)
        await conn.execute("UPDATE contractors SET phone_number = $1 WHERE id = $2::uuid", a_new, a_id)
        leftover = await conn.fetchval("SELECT count(*) FROM contractors WHERE phone_number LIKE 'swap-tmp-%'")
        if leftover:
            raise Abort("placeholder still present inside transaction — rolling back")


def _retell_headers() -> dict:
    key = os.environ.get("RETELL_API_KEY", "")
    if not key:
        raise Abort("RETELL_API_KEY not set")
    return {"Authorization": f"Bearer {key}"}


def retell_numbers() -> dict[str, dict]:
    resp = httpx.get(f"{RETELL_BASE}/list-phone-numbers", headers=_retell_headers(), timeout=15)
    resp.raise_for_status()
    return {n["phone_number"]: n for n in resp.json()}


def retell_set_nickname(number: str, nickname: str) -> None:
    resp = httpx.patch(
        f"{RETELL_BASE}/update-phone-number/{number}",
        headers=_retell_headers(),
        json={"nickname": nickname},
        timeout=15,
    )
    resp.raise_for_status()


def check_retell_dynamic(numbers: dict[str, dict], wanted: list[str], webhook_suffix: str = "/retell/inbound") -> None:
    """Routing must come from our inbound webhook (DB lookup), not a static agent binding."""
    for n in wanted:
        obj = numbers.get(n)
        if obj is None:
            raise Abort(f"Retell has no phone number {n}")
        if obj.get("inbound_agent_id"):
            raise Abort(f"{n} has a static inbound_agent_id in Retell — DB swap alone would not reroute it")
        if not (obj.get("inbound_webhook_url") or "").endswith(webhook_suffix):
            raise Abort(f"{n} inbound webhook is {obj.get('inbound_webhook_url')!r}, expected …{webhook_suffix}")


def append_audit(record: dict) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now(tz=timezone.utc).isoformat(),
        "by": f"{getpass.getuser()}@{socket.gethostname()}",
        **record,
    }
    with AUDIT_PATH.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def last_applied_swap() -> dict | None:
    if not AUDIT_PATH.exists():
        return None
    applied = [json.loads(l) for l in AUDIT_PATH.read_text().splitlines() if l.strip()]
    applied = [r for r in applied if r.get("action") in ("swap", "rollback") and r.get("result") == "applied"]
    return applied[-1] if applied else None
