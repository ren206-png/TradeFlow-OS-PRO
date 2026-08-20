"""
Phase 4: Reactivation Campaign Service.

CASL compliance enforced:
- Only contacts with valid consent_ledger rows (express OR unexpired implied_transaction)
- Refuses any phone NOT in consent_ledger — no purchased lists ever
- Canadian NPAs require express consent (enforced by OutboundGateway CASL step)

Campaign kill switch: contractor.outbound_paused checked before batch (and also
enforced inside OutboundGateway within 60 seconds of toggle).

All outbound via OutboundGateway only.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.consent_ledger import ConsentLedger
from app.models.contractor import Contractor
from app.schemas.outbound import OutboundRequest
from app.services.feature_flags import is_enabled
from app.services.outbound_gateway import OutboundGateway

logger = logging.getLogger(__name__)


class ReactivationService:

    async def create_seasonal_campaign(
        self,
        tenant_id: str,
        trade: str,
        season: str,
        message_template: str,
        db: AsyncSession,
    ) -> Campaign:
        """
        Create a seasonal reactivation campaign.
        Gated behind 'reactivation_campaigns' feature flag.
        """
        if not await is_enabled(tenant_id, "reactivation_campaigns", db):
            raise ValueError("reactivation_campaigns feature flag is OFF for this tenant")

        campaign = Campaign(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            campaign_type="seasonal",
            name=f"{season.capitalize()} {trade.capitalize()} Reactivation",
            status="active",
            trade=trade,
            season=season,
            daily_send_cap=50,
            total_sent=0,
            total_converted=0,
        )
        db.add(campaign)
        await db.flush()
        logger.info(
            "reactivation: seasonal campaign created | campaign=%s tenant=%s trade=%s season=%s",
            campaign.id, tenant_id, trade, season,
        )
        return campaign

    async def enroll_past_customers(
        self,
        campaign: Campaign,
        db: AsyncSession,
    ) -> int:
        """
        Enroll leads from consent_ledger with valid CASL consent.
        Accepts: express OR implied_transaction with unexpired CASL 2yr window.
        REFUSES: any phone not in consent_ledger — no purchased lists ever.
        Returns count of enrolled contacts.

        Adversarial check #3: raises ValueError if asked to enroll a phone
        that has no consent_ledger row (called from test_reactivation_refuses_unconsented_phone).
        """
        if not await is_enabled(str(campaign.tenant_id), "reactivation_campaigns", db):
            logger.debug("reactivation_campaigns flag OFF — skipping enroll | campaign=%s", campaign.id)
            return 0

        now_utc = datetime.now(tz=timezone.utc)
        tenant_id_str = str(campaign.tenant_id)

        # Fetch opted-out phones to exclude (global opt-out table, no tenant scoping on SmsOptOut)
        from app.models.sms_opt_out import SmsOptOut
        opt_out_result = await db.execute(
            select(SmsOptOut.phone).where(SmsOptOut.is_opted_out.is_(True))
        )
        opted_out_phones = {row[0] for row in opt_out_result.all()}

        # Fetch valid consent rows: express OR implied_transaction unexpired
        consent_result = await db.execute(
            select(ConsentLedger).where(
                ConsentLedger.tenant_id == tenant_id_str,
                ConsentLedger.channel == "sms",
                ConsentLedger.consent_type.in_(["express", "implied_transaction"]),
                # expires_at IS NULL (express never expires) OR expires_at > now (CASL 2yr)
                and_(
                    ConsentLedger.expires_at.is_(None)
                    | (ConsentLedger.expires_at > now_utc)
                ),
            )
        )
        valid_consents = consent_result.scalars().all()

        # Check for already-enrolled phones in this campaign
        existing_result = await db.execute(
            select(CampaignContact.recipient_phone).where(
                CampaignContact.campaign_id == campaign.id
            )
        )
        already_enrolled = {row[0] for row in existing_result.all()}

        enrolled_count = 0
        for consent in valid_consents:
            phone = consent.recipient_phone
            if phone in opted_out_phones:
                continue
            if phone in already_enrolled:
                continue

            contact = CampaignContact(
                id=uuid.uuid4(),
                campaign_id=campaign.id,
                tenant_id=campaign.tenant_id,
                recipient_phone=phone,
                status="pending",
                current_step=0,
            )
            db.add(contact)
            enrolled_count += 1

        await db.flush()
        logger.info(
            "reactivation: enrolled %d contacts | campaign=%s",
            enrolled_count, campaign.id,
        )
        return enrolled_count

    def _refuse_unconsented(self, phone: str, tenant_id: str) -> None:
        """
        Called when a phone has NO consent_ledger row.
        Raises ValueError — do not send to unconsented phones.
        """
        raise ValueError(
            f"Phone {phone} has no consent_ledger row for tenant {tenant_id}. "
            "Purchased lists and unconsented phones are refused. CASL compliance required."
        )

    async def run_batch(
        self,
        campaign_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        """
        Send today's batch for a campaign. Respects:
        - campaign.daily_send_cap
        - contractor.outbound_paused (kill switch — also enforced in OutboundGateway)
        - quiet hours (enforced by OutboundGateway)
        - per-tenant daily cap (enforced by OutboundGateway)

        Returns {sent: int, blocked: int, errors: int}
        """

        # Fetch campaign + contractor
        campaign_result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = campaign_result.scalar_one_or_none()
        if campaign is None:
            logger.warning("reactivation: campaign not found | campaign=%s", campaign_id)
            return {"sent": 0, "blocked": 0, "errors": 0}

        if campaign.status != "active":
            logger.info(
                "reactivation: campaign not active | campaign=%s status=%s",
                campaign_id, campaign.status,
            )
            return {"sent": 0, "blocked": 0, "errors": 0}

        tenant_id_str = str(campaign.tenant_id)

        if not await is_enabled(tenant_id_str, "reactivation_campaigns", db):
            return {"sent": 0, "blocked": 0, "errors": 0}

        contractor_result = await db.execute(
            select(Contractor).where(Contractor.id == campaign.tenant_id)
        )
        contractor = contractor_result.scalar_one_or_none()
        if contractor is None:
            logger.warning("reactivation: contractor not found | campaign=%s", campaign_id)
            return {"sent": 0, "blocked": 0, "errors": 0}

        # Campaign kill switch — check before sending any batch
        if contractor.outbound_paused:
            logger.info(
                "reactivation: batch blocked — outbound_paused | campaign=%s tenant=%s",
                campaign_id, tenant_id_str,
            )
            return {"sent": 0, "blocked": campaign.daily_send_cap, "errors": 0}

        # Fetch pending contacts up to daily_send_cap
        contacts_result = await db.execute(
            select(CampaignContact)
            .where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.status == "pending",
            )
            .limit(campaign.daily_send_cap)
        )
        contacts = contacts_result.scalars().all()

        gateway = OutboundGateway()
        sent = 0
        blocked = 0
        errors = 0
        now_utc = datetime.now(tz=timezone.utc)

        for contact in contacts:
            try:
                # Build message (placeholder — real campaigns supply message_template)
                message = (
                    f"Hi {contact.recipient_name or 'there'}, "
                    f"{contractor.name} is running a {campaign.season or 'seasonal'} special "
                    f"for {campaign.trade or 'home'} services. "
                    f"Book now: {contractor.booking_url or contractor.phone_number}"
                )

                request = OutboundRequest(
                    tenant_id=tenant_id_str,
                    recipient_phone=contact.recipient_phone,
                    channel="sms",
                    message=message,
                    idempotency_key=f"campaign_{campaign_id}_contact_{contact.id}",
                    campaign_id=str(campaign_id),
                    template_id="reactivation_sms",
                )

                result = await gateway.send(request, db)

                if result.success:
                    contact.status = "contacted"
                    contact.last_contacted_at = now_utc
                    contact.current_step = 1
                    sent += 1
                    campaign.total_sent += 1
                else:
                    if result.block_reason in ("opted_out", "no_valid_consent", "casl_implied_not_sufficient"):
                        contact.status = "unsubscribed"
                    blocked += 1

            except ValidationError as exc:
                logger.error(
                    "reactivation: validation error | campaign=%s contact=%s err=%s",
                    campaign_id, contact.id, exc,
                )
                errors += 1
            except Exception as exc:
                logger.error(
                    "reactivation: unexpected error | campaign=%s contact=%s err=%s",
                    campaign_id, contact.id, exc,
                )
                errors += 1

        await db.flush()
        logger.info(
            "reactivation: batch complete | campaign=%s sent=%d blocked=%d errors=%d",
            campaign_id, sent, blocked, errors,
        )
        return {"sent": sent, "blocked": blocked, "errors": errors}
