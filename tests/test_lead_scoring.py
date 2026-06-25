import pytest

from app.services.lead_scoring import calculate_scores


# ---------------------------------------------------------------------------
# Priority level derivation
# ---------------------------------------------------------------------------

def test_active_flooding_is_critical():
    result = calculate_scores({"problem_summary": "active flooding in basement"})
    assert result["priority_level"] == "Critical"
    assert result["emergency_score"] >= 8


def test_life_safety_risk_flag_forces_critical():
    result = calculate_scores({"life_safety_risk": True, "problem_summary": "minor drip"})
    assert result["priority_level"] == "Critical"


def test_no_heat_in_freeze_is_critical():
    result = calculate_scores({"problem_summary": "no heat, frozen pipes, baby in the house"})
    assert result["priority_level"] == "Critical"


def test_sparks_is_critical():
    result = calculate_scores({"problem_summary": "sparks from outlet"})
    assert result["priority_level"] == "Critical"


def test_price_shopping_outside_area_is_low():
    result = calculate_scores({
        "problem_summary": "just getting a quote to compare prices",
        "service_area_status": "outside",
    })
    assert result["priority_level"] == "Low"


def test_commercial_job_is_high():
    result = calculate_scores({
        "property_type": "commercial",
        "problem_summary": "HVAC system down in restaurant",
        "service_area_status": "inside",
    })
    assert result["priority_level"] in ("High", "Critical")
    assert result["revenue_score"] >= 8


def test_insurance_claim_revenue_score():
    result = calculate_scores({"service_category": "insurance_claim"})
    assert result["revenue_score"] == 9


def test_booked_appointment_max_close_probability():
    result = calculate_scores({"appointment_status": "booked"})
    assert result["close_probability"] == 10


def test_outside_service_area_close_probability_is_one():
    result = calculate_scores({"service_area_status": "outside"})
    assert result["close_probability"] == 1
    assert result["priority_level"] == "Low"


def test_standard_repair_is_medium():
    result = calculate_scores({
        "problem_summary": "slow drain in bathroom",
        "service_area_status": "inside",
    })
    assert result["priority_level"] == "Medium"


# ---------------------------------------------------------------------------
# Score bounds
# ---------------------------------------------------------------------------

def test_emergency_score_bounded_1_to_10():
    result = calculate_scores({"emergency_score": 15})
    assert 1 <= result["emergency_score"] <= 10


def test_revenue_score_bounded_1_to_10():
    result = calculate_scores({"revenue_score": 0})
    assert 1 <= result["revenue_score"] <= 10


def test_close_probability_bounded_1_to_10():
    result = calculate_scores({"close_probability": -5})
    assert 1 <= result["close_probability"] <= 10


def test_all_scores_present_in_result():
    result = calculate_scores({})
    assert "emergency_score" in result
    assert "revenue_score" in result
    assert "close_probability" in result
    assert "priority_level" in result


def test_preset_scores_are_used_as_is():
    result = calculate_scores({
        "emergency_score": 3,
        "revenue_score": 3,
        "close_probability": 3,
    })
    assert result["emergency_score"] == 3
    assert result["revenue_score"] == 3
    assert result["close_probability"] == 3


# ---------------------------------------------------------------------------
# Priority derivation edge cases
# ---------------------------------------------------------------------------

def test_high_revenue_score_alone_is_high_priority():
    result = calculate_scores({"revenue_score": 9, "service_area_status": "inside"})
    assert result["priority_level"] in ("High", "Critical")


def test_medium_all_mid_scores():
    result = calculate_scores({
        "emergency_score": 5,
        "revenue_score": 5,
        "close_probability": 5,
        "service_area_status": "inside",
    })
    assert result["priority_level"] == "Medium"
