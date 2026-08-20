"""
BilingualService — Phase 6 Full EN/FR Bilingual Outbound.

Feature flag: french_bilingual (default OFF).
Flag OFF → existing hardcoded English strings used everywhere (zero change).
Flag ON → localized templates returned from this service.

Quebec French (QC French):
- "vous" (formal) for customer-facing templates
- "tu" for owner-facing templates (owner alert SMS)
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contractor import Contractor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SMS Templates — EN and FR (QC French)
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, str]] = {
    "missed_call_textback": {
        "en": (
            "Hi! You recently called {company_name} and we missed you. "
            "We'd love to help — reply here or call us back anytime."
        ),
        "fr": (
            "Bonjour! Vous avez récemment appelé {company_name} et nous avons manqué votre appel. "
            "Nous aimerions vous aider — répondez ici ou rappelez-nous en tout temps."
        ),
    },
    "appointment_confirmation": {
        "en": (
            "Your appointment with {company_name} is confirmed for {appointment_time} "
            "at {service_address}. Reply STOP to opt out."
        ),
        "fr": (
            "Votre rendez-vous avec {company_name} est confirmé pour le {appointment_time} "
            "à {service_address}. Répondez STOP pour vous désabonner."
        ),
    },
    "appointment_reminder": {
        "en": (
            "Reminder: Your appointment with {company_name} is tomorrow, {appointment_time}. "
            "Questions? Reply here."
        ),
        "fr": (
            "Rappel : Votre rendez-vous avec {company_name} est demain, le {appointment_time}. "
            "Des questions? Répondez ici."
        ),
    },
    "estimate_followup_day2": {
        "en": (
            "Hi! {company_name} here — just checking in on the estimate we sent you. "
            "Ready to move forward or have questions? Reply anytime."
        ),
        "fr": (
            "Bonjour! C'est {company_name} — nous voulions simplement faire le suivi de la soumission "
            "que nous vous avons envoyée. Prêt(e) à aller de l'avant ou avez-vous des questions? "
            "Répondez en tout temps."
        ),
    },
    "estimate_followup_day10": {
        "en": (
            "{company_name}: Your estimate is still available. "
            "We're here when you're ready — reply or call us."
        ),
        "fr": (
            "{company_name} : Votre soumission est toujours disponible. "
            "Nous sommes là quand vous êtes prêt(e) — répondez ou appelez-nous."
        ),
    },
    "reactivation_seasonal_hvac_fall": {
        "en": (
            "Fall is here! {company_name} recommends scheduling your furnace tune-up before the cold hits. "
            "Reply YES to book or call us."
        ),
        "fr": (
            "L'automne est arrivé! {company_name} vous recommande de planifier l'entretien de votre fournaise "
            "avant le froid. Répondez OUI pour réserver ou appelez-nous."
        ),
    },
    "reactivation_seasonal_hvac_spring": {
        "en": (
            "Spring is here! Time to schedule your AC tune-up. "
            "{company_name} has openings now — reply YES to book."
        ),
        "fr": (
            "Le printemps est arrivé! C'est le moment de planifier l'entretien de votre climatiseur. "
            "{company_name} a des disponibilités — répondez OUI pour réserver."
        ),
    },
    "surge_mode_owner_alert": {
        # Owner-facing: "tu" form (informal, owner relationship)
        "en": (
            "SURGE ALERT — {company_name}: Surge mode activated ({surge_type}). "
            "Expires at {expires_at}. Current bookings today: {booked_count}. "
            "Reply SURGE OFF to deactivate."
        ),
        "fr": (
            "ALERTE POINTE — {company_name} : Mode pointe activé ({surge_type}). "
            "Expire à {expires_at}. Réservations aujourd'hui : {booked_count}. "
            "Réponds POINTE OFF pour désactiver."
        ),
    },
    "surge_mode_deactivated": {
        "en": "Surge mode deactivated for {company_name}. Normal operations resumed.",
        "fr": "Mode pointe désactivé pour {company_name}. Opérations normales reprises.",
    },
}


class BilingualService:

    def get_language_preference(
        self, contractor: Contractor, recipient_phone: str
    ) -> str:
        """
        Returns "fr" or "en".
        Caller should first check contact_language_preference table via
        get_stored_language_preference(); this method provides the contractor default fallback.
        """
        default = getattr(contractor, "default_language", "en") or "en"
        return default if default in ("en", "fr") else "en"

    async def get_stored_language_preference(
        self, contractor: Contractor, recipient_phone: str, db: AsyncSession
    ) -> str:
        """
        Check contact_language_preferences table for this phone.
        Fallback to contractor.default_language.
        """
        try:
            from sqlalchemy import select
            from app.models.contact_language_preference import ContactLanguagePreference

            result = await db.execute(
                select(ContactLanguagePreference).where(
                    ContactLanguagePreference.tenant_id == contractor.id,
                    ContactLanguagePreference.phone_number == recipient_phone,
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return row.language if row.language in ("en", "fr") else "en"
        except Exception as exc:
            logger.warning(
                "bilingual: language preference lookup failed | tenant=%s phone=%s err=%s",
                contractor.id, recipient_phone, exc,
            )
        return self.get_language_preference(contractor, recipient_phone)

    def get_sms_template(self, template_id: str, language: str) -> str:
        """
        Returns localized SMS template string.
        Falls back to "en" if FR variant is missing.
        Caller uses .format(**kwargs) to fill placeholders.
        """
        lang = language if language in ("en", "fr") else "en"
        template_variants = _TEMPLATES.get(template_id)
        if template_variants is None:
            logger.warning("bilingual: unknown template_id=%s — returning empty string", template_id)
            return ""
        result = template_variants.get(lang)
        if result is None:
            # Fallback to English
            logger.debug(
                "bilingual: FR variant missing for template_id=%s — falling back to EN", template_id
            )
            result = template_variants.get("en", "")
        return result

    async def record_language_preference(
        self,
        phone: str,
        language: str,
        tenant_id: str,
        db: AsyncSession,
    ) -> None:
        """Upsert into contact_language_preferences table."""
        if language not in ("en", "fr"):
            logger.warning("bilingual: invalid language=%s for phone=%s", language, phone)
            return

        try:
            from app.models.contact_language_preference import ContactLanguagePreference
            from sqlalchemy import select

            result = await db.execute(
                select(ContactLanguagePreference).where(
                    ContactLanguagePreference.tenant_id == uuid.UUID(tenant_id),
                    ContactLanguagePreference.phone_number == phone,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = ContactLanguagePreference(
                    id=uuid.uuid4(),
                    tenant_id=uuid.UUID(tenant_id),
                    phone_number=phone,
                    language=language,
                    source="detected",
                )
                db.add(row)
            else:
                row.language = language
                row.source = "detected"

            await db.flush()
            logger.debug("bilingual: language pref recorded | phone=%s lang=%s tenant=%s", phone, language, tenant_id)
        except Exception as exc:
            logger.warning(
                "bilingual: record_language_preference failed | phone=%s err=%s", phone, exc
            )
