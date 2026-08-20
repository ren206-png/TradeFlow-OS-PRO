"""
MembershipService — Phase 6 Membership/Service-Plan Recognition.

Feature flag: commercial_intake or french_bilingual (either enables membership lookup).
Fail open: FSM timeout (>3s) → return (None, 0) → standard flow, no crash.
Confirmatory only: "Are you calling about service at [address]?" — never assertive.
Confidence threshold: >= 75 to trigger acknowledgment.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from app.models.contractor import Contractor

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 75
_FSM_TIMEOUT_SECONDS = 3.0

# In-memory lookup cache: {(contractor_id, phone): (lead_or_None, confidence, expiry_ts)}
_LOOKUP_CACHE: dict[tuple[str, str], tuple[Optional[object], int, float]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


class MembershipService:

    async def lookup_caller(
        self,
        from_number: str,
        contractor: Contractor,
        db,
    ) -> tuple[Optional[object], int]:
        """
        Match inbound caller ID against FSM customer records (Lead table).
        Returns (matched_lead_or_None, confidence_score 0-100).

        Cache TTL: 5 minutes per contractor+phone pair.
        Fail open: FSM timeout (>3s) → (None, 0) — never crash.
        Confidence threshold: >= 75 to trigger acknowledgment.
        """
        if not from_number:
            return None, 0

        cache_key = (str(contractor.id), from_number)
        now = time.monotonic()

        # Check cache
        cached = _LOOKUP_CACHE.get(cache_key)
        if cached is not None:
            lead, confidence, expiry = cached
            if now < expiry:
                logger.debug(
                    "membership: cache hit | tenant=%s phone=%s confidence=%d",
                    contractor.id, from_number, confidence,
                )
                return lead, confidence

        # Perform lookup with timeout
        try:
            lead, confidence = await asyncio.wait_for(
                self._db_lookup(from_number, contractor, db),
                timeout=_FSM_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "membership: FSM lookup timeout (>%ss) | tenant=%s phone=%s — fail open",
                _FSM_TIMEOUT_SECONDS, contractor.id, from_number,
            )
            return None, 0
        except Exception as exc:
            logger.warning(
                "membership: lookup failed (fail open) | tenant=%s phone=%s err=%s",
                contractor.id, from_number, exc,
            )
            return None, 0

        # Populate cache
        _LOOKUP_CACHE[cache_key] = (lead, confidence, now + _CACHE_TTL_SECONDS)

        return lead, confidence

    async def _db_lookup(
        self,
        from_number: str,
        contractor: Contractor,
        db,
    ) -> tuple[Optional[object], int]:
        """
        Query Lead table for matching phone number (tenant-isolated).
        Returns (lead, confidence) where confidence is 0 or 95 (exact match).
        """
        try:
            from sqlalchemy import select
            from app.models.lead import Lead

            result = await db.execute(
                select(Lead).where(
                    Lead.contractor_id == contractor.id,
                    Lead.phone == from_number,
                ).order_by(Lead.created_at.desc()).limit(1)
            )
            lead = result.scalar_one_or_none()
            if lead is None:
                return None, 0

            # Exact phone match = high confidence
            return lead, 95

        except Exception as exc:
            logger.warning(
                "membership: db_lookup query failed | tenant=%s phone=%s err=%s",
                contractor.id, from_number, exc,
            )
            return None, 0

    def get_member_greeting_addition(
        self,
        lead: object,
        contractor: Contractor,
        confidence: int,
    ) -> Optional[str]:
        """
        Returns confirmatory (never assertive) greeting addition.
        Only if confidence >= 75.

        Must be a question ("Are you calling about service at [address]?").
        Never: "Hello [name]!" — assertive greetings are prohibited.
        Returns None if confidence < 75 or no match.
        """
        if lead is None:
            return None
        if confidence < _CONFIDENCE_THRESHOLD:
            return None

        address = getattr(lead, "service_address", None)
        if address:
            # Confirmatory question — always a question, never an assertion
            return f"Are you calling about service at {address}?"

        # No address — cannot confirm without risk of assertiveness
        return None

    @staticmethod
    def clear_cache() -> None:
        """Clear the in-memory lookup cache (for testing)."""
        _LOOKUP_CACHE.clear()
