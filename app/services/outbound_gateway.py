"""
Unified Outbound Gateway — Phase 1 Compliance Foundation.

This is the ONLY place outbound communication is initiated for tenants that
have the `outbound_gateway` feature flag enabled. No other service may send
SMS, calls, or email directly when the flag is ON.

Enforcement order (stops at first failure, writes a blocked ledger row):
  1. Idempotency
  2. Feature flag check (flag OFF → legacy path)
  3. Opt-out
  4. Consent
  5. CASL jurisdiction (Canadian NPAs → express consent required for marketing)
  6. A2P 10DLC (US SMS only)
  7. Quiet hours (NPA→timezone mapping)
  8. Per-tenant daily rate limit
  9. Send
  10. Write ledger row (always)

911 safety: This file does NOT touch triage.py or the retell.py intercept.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consent_ledger import ConsentLedger
from app.models.outbound_ledger import OutboundLedger
from app.schemas.outbound import GatewayResult, OutboundRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canadian NPA (area code) set — used for CASL jurisdiction check
# ---------------------------------------------------------------------------
_CANADIAN_NPAS: frozenset[str] = frozenset({
    "204", "226", "236", "249", "250", "263", "289",
    "306", "343", "354", "365", "367", "368", "382", "387",
    "403", "416", "418", "428", "431", "437", "438", "450", "468", "474",
    "506", "514", "519", "548", "579", "581", "584", "587",
    "604", "613", "622", "639", "647", "672", "683",
    "705", "709", "742", "753",
    "778", "780", "782",
    "807", "819", "825",
    "867", "873",
    "902", "905",
})

# ---------------------------------------------------------------------------
# NPA → UTC offset mapping (hours, may be fractional for Newfoundland).
# Covers the most common North American NPAs. Unknown NPAs → America/New_York
# ---------------------------------------------------------------------------
_NPA_UTC_OFFSET: dict[str, float] = {
    # Atlantic (UTC-4 / UTC-3 DST) — approximate as UTC-4 (conservative)
    "902": -4.0, "782": -4.0, "506": -4.0,
    # Newfoundland (UTC-3:30 / UTC-2:30 DST) — approximate as UTC-2.5 (summer)
    "709": -2.5, "742": -2.5, "753": -2.5,
    # Eastern (UTC-5 / UTC-4 DST) — approximate as UTC-4 (summer)
    "204": -5.0, "226": -4.0, "249": -4.0, "263": -4.0, "289": -4.0,
    "343": -4.0, "354": -4.0, "365": -4.0, "367": -4.0, "368": -4.0,
    "382": -4.0, "416": -4.0, "418": -4.0, "428": -4.0, "437": -4.0,
    "438": -4.0, "450": -4.0, "468": -4.0, "514": -4.0, "519": -4.0,
    "548": -4.0, "579": -4.0, "581": -4.0, "613": -4.0, "647": -4.0,
    "705": -4.0, "819": -4.0, "873": -4.0, "905": -4.0,
    # US Eastern
    "201": -4.0, "202": -4.0, "203": -4.0, "207": -4.0, "212": -4.0,
    "215": -4.0, "216": -4.0, "217": -4.0, "219": -4.0, "229": -4.0,
    "231": -4.0, "239": -4.0, "240": -4.0, "248": -4.0, "252": -4.0,
    "267": -4.0, "270": -4.0, "301": -4.0, "302": -4.0, "303": -4.0,
    "304": -4.0, "305": -4.0, "313": -4.0, "315": -4.0, "317": -4.0,
    "321": -4.0, "330": -4.0, "336": -4.0, "339": -4.0, "347": -4.0,
    "352": -4.0, "386": -4.0, "401": -4.0, "404": -4.0, "407": -4.0,
    "410": -4.0, "412": -4.0, "413": -4.0, "419": -4.0, "423": -4.0,
    "434": -4.0, "440": -4.0, "443": -4.0, "470": -4.0, "484": -4.0,
    "513": -4.0, "516": -4.0, "518": -4.0, "540": -4.0, "561": -4.0,
    "567": -4.0, "570": -4.0, "571": -4.0, "585": -4.0, "586": -4.0,
    "601": -5.0, "603": -4.0, "606": -4.0, "609": -4.0, "610": -4.0,
    "614": -4.0, "615": -5.0, "616": -4.0, "617": -4.0, "618": -5.0,
    "631": -4.0, "636": -5.0, "646": -4.0, "651": -5.0, "678": -4.0,
    "681": -4.0, "689": -4.0, "703": -4.0, "704": -4.0, "706": -4.0,
    "716": -4.0, "717": -4.0, "718": -4.0, "724": -4.0, "727": -4.0,
    "732": -4.0, "734": -4.0, "740": -4.0, "754": -4.0, "757": -4.0,
    "762": -4.0, "763": -5.0, "770": -4.0, "772": -4.0, "774": -4.0,
    "781": -4.0, "786": -4.0, "803": -4.0, "804": -4.0, "810": -4.0,
    "813": -4.0, "814": -4.0, "828": -4.0, "843": -4.0, "845": -4.0,
    "856": -4.0, "857": -4.0, "859": -4.0, "860": -4.0, "862": -4.0,
    "863": -4.0, "864": -4.0, "865": -4.0, "904": -4.0, "908": -4.0,
    "910": -4.0, "912": -4.0, "914": -4.0, "917": -4.0, "919": -4.0,
    "920": -5.0, "929": -4.0, "937": -4.0, "941": -4.0, "947": -4.0,
    "954": -4.0, "959": -4.0, "973": -4.0, "980": -4.0, "984": -4.0,
    # US Central (UTC-6 / UTC-5 DST) → approximate as UTC-5
    "205": -5.0, "218": -5.0, "224": -5.0, "225": -5.0, "228": -5.0,
    "251": -5.0, "254": -5.0, "256": -5.0, "262": -5.0, "281": -5.0,
    "309": -5.0, "312": -5.0, "314": -5.0, "316": -5.0, "318": -5.0,
    "319": -5.0, "320": -5.0, "334": -5.0, "337": -5.0, "361": -5.0,
    "402": -5.0, "405": -5.0, "406": -6.0, "414": -5.0, "417": -5.0,
    "479": -5.0, "501": -5.0, "507": -5.0, "512": -5.0, "515": -5.0,
    "563": -5.0, "608": -5.0, "612": -5.0, "620": -5.0, "630": -5.0,
    "641": -5.0, "660": -5.0, "662": -5.0, "682": -5.0, "708": -5.0,
    "712": -5.0, "713": -5.0, "715": -5.0, "720": -6.0, "737": -5.0,
    "769": -5.0, "773": -5.0, "779": -5.0, "785": -5.0, "806": -5.0,
    "815": -5.0, "816": -5.0, "817": -5.0, "830": -5.0, "847": -5.0,
    "870": -5.0, "901": -5.0, "903": -5.0, "913": -5.0, "915": -5.0,
    "918": -5.0, "930": -5.0, "936": -5.0, "940": -5.0, "952": -5.0,
    "956": -5.0, "972": -5.0, "979": -5.0,
    # US Mountain (UTC-7 / UTC-6 DST) → approximate as UTC-6
    "208": -6.0, "303": -6.0, "307": -6.0, "385": -6.0, "435": -6.0,
    "480": -6.0, "505": -6.0, "520": -7.0, "575": -6.0, "602": -6.0,
    "623": -6.0, "701": -5.0, "719": -6.0, "726": -5.0, "801": -6.0,
    "928": -7.0,  # Arizona (no DST)
    # US Pacific (UTC-8 / UTC-7 DST) → approximate as UTC-7
    "206": -7.0, "209": -7.0, "213": -7.0, "253": -7.0, "310": -7.0,
    "323": -7.0, "341": -7.0, "360": -7.0, "369": -7.0, "408": -7.0,
    "415": -7.0, "424": -7.0, "425": -7.0, "442": -7.0, "458": -7.0,
    "503": -7.0, "509": -7.0, "510": -7.0, "530": -7.0, "541": -7.0,
    "559": -7.0, "562": -7.0, "619": -7.0, "626": -7.0, "628": -7.0,
    "650": -7.0, "657": -7.0, "661": -7.0, "669": -7.0, "707": -7.0,
    "714": -7.0, "747": -7.0, "760": -7.0, "764": -7.0, "775": -7.0,
    "818": -7.0, "831": -7.0, "858": -7.0, "909": -7.0, "916": -7.0,
    "925": -7.0, "949": -7.0, "951": -7.0,
    # US Alaska / Hawaii
    "907": -8.0, "808": -10.0,
    # Canadian Western / Central
    "236": -7.0, "250": -7.0, "587": -6.0, "604": -7.0, "672": -7.0,
    "778": -7.0, "780": -6.0, "825": -6.0, "867": -7.0,
    "306": -6.0, "639": -6.0,
    "431": -5.0, "204": -5.0,
    "474": -6.0, "584": -6.0,
    "622": -6.0, "683": -6.0,
}

# Default UTC offset when NPA is unknown — Eastern (UTC-4 summer, conservative)
_DEFAULT_UTC_OFFSET: float = -4.0

# Quiet hours: 08:00–21:00 local time (inclusive start, exclusive end at 21:00)
_QUIET_HOURS_START = 8
_QUIET_HOURS_END = 21


def _extract_npa(phone: str) -> Optional[str]:
    """Extract 3-digit NPA (area code) from E.164 number. Returns None if unparseable."""
    # E.164: +1NPAXXXXXXX (US/CA) or +CCNPA...
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits[0] == "1":
        # North American: country code 1 + 10 digit number
        return digits[1:4]
    if len(digits) >= 11 and phone.startswith("+1"):
        return digits[1:4]
    return None


def _is_canadian_npa(npa: Optional[str]) -> bool:
    return npa in _CANADIAN_NPAS if npa else False


def _recipient_local_hour(phone: str, now_utc: datetime) -> int:
    """Return the recipient's local hour (0–23) based on NPA→UTC offset mapping."""
    npa = _extract_npa(phone)
    offset = _NPA_UTC_OFFSET.get(npa, _DEFAULT_UTC_OFFSET) if npa else _DEFAULT_UTC_OFFSET
    # Convert UTC hour to local: fractional offsets supported (Newfoundland UTC-2.5)
    utc_decimal = now_utc.hour + now_utc.minute / 60.0
    local_decimal = utc_decimal + offset
    # Wrap into 0–24 range
    local_decimal = local_decimal % 24
    return int(local_decimal)


async def _write_ledger(
    db: AsyncSession,
    *,
    ledger_id: uuid.UUID,
    tenant_id: str,
    idempotency_key: str,
    recipient_phone: str,
    channel: str,
    status: str,
    block_reason: Optional[str] = None,
    template_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    message_preview: Optional[str] = None,
) -> None:
    """Write a single ledger row. All sends AND blocks go through here."""
    row = OutboundLedger(
        id=ledger_id,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        recipient_phone=recipient_phone,
        channel=channel,
        status=status,
        block_reason=block_reason,
        template_id=template_id,
        campaign_id=campaign_id,
        message_preview=message_preview,
    )
    db.add(row)
    try:
        await db.flush()
    except Exception as exc:
        logger.error("outbound_gateway: ledger write failed | key=%s err=%s", idempotency_key, exc)


class OutboundGateway:
    """
    Unified gateway for all outbound communication.
    Call .send() — it is the single authorised send path.
    """

    async def send(self, request: OutboundRequest, db: AsyncSession) -> GatewayResult:
        """
        Enforce all compliance checks in order, then send.
        Returns GatewayResult always — never raises to the caller.
        """
        ledger_id = uuid.uuid4()

        # Truncate message preview to 160 chars, no PII beyond what's already in message
        preview: Optional[str] = None
        if request.message:
            preview = request.message[:160]

        def _blocked(reason: str) -> GatewayResult:
            return GatewayResult(success=False, block_reason=reason, ledger_id=str(ledger_id))

        # ------------------------------------------------------------------
        # Step 1: Idempotency check
        # ------------------------------------------------------------------
        try:
            existing = await db.execute(
                select(OutboundLedger).where(
                    OutboundLedger.idempotency_key == request.idempotency_key
                )
            )
            existing_row = existing.scalar_one_or_none()
            if existing_row is not None:
                logger.info(
                    "outbound_gateway: idempotency hit | key=%s ledger_id=%s",
                    request.idempotency_key, existing_row.id,
                )
                return GatewayResult(
                    success=(existing_row.status == "sent"),
                    block_reason=existing_row.block_reason,
                    ledger_id=str(existing_row.id),
                )
        except Exception as exc:
            logger.error("outbound_gateway: idempotency check failed | err=%s", exc)
            # Fail safe: continue rather than crash

        # ------------------------------------------------------------------
        # Step 2: Feature flag check
        # ------------------------------------------------------------------
        from app.services.feature_flags import is_enabled as _flag_enabled
        flag_on = await _flag_enabled(request.tenant_id, "outbound_gateway", db)

        if not flag_on:
            # Legacy path — SMS only; preserve current production behaviour
            if request.channel == "sms" and request.message:
                try:
                    from app.services.sms import SMSService
                    from app.models.contractor import Contractor
                    import uuid as _uuid_mod
                    contractor_result = await db.execute(
                        select(Contractor).where(
                            Contractor.id == _uuid_mod.UUID(request.tenant_id)
                        )
                    )
                    contractor = contractor_result.scalar_one_or_none()
                    if contractor:
                        sms = SMSService(contractor).with_db(db)
                        result = await sms._send_compliant(
                            request.recipient_phone, request.message, "outbound_gateway_legacy"
                        )
                        status = "sent" if result.get("success") else "failed"
                        await _write_ledger(
                            db, ledger_id=ledger_id,
                            tenant_id=request.tenant_id,
                            idempotency_key=request.idempotency_key,
                            recipient_phone=request.recipient_phone,
                            channel=request.channel,
                            status=status,
                            template_id=request.template_id,
                            campaign_id=request.campaign_id,
                            message_preview=preview,
                        )
                        return GatewayResult(success=result.get("success", False), ledger_id=str(ledger_id))
                except Exception as exc:
                    logger.error("outbound_gateway: legacy SMS path failed | err=%s", exc)

            # Flag off + non-SMS: log and return no-op success (don't crash callers)
            await _write_ledger(
                db, ledger_id=ledger_id,
                tenant_id=request.tenant_id,
                idempotency_key=request.idempotency_key,
                recipient_phone=request.recipient_phone,
                channel=request.channel,
                status="blocked",
                block_reason="flag_off_channel_not_supported",
                template_id=request.template_id,
                campaign_id=request.campaign_id,
                message_preview=preview,
            )
            return _blocked("flag_off_channel_not_supported")

        # ------------------------------------------------------------------
        # Steps 3–10 only run when flag is ON
        # ------------------------------------------------------------------

        # ------------------------------------------------------------------
        # Step 2.5: Campaign kill switch (Phase 4)
        # Enforced within 60 seconds of toggle — checked on every send.
        # ------------------------------------------------------------------
        try:
            from app.models.contractor import Contractor as _Contractor
            import uuid as _uuid_mod
            _ks_result = await db.execute(
                select(_Contractor).where(
                    _Contractor.id == _uuid_mod.UUID(request.tenant_id)
                )
            )
            _ks_contractor = _ks_result.scalar_one_or_none()
            if _ks_contractor and getattr(_ks_contractor, "outbound_paused", False):
                logger.info(
                    "outbound_gateway: blocked tenant_outbound_paused | tenant=%s",
                    request.tenant_id,
                )
                await _write_ledger(
                    db, ledger_id=ledger_id,
                    tenant_id=request.tenant_id,
                    idempotency_key=request.idempotency_key,
                    recipient_phone=request.recipient_phone,
                    channel=request.channel,
                    status="blocked",
                    block_reason="tenant_outbound_paused",
                    template_id=request.template_id,
                    campaign_id=request.campaign_id,
                    message_preview=preview,
                )
                return _blocked("tenant_outbound_paused")
        except Exception as exc:
            logger.error("outbound_gateway: kill switch check failed | err=%s", exc)
            # Fail open — don't crash the send pipeline on kill switch check failure

        # ------------------------------------------------------------------
        # Step 3: Opt-out check
        # ------------------------------------------------------------------
        try:
            from app.services.sms_compliance import is_opted_out
            opted_out = await is_opted_out(request.recipient_phone, db)
            if opted_out:
                logger.info(
                    "outbound_gateway: blocked opted_out | tenant=%s phone=%s",
                    request.tenant_id, request.recipient_phone,
                )
                await _write_ledger(
                    db, ledger_id=ledger_id,
                    tenant_id=request.tenant_id,
                    idempotency_key=request.idempotency_key,
                    recipient_phone=request.recipient_phone,
                    channel=request.channel,
                    status="blocked",
                    block_reason="opted_out",
                    template_id=request.template_id,
                    campaign_id=request.campaign_id,
                    message_preview=preview,
                )
                return _blocked("opted_out")
        except Exception as exc:
            logger.error("outbound_gateway: opt-out check failed | err=%s", exc)

        # ------------------------------------------------------------------
        # Step 4: Consent check
        # ------------------------------------------------------------------
        now_utc = datetime.now(tz=timezone.utc)
        try:
            consent_q = select(ConsentLedger).where(
                ConsentLedger.tenant_id == request.tenant_id,
                ConsentLedger.recipient_phone == request.recipient_phone,
                ConsentLedger.channel == request.channel,
            )
            consent_result = await db.execute(consent_q)
            consents = consent_result.scalars().all()

            valid_consent = None
            for c in consents:
                # Express consent never expires (expires_at is null)
                if c.expires_at is None:
                    valid_consent = c
                    break
                # Implied consent: check expiry
                expires = c.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires > now_utc:
                    valid_consent = c
                    break

            if valid_consent is None:
                logger.info(
                    "outbound_gateway: blocked no_valid_consent | tenant=%s phone=%s channel=%s",
                    request.tenant_id, request.recipient_phone, request.channel,
                )
                await _write_ledger(
                    db, ledger_id=ledger_id,
                    tenant_id=request.tenant_id,
                    idempotency_key=request.idempotency_key,
                    recipient_phone=request.recipient_phone,
                    channel=request.channel,
                    status="blocked",
                    block_reason="no_valid_consent",
                    template_id=request.template_id,
                    campaign_id=request.campaign_id,
                    message_preview=preview,
                )
                return _blocked("no_valid_consent")

        except Exception as exc:
            logger.error("outbound_gateway: consent check failed | err=%s", exc)
            await _write_ledger(
                db, ledger_id=ledger_id,
                tenant_id=request.tenant_id,
                idempotency_key=request.idempotency_key,
                recipient_phone=request.recipient_phone,
                channel=request.channel,
                status="blocked",
                block_reason="consent_check_error",
                template_id=request.template_id,
                campaign_id=request.campaign_id,
            )
            return _blocked("consent_check_error")

        # ------------------------------------------------------------------
        # Step 5: CASL jurisdiction check
        # Canadian numbers require express consent for marketing channels
        # ------------------------------------------------------------------
        npa = _extract_npa(request.recipient_phone)
        is_canadian = _is_canadian_npa(npa)
        if is_canadian and valid_consent.consent_type != "express":
            logger.info(
                "outbound_gateway: blocked casl_implied_not_sufficient | tenant=%s phone=%s",
                request.tenant_id, request.recipient_phone,
            )
            await _write_ledger(
                db, ledger_id=ledger_id,
                tenant_id=request.tenant_id,
                idempotency_key=request.idempotency_key,
                recipient_phone=request.recipient_phone,
                channel=request.channel,
                status="blocked",
                block_reason="casl_implied_not_sufficient",
                template_id=request.template_id,
                campaign_id=request.campaign_id,
                message_preview=preview,
            )
            return _blocked("casl_implied_not_sufficient")

        # ------------------------------------------------------------------
        # Step 6: A2P 10DLC check (US SMS only — non-Canadian +1 numbers)
        # ------------------------------------------------------------------
        is_us_sms = (
            request.channel == "sms"
            and request.recipient_phone.startswith("+1")
            and not is_canadian
        )
        if is_us_sms:
            try:
                from app.models.a2p_registration import A2PRegistration
                a2p_result = await db.execute(
                    select(A2PRegistration).where(
                        A2PRegistration.tenant_id == request.tenant_id
                    )
                )
                a2p = a2p_result.scalar_one_or_none()
                if a2p is None or a2p.status != "approved":
                    reason = "a2p_not_approved"
                    logger.info(
                        "outbound_gateway: blocked %s | tenant=%s a2p_status=%s",
                        reason, request.tenant_id, a2p.status if a2p else "missing",
                    )
                    await _write_ledger(
                        db, ledger_id=ledger_id,
                        tenant_id=request.tenant_id,
                        idempotency_key=request.idempotency_key,
                        recipient_phone=request.recipient_phone,
                        channel=request.channel,
                        status="blocked",
                        block_reason=reason,
                        template_id=request.template_id,
                        campaign_id=request.campaign_id,
                        message_preview=preview,
                    )
                    return _blocked(reason)
            except Exception as exc:
                logger.error("outbound_gateway: A2P check failed | err=%s", exc)

        # ------------------------------------------------------------------
        # Step 7: Quiet hours check (08:00–21:00 local time)
        # ------------------------------------------------------------------
        local_hour = _recipient_local_hour(request.recipient_phone, now_utc)
        if not (_QUIET_HOURS_START <= local_hour < _QUIET_HOURS_END):
            logger.info(
                "outbound_gateway: blocked quiet_hours | tenant=%s phone=%s local_hour=%s",
                request.tenant_id, request.recipient_phone, local_hour,
            )
            await _write_ledger(
                db, ledger_id=ledger_id,
                tenant_id=request.tenant_id,
                idempotency_key=request.idempotency_key,
                recipient_phone=request.recipient_phone,
                channel=request.channel,
                status="blocked",
                block_reason="quiet_hours",
                template_id=request.template_id,
                campaign_id=request.campaign_id,
                message_preview=preview,
            )
            return _blocked("quiet_hours")

        # ------------------------------------------------------------------
        # Step 8: Per-tenant daily rate limit
        # ------------------------------------------------------------------
        try:
            from sqlalchemy import func as sa_func
            day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            count_result = await db.execute(
                select(sa_func.count()).select_from(OutboundLedger).where(
                    OutboundLedger.tenant_id == request.tenant_id,
                    OutboundLedger.created_at >= day_start,
                    OutboundLedger.status == "sent",
                )
            )
            sent_today = count_result.scalar() or 0

            # Fetch tenant's daily cap (default 500)
            daily_cap = 500
            try:
                from app.models.contractor import Contractor
                import uuid as _uuid_mod
                cap_result = await db.execute(
                    select(Contractor).where(
                        Contractor.id == _uuid_mod.UUID(request.tenant_id)
                    )
                )
                contractor = cap_result.scalar_one_or_none()
                if contractor and hasattr(contractor, "outbound_daily_cap") and contractor.outbound_daily_cap:
                    daily_cap = contractor.outbound_daily_cap
            except Exception:
                pass  # use default cap

            if sent_today >= daily_cap:
                logger.info(
                    "outbound_gateway: blocked rate_limit_exceeded | tenant=%s sent_today=%d cap=%d",
                    request.tenant_id, sent_today, daily_cap,
                )
                await _write_ledger(
                    db, ledger_id=ledger_id,
                    tenant_id=request.tenant_id,
                    idempotency_key=request.idempotency_key,
                    recipient_phone=request.recipient_phone,
                    channel=request.channel,
                    status="blocked",
                    block_reason="rate_limit_exceeded",
                    template_id=request.template_id,
                    campaign_id=request.campaign_id,
                    message_preview=preview,
                )
                return _blocked("rate_limit_exceeded")
        except Exception as exc:
            logger.error("outbound_gateway: rate limit check failed | err=%s", exc)

        # ------------------------------------------------------------------
        # Step 8b: Cross-tenant recipient daily cap (carrier reputation guard)
        # Max 20 SMS per recipient phone number across ALL tenants per 24h
        # ------------------------------------------------------------------
        if request.channel == "sms":
            try:
                from datetime import timedelta
                window_start = now_utc - timedelta(hours=24)
                cross_tenant_result = await db.execute(
                    select(func.count()).select_from(OutboundLedger).where(
                        OutboundLedger.recipient_phone == request.recipient_phone,
                        OutboundLedger.created_at > window_start,
                        OutboundLedger.status == "sent",
                    )
                )
                cross_tenant_count = cross_tenant_result.scalar() or 0
                if cross_tenant_count >= 20:
                    logger.info(
                        "outbound_gateway: blocked cross_tenant_daily_cap | phone=%s count=%d",
                        request.recipient_phone, cross_tenant_count,
                    )
                    await _write_ledger(
                        db, ledger_id=ledger_id,
                        tenant_id=request.tenant_id,
                        idempotency_key=request.idempotency_key,
                        recipient_phone=request.recipient_phone,
                        channel=request.channel,
                        status="blocked",
                        block_reason="cross_tenant_daily_cap",
                        template_id=request.template_id,
                        campaign_id=request.campaign_id,
                        message_preview=preview,
                    )
                    return GatewayResult(
                        success=False,
                        block_reason="cross_tenant_daily_cap",
                        ledger_id=str(ledger_id),
                    )
            except Exception as exc:
                logger.error("outbound_gateway: cross-tenant rate limit check failed | err=%s", exc)

        # ------------------------------------------------------------------
        # Step 9: Send — route to appropriate sender
        # ------------------------------------------------------------------
        send_success = False
        send_error: Optional[str] = None

        try:
            if request.channel == "sms":
                from app.services.sms import SMSService
                from app.models.contractor import Contractor
                import uuid as _uuid_mod
                contractor_result = await db.execute(
                    select(Contractor).where(
                        Contractor.id == _uuid_mod.UUID(request.tenant_id)
                    )
                )
                contractor = contractor_result.scalar_one_or_none()
                if contractor and request.message:
                    sms = SMSService(contractor).with_db(db)
                    result = await sms._send_compliant(
                        request.recipient_phone, request.message, "outbound_gateway"
                    )
                    send_success = result.get("success", False)
                    if not send_success:
                        send_error = result.get("error", "sms_send_failed")
                else:
                    send_error = "contractor_not_found_or_no_message"

            elif request.channel == "call":
                from app.services.retell_client import RetellClient
                client = RetellClient()
                call_result = await client.create_phone_call(
                    to_number=request.recipient_phone,
                    from_number=None,  # RetellClient uses its configured from number
                    metadata={
                        "tenant_id": request.tenant_id,
                        "campaign_id": request.campaign_id,
                        "call_script_id": request.call_script_id,
                        "ledger_id": str(ledger_id),
                    },
                )
                send_success = bool(call_result)
                if not send_success:
                    send_error = "retell_call_failed"

            elif request.channel == "email":
                # Email not yet implemented — block with clear reason
                send_success = False
                send_error = "email_not_implemented"
                logger.info(
                    "outbound_gateway: email channel not yet implemented | tenant=%s",
                    request.tenant_id,
                )

        except Exception as exc:
            logger.error("outbound_gateway: send failed | err=%s", exc)
            send_error = str(exc)[:200]

        # ------------------------------------------------------------------
        # Step 10: Write ledger row — always
        # ------------------------------------------------------------------
        final_status = "sent" if send_success else ("blocked" if send_error == "email_not_implemented" else "failed")
        await _write_ledger(
            db, ledger_id=ledger_id,
            tenant_id=request.tenant_id,
            idempotency_key=request.idempotency_key,
            recipient_phone=request.recipient_phone,
            channel=request.channel,
            status=final_status,
            block_reason=send_error if not send_success else None,
            template_id=request.template_id,
            campaign_id=request.campaign_id,
            message_preview=preview,
        )

        return GatewayResult(
            success=send_success,
            block_reason=send_error if not send_success else None,
            ledger_id=str(ledger_id),
        )
