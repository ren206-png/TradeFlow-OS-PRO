"""
Phase 5: Web push notification service.
Stores subscriptions; defers actual push sending until a VAPID provider is configured.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PushNotificationService:

    async def register_subscription(
        self,
        tenant_id: uuid.UUID,
        subscription_json: dict,
        db: AsyncSession,
    ) -> None:
        """Store a web push subscription for a tenant."""
        from app.models.push_subscription import PushSubscription

        endpoint = subscription_json.get("endpoint", "")
        keys = subscription_json.get("keys", {})
        auth = keys.get("auth", "")
        p256dh = keys.get("p256dh", "")

        if not endpoint:
            logger.warning("register_subscription: missing endpoint | tenant=%s", tenant_id)
            return

        sub = PushSubscription(
            tenant_id=tenant_id,
            endpoint=endpoint,
            auth=auth,
            p256dh=p256dh,
        )
        db.add(sub)
        await db.flush()
        logger.info("push: registered subscription | tenant=%s endpoint=%.60s", tenant_id, endpoint)

    async def send_urgent_alert(
        self,
        tenant_id: uuid.UUID,
        call_id: str,
        urgency_level: str,
        db: AsyncSession,
    ) -> None:
        """Send urgent call alert push. Deferred if no VAPID key configured."""
        from app.config import settings
        if not getattr(settings, "vapid_private_key", ""):
            logger.info(
                "push: vapid_private_key not configured — skipping urgent alert | tenant=%s call=%s",
                tenant_id, call_id,
            )
            return
        # TODO: implement actual push send when VAPID provider decided
        logger.info("push: urgent alert stub | tenant=%s call=%s urgency=%s", tenant_id, call_id, urgency_level)

    async def send_daily_digest(
        self,
        tenant_id: uuid.UUID,
        stats: dict,
        db: AsyncSession,
    ) -> None:
        """Send daily digest push. Deferred if no VAPID key configured."""
        from app.config import settings
        if not getattr(settings, "vapid_private_key", ""):
            logger.info(
                "push: vapid_private_key not configured — skipping daily digest | tenant=%s", tenant_id
            )
            return
        # TODO: implement actual push send when VAPID provider decided
        logger.info("push: daily digest stub | tenant=%s", tenant_id)
