"""
Phase 5: Web push notification service.

STATUS: Push *sending* is not yet implemented — a VAPID provider has not been
selected.  Subscription storage (PushSubscription model) and the service
scaffolding are complete.  The send methods are intentionally no-ops and log a
clear warning so the absence of push delivery is always visible in logs.

To implement:
  1. Pick a provider (pywebpush, web-push, etc.)
  2. Set VAPID_PRIVATE_KEY in Railway env vars
  3. Replace the _NOT_IMPLEMENTED bodies below with real send logic
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_PUSH_NOT_IMPLEMENTED_MSG = (
    "Web push sending is not yet implemented — no VAPID provider configured. "
    "Subscription stored but no push will be delivered."
)


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
        """
        Send urgent call alert push notification.
        NOT YET IMPLEMENTED — logs a warning and returns without sending.
        """
        logger.warning(
            "push.send_urgent_alert: %s | tenant=%s call=%s urgency=%s",
            _PUSH_NOT_IMPLEMENTED_MSG,
            tenant_id,
            call_id,
            urgency_level,
        )

    async def send_daily_digest(
        self,
        tenant_id: uuid.UUID,
        stats: dict,
        db: AsyncSession,
    ) -> None:
        """
        Send daily digest push notification.
        NOT YET IMPLEMENTED — logs a warning and returns without sending.
        """
        logger.warning(
            "push.send_daily_digest: %s | tenant=%s",
            _PUSH_NOT_IMPLEMENTED_MSG,
            tenant_id,
        )
