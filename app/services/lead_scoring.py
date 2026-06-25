def calculate_scores(lead_data: dict) -> dict:
    """
    Derive emergency_score, revenue_score, close_probability, and priority_level
    from lead data fields. Scores already set in lead_data are used as-is;
    missing scores are inferred from keyword signals in problem_summary and other flags.
    """
    emergency_score: int = lead_data.get("emergency_score") or _infer_emergency_score(lead_data)
    revenue_score: int = lead_data.get("revenue_score") or _infer_revenue_score(lead_data)
    close_probability: int = lead_data.get("close_probability") or _infer_close_probability(lead_data)

    priority_level = _derive_priority(
        emergency_score,
        revenue_score,
        close_probability,
        bool(lead_data.get("life_safety_risk")),
        lead_data.get("service_area_status", "unknown"),
    )

    return {
        "emergency_score": max(1, min(10, emergency_score)),
        "revenue_score": max(1, min(10, revenue_score)),
        "close_probability": max(1, min(10, close_probability)),
        "priority_level": priority_level,
    }


def _infer_emergency_score(lead_data: dict) -> int:
    summary = (lead_data.get("problem_summary") or "").lower()
    emergency_level = (lead_data.get("emergency_level") or "").lower()
    life_safety = bool(lead_data.get("life_safety_risk"))

    critical_keywords = [
        "flooding", "burst pipe", "gas smell", "sparks", "fire", "no heat",
        "frozen pipes", "electrocution", "trapped", "sewage backup", "sump pump failed",
    ]
    high_keywords = [
        "no hot water", "hvac failure", "ac not working", "heat not working",
        "garage door stuck", "leak", "breaker tripping",
    ]
    medium_keywords = [
        "slow drain", "intermittent", "dripping", "minor leak", "noisy",
    ]
    low_keywords = [
        "estimate", "quote", "future", "planning", "thinking about",
    ]

    if life_safety or any(k in summary for k in critical_keywords):
        return 9
    if any(k in summary for k in high_keywords) or "emergency" in emergency_level:
        return 7
    if any(k in summary for k in medium_keywords) or "urgent" in emergency_level:
        return 5
    if any(k in summary for k in low_keywords):
        return 2
    return 4


def _infer_revenue_score(lead_data: dict) -> int:
    property_type = (lead_data.get("property_type") or "").lower()
    service_category = (lead_data.get("service_category") or "").lower()
    summary = (lead_data.get("problem_summary") or "").lower()

    if property_type == "commercial":
        return 9
    if "replacement" in service_category or "installation" in service_category:
        return 8
    if "insurance" in service_category:
        return 9
    if any(k in summary for k in ["replace", "install", "new system", "upgrade"]):
        return 8
    if any(k in summary for k in ["repair", "fix", "broken"]):
        return 6
    if any(k in summary for k in ["tune", "maintenance", "check", "inspect"]):
        return 4
    return 5


def _infer_close_probability(lead_data: dict) -> int:
    appointment_status = (lead_data.get("appointment_status") or "").lower()
    service_area_status = (lead_data.get("service_area_status") or "unknown").lower()
    summary = (lead_data.get("problem_summary") or "").lower()

    if service_area_status == "outside":
        return 1
    if appointment_status == "booked":
        return 10
    if appointment_status == "transferred":
        return 6
    if any(k in summary for k in ["price shop", "just checking", "comparing", "getting quotes"]):
        return 3
    if any(k in summary for k in ["need", "asap", "urgent", "today", "right away"]):
        return 8
    return 6


def _derive_priority(
    emergency_score: int,
    revenue_score: int,
    close_probability: int,
    life_safety: bool,
    service_area_status: str,
) -> str:
    if life_safety or emergency_score >= 8:
        return "Critical"
    if emergency_score >= 6 or revenue_score >= 8 or close_probability >= 8:
        return "High"
    if service_area_status == "outside" or (emergency_score <= 3 and revenue_score <= 3):
        return "Low"
    return "Medium"
