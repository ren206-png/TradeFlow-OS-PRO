"""Contractor-local time helpers. Appointment instants are stored in UTC; people see local time."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Edmonton"

# Canadian area code -> IANA zone (covers the vast majority of subscribers; unknown codes use the default)
_AREA_CODE_TZ = {
    **{c: "America/Edmonton" for c in ("403", "587", "780", "825", "368")},                     # Alberta
    **{c: "America/Vancouver" for c in ("604", "778", "236", "672", "250")},                     # British Columbia
    **{c: "America/Regina" for c in ("306", "639", "474")},                                      # Saskatchewan
    **{c: "America/Winnipeg" for c in ("204", "431")},                                           # Manitoba
    **{c: "America/Toronto" for c in ("416", "647", "437", "905", "289", "365", "519", "226", "548",
                                      "705", "249", "613", "343", "807", "514", "438", "450", "579",
                                      "581", "819", "873", "418", "367")},                       # Ontario / Quebec
    **{c: "America/Halifax" for c in ("902", "782", "506")},                                     # Atlantic
    "709": "America/St_Johns",
}


def zone(tz_name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def to_local(dt: Optional[datetime], tz_name: Optional[str]) -> Optional[datetime]:
    """Aware datetime in the contractor's zone. Naive values are treated as UTC (how they are stored)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone(tz_name))


def timezone_for_phone(e164: Optional[str]) -> Optional[str]:
    """Best-guess time zone from a Canadian NANP number; None if unknown (caller keeps its default)."""
    digits = "".join(ch for ch in (e164 or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return _AREA_CODE_TZ.get(digits[:3]) if len(digits) == 10 else None


def area_codes_in_zone(tz_name: str) -> list[str]:
    return [code for code, z in _AREA_CODE_TZ.items() if z == tz_name]
