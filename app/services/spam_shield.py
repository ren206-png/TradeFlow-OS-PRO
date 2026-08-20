"""
Phase 5: Spam Shield — blocks abusive callers before they reach the AI agent.
Blocked calls are reviewable and one-tap unblockable (false-positive safe).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SpamShield:

    async def check_call(
        self,
        from_number: str,
        to_number: str,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> tuple[bool, str]:
        """
        Returns (should_block, reason).
        False positive rate is tracked — blocks are always reviewable.
        """
        from app.models.spam_block import SpamBlock
        from app.models.call import CallSession

        # 1. Manual block: number in spam_blocks for this tenant
        existing_result = await db.execute(
            select(SpamBlock).where(
                SpamBlock.tenant_id == tenant_id,
                SpamBlock.phone_number == from_number,
                SpamBlock.is_active == True,  # noqa: E712
            )
        )
        existing_block = existing_result.scalar_one_or_none()
        if existing_block:
            return True, "manual_block"

        # 2. Repeat hangup: ≥3 call_sessions with duration_seconds < 5 in last 60 min
        sixty_min_ago = datetime.now(tz=timezone.utc) - timedelta(minutes=60)
        hangup_result = await db.execute(
            select(func.count(CallSession.id)).where(
                CallSession.contractor_id == tenant_id,
                CallSession.duration_seconds < 5,
                CallSession.started_at >= sixty_min_ago,
            )
        )
        # We need to filter by from_number — CallSession doesn't store from_number directly.
        # Use lead phone matching as a proxy via join.
        from app.models.lead import Lead
        hangup_count_result = await db.execute(
            select(func.count(CallSession.id))
            .join(Lead, CallSession.lead_id == Lead.id, isouter=True)
            .where(
                CallSession.contractor_id == tenant_id,
                CallSession.started_at >= sixty_min_ago,
                CallSession.duration_seconds < 5,
                Lead.phone == from_number,
            )
        )
        hangup_count = int(hangup_count_result.scalar() or 0)
        if hangup_count >= 3:
            await self._record_block(
                from_number, tenant_id, "repeat_hangup", "behavioral", db
            )
            return True, "repeat_hangup"

        # 3. Cross-tenant abuse: ≥5 call_sessions across ALL tenants in last 24h
        twenty_four_h_ago = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        cross_tenant_result = await db.execute(
            select(func.count(CallSession.id))
            .join(Lead, CallSession.lead_id == Lead.id, isouter=True)
            .where(
                CallSession.started_at >= twenty_four_h_ago,
                Lead.phone == from_number,
            )
        )
        cross_count = int(cross_tenant_result.scalar() or 0)
        if cross_count >= 5:
            await self._record_block(
                from_number, tenant_id, "cross_tenant_abuse", "behavioral", db
            )
            return True, "cross_tenant_abuse"

        # 4. Carrier spam flag — not yet stored; log and skip
        logger.debug(
            "spam_shield: carrier spam check not_implemented for %s — returning no block",
            from_number,
        )

        return False, ""

    async def _record_block(
        self,
        phone_number: str,
        tenant_id: uuid.UUID,
        reason: str,
        source: str,
        db: AsyncSession,
    ) -> None:
        """Record a new spam block row (idempotent: skip if already active)."""
        from app.models.spam_block import SpamBlock

        existing = await db.execute(
            select(SpamBlock).where(
                SpamBlock.tenant_id == tenant_id,
                SpamBlock.phone_number == phone_number,
                SpamBlock.is_active == True,  # noqa: E712
            )
        )
        if existing.scalar_one_or_none():
            return  # already blocked

        block = SpamBlock(
            tenant_id=tenant_id,
            phone_number=phone_number,
            block_reason=reason,
            block_source=source,
            is_active=True,
        )
        db.add(block)
        await db.flush()
        logger.info("spam_shield: blocked %s for tenant %s reason=%s", phone_number, tenant_id, reason)

    async def report_false_positive(
        self, block_id: uuid.UUID, tenant_id: uuid.UUID, db: AsyncSession
    ) -> None:
        """
        One-tap unblock. Sets is_active=False, false_positive_reported_at=now().
        NEVER deletes — keeps the record for false-positive rate tracking.
        """
        from app.models.spam_block import SpamBlock

        result = await db.execute(
            select(SpamBlock).where(
                SpamBlock.id == block_id,
                SpamBlock.tenant_id == tenant_id,
            )
        )
        block = result.scalar_one_or_none()
        if block is None:
            logger.warning("report_false_positive: block %s not found for tenant %s", block_id, tenant_id)
            return

        block.is_active = False
        block.false_positive_reported_at = datetime.now(tz=timezone.utc)
        await db.flush()
        logger.info("spam_shield: false positive unblocked | block=%s tenant=%s", block_id, tenant_id)

    async def get_shield_stats(
        self, tenant_id: uuid.UUID, days: int, db: AsyncSession
    ) -> dict:
        """
        Returns stats including false_positive_rate_pct as an integer (never float).
        """
        from app.models.spam_block import SpamBlock

        since = datetime.now(tz=timezone.utc) - timedelta(days=days)

        result = await db.execute(
            select(SpamBlock).where(
                SpamBlock.tenant_id == tenant_id,
                SpamBlock.created_at >= since,
            )
        )
        blocks = result.scalars().all()

        total_blocked = len(blocks)
        false_positives = sum(1 for b in blocks if b.false_positive_reported_at is not None)
        # Integer percentage — never float
        false_positive_rate_pct = int((false_positives / total_blocked * 100)) if total_blocked > 0 else 0

        by_reason: dict[str, int] = {}
        for b in blocks:
            by_reason[b.block_reason] = by_reason.get(b.block_reason, 0) + 1

        return {
            "total_blocked": total_blocked,
            "false_positives": false_positives,
            "false_positive_rate_pct": false_positive_rate_pct,  # integer — surfaced in dashboard
            "by_reason": by_reason,
        }
