from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from app.models.contractor import Contractor
from app.prompts.master_prompt import MASTER_PROMPT_TEMPLATE
from app.prompts.multilang_wrapper import apply_language_directive
from app.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def build_system_prompt(contractor: Contractor, intake_section: str = "") -> str:
    """Build a personalized system prompt for a contractor by filling template variables."""
    required = {
        "name": contractor.name,
        "agent_name": contractor.agent_name,
        "service_areas": contractor.service_areas,
        "trades": contractor.trades,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(f"Contractor is missing required fields: {', '.join(missing)}")

    diagnostic_fee_clause = (
        f"A ${contractor.diagnostic_fee:.0f} diagnostic fee applies to all service calls."
        if contractor.diagnostic_fee
        else "No diagnostic fee applies."
    )

    free_estimate_clause = (
        "Free estimates are available for replacement and installation projects."
        if contractor.free_estimate
        else ""
    )

    service_area_str = ", ".join(contractor.service_areas) if contractor.service_areas else "our local service area"
    trades_str = ", ".join(t.title() for t in contractor.trades) if contractor.trades else "general trades"
    review_link = contractor.review_link or ""

    base = MASTER_PROMPT_TEMPLATE.format(
        AGENT_NAME=contractor.agent_name,
        COMPANY_NAME=contractor.name,
        SERVICE_AREA=service_area_str,
        SUPPORTED_TRADES=trades_str,
        DIAGNOSTIC_FEE_CLAUSE=diagnostic_fee_clause,
        FREE_ESTIMATE_CLAUSE=free_estimate_clause,
        REVIEW_LINK=review_link,
    ).strip()

    if intake_section:
        base += f"\n\n{intake_section}"

    return apply_language_directive(base)


async def build_system_prompt_async(
    contractor: Contractor,
    intake_section: str = "",
    db: Optional["AsyncSession"] = None,
) -> str:
    """
    Async variant of build_system_prompt.
    Appends a TRIAGE_INSTRUCTIONS section when triage_library_v2 is ON
    and an active triage tree exists for the contractor's primary trade.
    Flag OFF (default) → byte-for-byte identical to build_system_prompt().
    """
    base = build_system_prompt(contractor, intake_section=intake_section)

    if not settings.triage_library_v2 or db is None:
        return base

    triage_section = await _build_triage_section(contractor, db)
    if triage_section:
        base += "\n\n" + triage_section

    return base


async def _build_triage_section(
    contractor: Contractor,
    db: "AsyncSession",
) -> str:
    """
    Build the TRIAGE_INSTRUCTIONS prompt section for a contractor.
    Returns empty string if no active tree is found or any error occurs.
    """
    try:
        from app.services.triage_library import TriageLibraryService

        # Determine trade — use primary_trade if set, else first entry in contractor.trades
        trade = getattr(contractor, "primary_trade", None)
        if not trade:
            trades = contractor.trades or []
            trade = trades[0] if trades else None
        if not trade:
            return ""

        svc = TriageLibraryService()
        tree = await svc.get_active_tree(
            tenant_id=contractor.id,
            trade=trade,
            language="en",
            db=db,
        )
        if tree is None:
            return ""

        nodes = await svc.get_tree_nodes(tree.id, db)
        return svc.build_triage_prompt_section(tree, nodes)

    except Exception as exc:
        logger.warning(
            "builder: triage section build failed (non-fatal) | contractor=%s err=%s",
            getattr(contractor, "id", "?"), exc,
        )
        return ""
