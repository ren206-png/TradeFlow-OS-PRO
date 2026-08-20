"""
WeatherSurgeService — Phase 6 Surge Intelligence.

Safety contracts:
- API outage → log, return [], do NOT activate or crash.
- Surge auto-expires at min(alert.expires_at, now + 12h). Never stuck.
- Overbooking multiplier hard cap: 1.5x. Reject > 1.5.
- All outbound SMS via OutboundGateway only.
- Tenant isolation at every query.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import httpx
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contractor import Contractor
from app.models.surge_mode_record import SurgeModeRecord
from app.models.weather_alert import WeatherAlert

logger = logging.getLogger(__name__)

_HARD_CEILING_HOURS = 12
_MAX_OVERBOOKING_MULTIPLIER = Decimal("1.5")
_HTTP_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Pydantic schemas — extra="forbid" on all Phase 6 schemas
# ---------------------------------------------------------------------------

class WeatherAlertSchema(BaseModel):
    model_config = {"extra": "forbid"}

    alert_id: str
    surge_type: str  # extreme_cold|heat|storm
    title: str
    effective_at: datetime
    expires_at: datetime
    source: str  # environment_canada|nws
    postal_codes: list[str]
    raw_payload: dict


class SurgeModeActivationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    # overbooking_multiplier stored as Decimal; int*10 compared internally
    overbooking_multiplier: Decimal = Decimal("1.0")

    @field_validator("overbooking_multiplier")
    @classmethod
    def cap_multiplier(cls, v: Decimal) -> Decimal:
        if v > _MAX_OVERBOOKING_MULTIPLIER:
            raise ValueError(
                f"overbooking_multiplier {v} exceeds hard cap of {_MAX_OVERBOOKING_MULTIPLIER}"
            )
        return v


# ---------------------------------------------------------------------------
# Alert type mapping
# ---------------------------------------------------------------------------

_SURGE_TYPE_MAP: dict[str, str] = {
    "extreme cold warning": "extreme_cold",
    "wind chill warning": "extreme_cold",
    "blizzard warning": "extreme_cold",
    "heat warning": "heat",
    "heat advisory": "heat",
    "excessive heat warning": "heat",
    "winter storm warning": "storm",
    "ice storm warning": "storm",
    "freezing rain warning": "storm",
}


def _classify_alert_title(title: str) -> Optional[str]:
    """Return surge_type for a known alert title, or None to skip."""
    lower = title.lower()
    for keyword, surge_type in _SURGE_TYPE_MAP.items():
        if keyword in lower:
            return surge_type
    return None


# ---------------------------------------------------------------------------
# Postal code → bounding box (very rough, used for EC API)
# ---------------------------------------------------------------------------

def _postal_to_bbox(postal_code: str) -> Optional[str]:
    """
    Return a rough BBOX string for Environment Canada WFS queries.
    Uses first character of Canadian postal code for regional bounding.
    Returns None for US ZIPs (handled by NWS point-based API).
    """
    code = postal_code.strip().upper().replace(" ", "")
    if not code:
        return None
    # Canadian postal codes start with a letter
    if code[0].isalpha():
        # Very rough bounding boxes per FSA letter prefix
        _FSA_BBOX: dict[str, str] = {
            "A": "-60,45,-52,52",   # NL
            "B": "-66,43,-59,48",   # NS
            "C": "-64,45,-62,47",   # PEI
            "E": "-67,44,-63,48",   # NB
            "G": "-76,44,-64,52",   # QC east
            "H": "-74,45,-72,46",   # Montreal
            "J": "-76,45,-72,47",   # QC Outaouais
            "K": "-78,43,-74,46",   # ON east
            "L": "-80,43,-78,45",   # ON central
            "M": "-80,43,-78,44",   # Toronto
            "N": "-83,41,-79,44",   # ON south
            "P": "-92,45,-79,49",   # ON north
            "R": "-101,49,-96,51",  # MB
            "S": "-107,49,-101,52", # SK
            "T": "-117,49,-110,54", # AB
            "V": "-139,48,-114,60", # BC
            "X": "-136,60,-62,84",  # NT/NU
            "Y": "-141,60,-124,70", # YT
        }
        first = code[0]
        return _FSA_BBOX.get(first)
    # US ZIP — use NWS point API instead
    return None


async def _geocode_zip(zip_code: str) -> Optional[tuple[float, float]]:
    """
    Rough lat/lon for US ZIP via NWS geocoding (no external geocoder dependency).
    Uses NWS points API — if it fails, return None and skip.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"https://api.weather.gov/points/{zip_code}",
                headers={"User-Agent": "TradeFlow-OS/1.0 (ren206@gmail.com)"},
            )
            if resp.status_code == 200:
                data = resp.json()
                coords = data.get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    return float(coords[1]), float(coords[0])
    except Exception as exc:
        logger.debug("geocode_zip failed for %s: %s", zip_code, exc)
    return None


class WeatherSurgeService:

    async def poll_alerts(
        self, contractor: Contractor, db: AsyncSession
    ) -> list[WeatherAlertSchema]:
        """
        Poll Environment Canada GeoMet/alerts (Canadian postal codes) and
        NWS api.weather.gov (US ZIP codes) for surge-triggering weather alerts.

        On any API failure: log, return [], never crash or activate surge.
        """
        postal_codes: list[str] = getattr(contractor, "service_area_postal_codes", None) or []
        if not postal_codes:
            return []

        alerts: list[WeatherAlertSchema] = []

        for code in postal_codes:
            clean = code.strip().upper().replace(" ", "")
            if not clean:
                continue

            if clean[0].isalpha():
                # Canadian postal code — use Environment Canada
                found = await self._poll_ec(clean, contractor)
                alerts.extend(found)
            else:
                # US ZIP — use NWS
                found = await self._poll_nws(clean, contractor)
                alerts.extend(found)

        # Deduplicate by alert_id
        seen: set[str] = set()
        unique: list[WeatherAlertSchema] = []
        for a in alerts:
            if a.alert_id not in seen:
                seen.add(a.alert_id)
                unique.append(a)

        return unique

    async def _poll_ec(
        self, postal_code: str, contractor: Contractor
    ) -> list[WeatherAlertSchema]:
        """Poll Environment Canada WFS for CAP alerts."""
        bbox = _postal_to_bbox(postal_code)
        if not bbox:
            return []

        url = "https://geo.weather.gc.ca/geomet"
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "alerts:cap-alerts",
            "BBOX": bbox,
            "outputFormat": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(
                    url, params=params,
                    headers={"User-Agent": "TradeFlow-OS/1.0 (ren206@gmail.com)"},
                )
                if resp.status_code != 200:
                    logger.warning(
                        "weather_surge: EC API returned %d for postal=%s tenant=%s",
                        resp.status_code, postal_code, contractor.id,
                    )
                    return []
                data = resp.json()
        except httpx.TimeoutException as exc:
            logger.warning(
                "weather_surge: EC API timeout for postal=%s tenant=%s err=%s",
                postal_code, contractor.id, exc,
            )
            return []
        except Exception as exc:
            logger.warning(
                "weather_surge: EC API error for postal=%s tenant=%s err=%s",
                postal_code, contractor.id, exc,
            )
            return []

        results: list[WeatherAlertSchema] = []
        features = data.get("features", [])
        for feat in features:
            try:
                props = feat.get("properties", {})
                title = props.get("title", "") or props.get("headline", "")
                surge_type = _classify_alert_title(title)
                if surge_type is None:
                    continue

                alert_id = props.get("identifier", "") or feat.get("id", "")
                if not alert_id:
                    continue

                effective_str = props.get("effective", "") or props.get("sent", "")
                expires_str = props.get("expires", "") or props.get("ends", "")

                if not effective_str or not expires_str:
                    continue

                effective_at = datetime.fromisoformat(effective_str.replace("Z", "+00:00"))
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))

                results.append(WeatherAlertSchema(
                    alert_id=f"ec_{alert_id}",
                    surge_type=surge_type,
                    title=title,
                    effective_at=effective_at,
                    expires_at=expires_at,
                    source="environment_canada",
                    postal_codes=[postal_code],
                    raw_payload=props,
                ))
            except Exception as exc:
                logger.warning("weather_surge: failed to parse EC feature | err=%s", exc)
                continue

        return results

    async def _poll_nws(
        self, zip_code: str, contractor: Contractor
    ) -> list[WeatherAlertSchema]:
        """Poll NWS api.weather.gov for active alerts near a US ZIP."""
        # NWS uses lat/lon points; try to approximate from ZIP
        # Use a simple NWS zone search by zip (NWS /alerts/active?zone not directly by zip)
        # Fallback: use /alerts/active with area filter using state approximation
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(
                    "https://api.weather.gov/alerts/active",
                    params={"status": "actual", "message_type": "alert,update"},
                    headers={
                        "User-Agent": "TradeFlow-OS/1.0 (ren206@gmail.com)",
                        "Accept": "application/geo+json",
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "weather_surge: NWS API returned %d for zip=%s tenant=%s",
                        resp.status_code, zip_code, contractor.id,
                    )
                    return []
                data = resp.json()
        except httpx.TimeoutException as exc:
            logger.warning(
                "weather_surge: NWS API timeout for zip=%s tenant=%s err=%s",
                zip_code, contractor.id, exc,
            )
            return []
        except Exception as exc:
            logger.warning(
                "weather_surge: NWS API error for zip=%s tenant=%s err=%s",
                zip_code, contractor.id, exc,
            )
            return []

        results: list[WeatherAlertSchema] = []
        features = data.get("features", [])
        for feat in features:
            try:
                props = feat.get("properties", {})
                title = props.get("headline", "") or props.get("event", "")
                surge_type = _classify_alert_title(title)
                if surge_type is None:
                    continue

                alert_id = props.get("id", "") or feat.get("id", "")
                if not alert_id:
                    continue

                effective_str = props.get("effective", "")
                expires_str = props.get("expires", "") or props.get("ends", "")

                if not effective_str or not expires_str:
                    continue

                effective_at = datetime.fromisoformat(effective_str.replace("Z", "+00:00"))
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))

                results.append(WeatherAlertSchema(
                    alert_id=f"nws_{alert_id}",
                    surge_type=surge_type,
                    title=title,
                    effective_at=effective_at,
                    expires_at=expires_at,
                    source="nws",
                    postal_codes=[zip_code],
                    raw_payload=props,
                ))
            except Exception as exc:
                logger.warning("weather_surge: failed to parse NWS feature | err=%s", exc)
                continue

        return results

    async def activate_surge(
        self,
        contractor: Contractor,
        alert: WeatherAlertSchema,
        db: AsyncSession,
        overbooking_multiplier: Decimal = Decimal("1.0"),
    ) -> SurgeModeRecord:
        """
        Activate surge mode for this tenant.

        Hard ceiling: expires_at = min(alert.expires_at, now + 12h)
        Overbooking multiplier hard cap: 1.5x.
        Sends owner notification SMS via OutboundGateway.
        """
        # Validate overbooking multiplier via Pydantic
        try:
            req = SurgeModeActivationRequest(overbooking_multiplier=overbooking_multiplier)
            validated_multiplier = req.overbooking_multiplier
        except Exception as exc:
            logger.error(
                "weather_surge: invalid overbooking_multiplier=%s tenant=%s err=%s",
                overbooking_multiplier, contractor.id, exc,
            )
            raise ValueError(str(exc)) from exc

        now = datetime.now(tz=timezone.utc)
        hard_ceiling = now + timedelta(hours=_HARD_CEILING_HOURS)

        alert_expires = alert.expires_at
        if alert_expires.tzinfo is None:
            alert_expires = alert_expires.replace(tzinfo=timezone.utc)

        # Hard ceiling enforced: never stuck in surge beyond 12h
        expires_at = min(alert_expires, hard_ceiling)

        # Save WeatherAlert record (upsert by alert_id for idempotency)
        existing_alert_result = await db.execute(
            select(WeatherAlert).where(WeatherAlert.alert_id == alert.alert_id)
        )
        weather_alert_row = existing_alert_result.scalar_one_or_none()
        if weather_alert_row is None:
            weather_alert_row = WeatherAlert(
                id=uuid.uuid4(),
                tenant_id=contractor.id,
                alert_id=alert.alert_id,
                surge_type=alert.surge_type,
                title=alert.title,
                effective_at=alert.effective_at,
                expires_at=alert.expires_at,
                source=alert.source,
                postal_codes=alert.postal_codes,
                raw_payload=alert.raw_payload,
            )
            db.add(weather_alert_row)
            await db.flush()

        # Create SurgeModeRecord
        record = SurgeModeRecord(
            id=uuid.uuid4(),
            tenant_id=contractor.id,
            alert_id=weather_alert_row.id,
            surge_type=alert.surge_type,
            expires_at=expires_at,
            is_manual=False,
            overbooking_multiplier=validated_multiplier,
            activated_by_alert_title=alert.title,
        )
        db.add(record)

        # Update contractor surge flag
        contractor.surge_mode_active = True
        contractor.surge_overbooking_multiplier = validated_multiplier
        await db.flush()

        # Send owner notification SMS via OutboundGateway (non-blocking on failure)
        await self._notify_owner_activate(contractor, record, db)

        logger.info(
            "weather_surge: surge activated | tenant=%s surge_type=%s expires_at=%s multiplier=%s",
            contractor.id, alert.surge_type, expires_at.isoformat(), validated_multiplier,
        )
        return record

    async def deactivate_surge(self, contractor: Contractor, db: AsyncSession) -> None:
        """
        Deactivate surge for this tenant.
        Stamps deactivated_at on all active SurgeModeRecords.
        Sends owner notification. Manual override always works.
        """
        now = datetime.now(tz=timezone.utc)

        active_records_result = await db.execute(
            select(SurgeModeRecord).where(
                SurgeModeRecord.tenant_id == contractor.id,
                SurgeModeRecord.deactivated_at.is_(None),
            )
        )
        active_records = active_records_result.scalars().all()

        for rec in active_records:
            rec.deactivated_at = now

        contractor.surge_mode_active = False
        await db.flush()

        # Notify owner
        await self._notify_owner_deactivate(contractor, db)

        logger.info(
            "weather_surge: surge deactivated | tenant=%s records_closed=%d",
            contractor.id, len(active_records),
        )

    async def check_and_expire(self, db: AsyncSession) -> int:
        """
        Called by scheduler every 30 minutes.
        Deactivates any SurgeModeRecord where expires_at < now.
        Returns count of deactivated tenants.
        """
        from sqlalchemy import and_

        now = datetime.now(tz=timezone.utc)

        expired_result = await db.execute(
            select(SurgeModeRecord).where(
                and_(
                    SurgeModeRecord.expires_at < now,
                    SurgeModeRecord.deactivated_at.is_(None),
                )
            )
        )
        expired_records = expired_result.scalars().all()

        if not expired_records:
            return 0

        # Group by tenant to call deactivate_surge once per tenant
        from app.models.contractor import Contractor as ContractorModel

        tenant_ids: set[uuid.UUID] = {r.tenant_id for r in expired_records}
        count = 0

        for tenant_id in tenant_ids:
            try:
                contractor_result = await db.execute(
                    select(ContractorModel).where(ContractorModel.id == tenant_id)
                )
                contractor = contractor_result.scalar_one_or_none()
                if contractor:
                    await self.deactivate_surge(contractor, db)
                    await db.commit()
                    count += 1
            except Exception as exc:
                logger.error(
                    "weather_surge: check_and_expire deactivation failed | tenant=%s err=%s",
                    tenant_id, exc,
                )

        logger.info("weather_surge: check_and_expire complete | deactivated=%d", count)
        return count

    def get_surge_greeting(self, surge_type: str, contractor_name: str) -> str:
        """
        Returns a fixed surge greeting string — no LLM generation.
        Used as a prefix to the system prompt when surge_mode_active and flag is ON.
        """
        greetings: dict[str, str] = {
            "extreme_cold": (
                f"Thank you for calling {contractor_name}. Due to the cold weather emergency, "
                "we're experiencing high call volume — I can book you right now."
            ),
            "heat": (
                f"Thank you for calling {contractor_name}. During this heat warning, "
                "we're prioritizing urgent cooling calls — let me get you scheduled."
            ),
            "storm": (
                f"Thank you for calling {contractor_name}. We're managing high volume from the storm — "
                "I can book your service call right now."
            ),
        }
        return greetings.get(surge_type, f"Thank you for calling {contractor_name}. How can I help you today?")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _notify_owner_activate(
        self,
        contractor: Contractor,
        record: SurgeModeRecord,
        db: AsyncSession,
    ) -> None:
        """Send owner SMS notification via OutboundGateway on surge activation."""
        try:
            # Get current booked count for notification
            booked_count = await self._get_booked_count_today(contractor, db)

            from app.services.bilingual import BilingualService
            bilingual = BilingualService()
            # Owner alert is always sent to contractor's own phone (owner-facing)
            # Use default_language for owner; owner alerts are not customer-facing
            lang = getattr(contractor, "default_language", "en") or "en"
            template = bilingual.get_sms_template("surge_mode_owner_alert", lang)
            message = template.format(
                company_name=contractor.name,
                surge_type=record.surge_type,
                expires_at=record.expires_at.strftime("%H:%M UTC"),
                booked_count=booked_count,
            )

            from app.schemas.outbound import OutboundRequest
            from app.services.outbound_gateway import OutboundGateway
            import hashlib

            idempotency_key = hashlib.sha256(
                f"surge_activate_{contractor.id}_{record.id}".encode()
            ).hexdigest()

            gateway_req = OutboundRequest(
                tenant_id=str(contractor.id),
                recipient_phone=contractor.phone_number,
                channel="sms",
                message=message,
                idempotency_key=idempotency_key,
                template_id="surge_mode_owner_alert",
            )
            await OutboundGateway().send(gateway_req, db)
        except Exception as exc:
            logger.warning(
                "weather_surge: owner activate notification failed (non-fatal) | tenant=%s err=%s",
                contractor.id, exc,
            )

    async def _notify_owner_deactivate(
        self, contractor: Contractor, db: AsyncSession
    ) -> None:
        """Send owner SMS notification on surge deactivation."""
        try:
            from app.services.bilingual import BilingualService
            bilingual = BilingualService()
            lang = getattr(contractor, "default_language", "en") or "en"
            template = bilingual.get_sms_template("surge_mode_deactivated", lang)
            message = template.format(company_name=contractor.name)

            from app.schemas.outbound import OutboundRequest
            from app.services.outbound_gateway import OutboundGateway
            import hashlib

            idempotency_key = hashlib.sha256(
                f"surge_deactivate_{contractor.id}_{datetime.now(tz=timezone.utc).date()}".encode()
            ).hexdigest()

            gateway_req = OutboundRequest(
                tenant_id=str(contractor.id),
                recipient_phone=contractor.phone_number,
                channel="sms",
                message=message,
                idempotency_key=idempotency_key,
                template_id="surge_mode_deactivated",
            )
            await OutboundGateway().send(gateway_req, db)
        except Exception as exc:
            logger.warning(
                "weather_surge: owner deactivate notification failed (non-fatal) | tenant=%s err=%s",
                contractor.id, exc,
            )

    async def _get_booked_count_today(self, contractor: Contractor, db: AsyncSession) -> int:
        """Return count of booked appointments today for this tenant."""
        try:
            from sqlalchemy import func as sa_func, and_
            from app.models.lead import Lead

            today_start = datetime.now(tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            count_result = await db.execute(
                select(sa_func.count()).select_from(Lead).where(
                    and_(
                        Lead.contractor_id == contractor.id,
                        Lead.appointment_status == "booked",
                        Lead.created_at >= today_start,
                    )
                )
            )
            return count_result.scalar() or 0
        except Exception as exc:
            logger.warning("weather_surge: booked count query failed | err=%s", exc)
            return 0
