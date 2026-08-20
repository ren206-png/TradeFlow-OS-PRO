"""
Phone validation and abuse-prevention for callback endpoints.

All checks are purely DB-query-based — no external API calls.
Returns (is_valid: bool, reason: str).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# NPAs that should never receive automated outbound calls
_BLOCKED_NPAS: frozenset[str] = frozenset({"900", "976", "550"})

# E.164 pattern: + followed by 7–15 digits
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

# Rate-limit thresholds (cross-tenant)
_MAX_PER_HOUR = 3
_MAX_PER_DAY = 10


def _extract_npa(phone: str) -> str | None:
    """Extract 3-digit NPA from E.164 North American number."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits[0] == "1":
        return digits[1:4]
    if len(digits) >= 11 and phone.startswith("+1"):
        return digits[1:4]
    return None


async def validate_callback_phone(phone: str, db: AsyncSession) -> tuple[bool, str]:
    """
    Returns (is_valid, reason).
    Checks:
      1. E.164 format validation
      2. Blocked NPA prefixes (900, 976, 550)
      3. Rate limit: max 3 callback requests per phone per hour (cross-tenant)
      4. Rate limit: max 10 callback requests per phone per 24h (cross-tenant)
    """
    # 1. E.164 format
    if not _E164_RE.match(phone):
        logger.info("phone_validation: invalid E.164 | phone=%r", phone)
        return False, "invalid_phone_format"

    # 2. Blocked NPA
    npa = _extract_npa(phone)
    if npa and npa in _BLOCKED_NPAS:
        logger.info("phone_validation: blocked NPA %s | phone=%s", npa, phone)
        return False, f"blocked_npa_{npa}"

    # 3 & 4. Rate limits — query callback_requests table
    try:
        from app.models.callback_request import CallbackRequest

        now_utc = datetime.now(tz=timezone.utc)
        hour_ago = now_utc - timedelta(hours=1)
        day_ago = now_utc - timedelta(hours=24)

        hour_count_result = await db.execute(
            select(func.count()).select_from(CallbackRequest).where(
                CallbackRequest.caller_phone == phone,
                CallbackRequest.created_at >= hour_ago,
            )
        )
        hour_count = hour_count_result.scalar() or 0
        if hour_count >= _MAX_PER_HOUR:
            logger.info(
                "phone_validation: rate_limit_hour | phone=%s count=%d", phone, hour_count
            )
            return False, "rate_limit_exceeded_hour"

        day_count_result = await db.execute(
            select(func.count()).select_from(CallbackRequest).where(
                CallbackRequest.caller_phone == phone,
                CallbackRequest.created_at >= day_ago,
            )
        )
        day_count = day_count_result.scalar() or 0
        if day_count >= _MAX_PER_DAY:
            logger.info(
                "phone_validation: rate_limit_day | phone=%s count=%d", phone, day_count
            )
            return False, "rate_limit_exceeded_day"

    except Exception as exc:
        logger.error("phone_validation: rate-limit DB check failed | err=%s", exc)
        # Fail open — do not block valid users due to a DB error
        return True, ""

    return True, ""
