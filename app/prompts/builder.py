from app.models.contractor import Contractor
from app.prompts.master_prompt import MASTER_PROMPT_TEMPLATE
from app.prompts.multilang_wrapper import apply_language_directive
from app.config import settings


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
