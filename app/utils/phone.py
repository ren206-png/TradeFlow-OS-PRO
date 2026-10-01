"""NANP phone helpers for numbers stored in E.164 (+1XXXXXXXXXX)."""
from __future__ import annotations

import re

NANP_E164 = re.compile(r"^\+1\d{10}$")


def is_nanp_e164(value: str) -> bool:
    return bool(NANP_E164.match(value or ""))


def format_display(e164: str) -> str:
    """'+15875550100' -> '(587) 555-0100'. Non-NANP input is returned unchanged."""
    if not is_nanp_e164(e164):
        return e164 or ""
    d = e164[2:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}"


def tel_href(e164: str) -> str:
    return f"tel:{e164}" if e164 else ""


def normalize_nanp(raw: str) -> str | None:
    """'(587) 555-0100', '5875550100', '+1 587 555 0100' -> '+15875550100'; None if not a NANP number."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return "+1" + digits
