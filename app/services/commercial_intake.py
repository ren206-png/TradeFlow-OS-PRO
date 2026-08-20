"""
CommercialIntakeService — Phase 6 Commercial/Mechanical Intake Mode.

Feature flag: commercial_intake (default OFF).
Flag OFF → zero change to existing flow.
Flag ON → additional intake fields collected for commercial_mechanical tenants.

Safety contract:
- Service agreement match confidence must be >= 80 to trigger acknowledgment (confirmatory only).
- Acknowledgment is always confirmatory ("Are you calling about service for [company]?"),
  never assertive.
- Pydantic extra="forbid" on all schemas.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contractor import Contractor

logger = logging.getLogger(__name__)

_SERVICE_AGREEMENT_CONFIDENCE_THRESHOLD = 80


# ---------------------------------------------------------------------------
# Pydantic schemas — extra="forbid"
# ---------------------------------------------------------------------------

class IntakeField(BaseModel):
    model_config = {"extra": "forbid"}

    field_name: str
    label: str
    required: bool
    field_type: str  # text|phone|optional_text


class ServiceAgreementMatchResult(BaseModel):
    model_config = {"extra": "forbid"}

    is_match: bool
    match_confidence: int  # 0-100; must be >= 80 to acknowledge
    agreement_id: Optional[str] = None
    company_name: Optional[str] = None
    agreement_number: Optional[str] = None
    priority_routing: bool = False

    @field_validator("match_confidence")
    @classmethod
    def validate_confidence(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError(f"match_confidence must be 0-100, got {v}")
        return v


class PipefieldPayload(BaseModel):
    model_config = {"extra": "forbid"}

    job_site: dict
    equipment: dict
    service_agreement: dict
    contacts: dict
    urgency: str


class CommercialIntakeInput(BaseModel):
    """Pydantic input model for collect_commercial_intake tool. extra=forbid."""
    model_config = {"extra": "forbid"}

    site_contact_name: Optional[str] = None
    site_contact_phone: Optional[str] = None
    account_contact_name: Optional[str] = None
    po_number: Optional[str] = None
    building_id: Optional[str] = None
    unit_id: Optional[str] = None
    equipment_tag_id: Optional[str] = None
    caller_company: Optional[str] = None
    service_address: Optional[str] = None
    urgency: Optional[str] = None


# ---------------------------------------------------------------------------
# Fuzzy match (no external library dependency)
# ---------------------------------------------------------------------------

def _simple_fuzzy_score(a: str, b: str) -> int:
    """
    Simple token-based fuzzy similarity score (0-100).
    Counts token overlap / max token count.
    Sufficient for company name matching (avoids adding fuzzywuzzy/rapidfuzz dep).
    """
    if not a or not b:
        return 0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0
    intersection = tokens_a & tokens_b
    score = int(len(intersection) / max(len(tokens_a), len(tokens_b)) * 100)
    return score


class CommercialIntakeService:

    async def get_intake_fields(self, contractor: Contractor) -> list[IntakeField]:
        """
        Return the intake fields for commercial_mechanical tenants.
        Standard residential tenants get an empty list (no commercial fields).
        """
        tenant_type = getattr(contractor, "tenant_type", "residential") or "residential"
        if tenant_type != "commercial_mechanical":
            return []

        return [
            IntakeField(
                field_name="site_contact_name",
                label="Site Contact Name",
                required=True,
                field_type="text",
            ),
            IntakeField(
                field_name="site_contact_phone",
                label="Site Contact Phone",
                required=True,
                field_type="phone",
            ),
            IntakeField(
                field_name="account_contact_name",
                label="Billing / Account Contact Name",
                required=False,
                field_type="text",
            ),
            IntakeField(
                field_name="po_number",
                label="PO Number",
                required=False,
                field_type="optional_text",
            ),
            IntakeField(
                field_name="building_id",
                label="Building ID",
                required=False,
                field_type="optional_text",
            ),
            IntakeField(
                field_name="unit_id",
                label="Unit ID",
                required=False,
                field_type="optional_text",
            ),
            IntakeField(
                field_name="equipment_tag_id",
                label="Equipment Tag ID",
                required=False,
                field_type="optional_text",
            ),
            IntakeField(
                field_name="service_agreement_id",
                label="Service Agreement Number",
                required=False,
                field_type="optional_text",
            ),
        ]

    async def check_service_agreement(
        self,
        caller_company: str,
        tenant_id: UUID,
        db: AsyncSession,
    ) -> tuple[bool, Optional[ServiceAgreementMatchResult]]:
        """
        Match caller's company name against service_agreements table.
        Returns (is_match, ServiceAgreementMatchResult).

        Confidence threshold: fuzzy match score >= 80.
        If match: AI acknowledges "Are you calling about service for [company]?" — confirmatory only.
        If no match: return (False, None) — proceed as standard intake.
        """
        if not caller_company or not caller_company.strip():
            return False, None

        try:
            from app.models.service_agreement import ServiceAgreement

            result = await db.execute(
                select(ServiceAgreement).where(
                    ServiceAgreement.tenant_id == tenant_id,
                    ServiceAgreement.is_active == True,  # noqa: E712
                )
            )
            agreements = result.scalars().all()
        except Exception as exc:
            logger.warning(
                "commercial_intake: service agreement query failed | tenant=%s err=%s",
                tenant_id, exc,
            )
            return False, None

        best_score = 0
        best_agreement = None

        for agreement in agreements:
            score = _simple_fuzzy_score(caller_company, agreement.company_name)
            if score > best_score:
                best_score = score
                best_agreement = agreement

        if best_score < _SERVICE_AGREEMENT_CONFIDENCE_THRESHOLD or best_agreement is None:
            logger.debug(
                "commercial_intake: no agreement match | caller=%s best_score=%d tenant=%s",
                caller_company, best_score, tenant_id,
            )
            return False, None

        # Pydantic-validate confidence before returning
        try:
            match_result = ServiceAgreementMatchResult(
                is_match=True,
                match_confidence=best_score,
                agreement_id=str(best_agreement.id),
                company_name=best_agreement.company_name,
                agreement_number=best_agreement.agreement_number,
                priority_routing=best_agreement.priority_routing,
            )
        except Exception as exc:
            logger.warning(
                "commercial_intake: match result validation failed | tenant=%s err=%s",
                tenant_id, exc,
            )
            return False, None

        logger.info(
            "commercial_intake: agreement matched | caller=%s matched=%s score=%d tenant=%s",
            caller_company, best_agreement.company_name, best_score, tenant_id,
        )
        return True, match_result

    def get_confirmatory_acknowledgment(self, match_result: ServiceAgreementMatchResult) -> Optional[str]:
        """
        Returns confirmatory (never assertive) acknowledgment string.
        Only if confidence >= threshold.
        Always a question ("Are you calling about service for [company]?").
        """
        if not match_result.is_match:
            return None
        if match_result.match_confidence < _SERVICE_AGREEMENT_CONFIDENCE_THRESHOLD:
            return None
        if not match_result.company_name:
            return None
        # Confirmatory question — never asserts, always asks
        return f"Are you calling about service for {match_result.company_name}?"

    def build_pipefield_handoff_payload(
        self,
        lead_id: str,
        intake_data: dict,
        match_result: Optional[ServiceAgreementMatchResult] = None,
    ) -> dict:
        """
        Build structured JSON payload shaped for future PipeField OS handoff.
        Integration NOT built yet — returns documented schema dict.

        Keys: job_site, equipment, service_agreement, contacts, urgency.
        """
        return {
            "job_site": {
                "address": intake_data.get("service_address"),
                "building_id": intake_data.get("building_id"),
                "unit_id": intake_data.get("unit_id"),
            },
            "equipment": {
                "equipment_tag_id": intake_data.get("equipment_tag_id"),
            },
            "service_agreement": {
                "agreement_id": match_result.agreement_id if match_result else None,
                "agreement_number": match_result.agreement_number if match_result else None,
                "company_name": match_result.company_name if match_result else intake_data.get("caller_company"),
                "po_number": intake_data.get("po_number"),
                "priority_routing": match_result.priority_routing if match_result else False,
            },
            "contacts": {
                "site_contact_name": intake_data.get("site_contact_name"),
                "site_contact_phone": intake_data.get("site_contact_phone"),
                "account_contact_name": intake_data.get("account_contact_name"),
            },
            "urgency": intake_data.get("urgency", "standard"),
            "_lead_id": lead_id,
            "_schema_version": "1.0",
            "_integration_status": "pending",
        }

    async def save_intake(
        self,
        lead_id: uuid.UUID,
        tenant_id: uuid.UUID,
        intake_data: dict,
        match_result: Optional[ServiceAgreementMatchResult],
        db: AsyncSession,
    ) -> None:
        """Persist CommercialIntake record to DB."""
        try:
            from app.models.commercial_intake import CommercialIntake

            pipefield_payload = self.build_pipefield_handoff_payload(
                str(lead_id), intake_data, match_result
            )

            record = CommercialIntake(
                id=uuid.uuid4(),
                lead_id=lead_id,
                tenant_id=tenant_id,
                site_contact_name=intake_data.get("site_contact_name"),
                site_contact_phone=intake_data.get("site_contact_phone"),
                account_contact_name=intake_data.get("account_contact_name"),
                po_number=intake_data.get("po_number"),
                building_id=intake_data.get("building_id"),
                unit_id=intake_data.get("unit_id"),
                equipment_tag_id=intake_data.get("equipment_tag_id"),
                service_agreement_id=(
                    uuid.UUID(match_result.agreement_id)
                    if match_result and match_result.agreement_id
                    else None
                ),
                match_confidence=(
                    match_result.match_confidence if match_result else None
                ),
                pipefield_payload=pipefield_payload,
            )
            db.add(record)
            await db.flush()
        except Exception as exc:
            logger.warning(
                "commercial_intake: save_intake failed (non-fatal) | lead=%s err=%s",
                lead_id, exc,
            )
