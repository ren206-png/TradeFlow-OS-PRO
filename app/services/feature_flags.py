"""
Per-tenant feature flag service.
Missing row always returns False (default OFF — safe production baseline).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feature_flag import FeatureFlag

logger = logging.getLogger(__name__)


async def is_enabled(tenant_id: str, flag_key: str, db: AsyncSession) -> bool:
    """
    Returns True only if a FeatureFlag row exists for this tenant+flag AND enabled=True.
    Missing row → False (flag off by default).
    Any DB error → logs a warning, returns False (fail-safe).
    """
    try:
        result = await db.execute(
            select(FeatureFlag).where(
                FeatureFlag.tenant_id == tenant_id,
                FeatureFlag.flag_key == flag_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        return bool(row.enabled)
    except Exception as exc:
        logger.warning(
            "feature_flags: DB error checking %s/%s — defaulting to OFF. err=%s",
            tenant_id, flag_key, exc,
        )
        return False
