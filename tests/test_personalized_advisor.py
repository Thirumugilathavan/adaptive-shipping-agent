"""
Automated unit tests for the Smart Personalized Courier Recommendations & Closest-Match Engine.
Validates preference modeling, exact match prioritization, closest match scoring,
overrun/delay calculations, tie-breaking, narrative hint generation, and safety guards.
"""

from __future__ import annotations

import pytest

from src.domain.advisor import (
    AdvisorRecommendationResult,
    CourierComplianceCategory,
    CourierPreferenceCompliance,
    DeliveryDeadlineOption,
    UserShippingPreferences,
    build_advisor_recommendation,
    evaluate_quote_compliance,
    rank_compliance_candidates,
)
from src.domain.models import (
    Address,
    CostBreakdown,
    CourierQuote,
    Dimensions,
    OrderInput,
    PackageType,
    WeightResult,
)


def _make_dummy_quote(
    quote_id: str,
    courier_code: str,
    courier_name: str,
    total_cost: float,
    days: int | None = None,
    is_serviceable: bool = True,
    is_demo: bool = True,
    source_type: str = "DEMO",
) -> CourierQuote:
    """Helper to create test quotes with specific costs and transit days."""
    cost_bd = (
        CostBreakdown(
            base_freight=round(total_cost * 0.8, 2),
            fuel_surcharge=round(total_cost * 0.1, 2),
            fuel_surcharge_rate_pct=10.0,
            cod_fee=0.0,
            handling_fee=0.0,
            subtotal_before_tax=round(total_cost * 0.9, 2),
            tax_amount=round(total_cost * 0.1, 2),
            tax_rate_pct=18.0,
            total_cost=total_cost,
            currency="INR",
        )
        if total_cost > 0
        else None
    )

    return CourierQuote(
        quote_id=quote_id,
        courier_code=courier_code,
        courier_name=courier_name,
        service_type="Standard Express",
        estimated_delivery_days=days,
        is_serviceable=is_serviceable,
        is_eligible=is_serviceable and (cost_bd is not None),
        cost_breakdown=cost_bd,
        source_type=source_type,
        input_hash="hash-123",
        is_demo_data=is_demo,
    )


# ---------------------------------------------------------------------------
# 1. Budget & Deadline Preference Modeling
# ---------------------------------------------------------------------------

def test_preference_creation_flexible_budget_and_deadline():
    prefs = UserShippingPreferences.create(max_budget=None, is_flexible_budget=True, deadline_choice="Flexible delivery time")
    assert prefs.is_flexible_budget is True
    assert prefs.max_budget is None
    assert prefs.is_flexible_deadline is True
    assert prefs.max_delivery_days is None


def test_preference_creation_explicit_budget_and_deadline():
    prefs = UserShippingPreferences.create(max_budget=250.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    assert prefs.is_flexible_budget is False
    assert prefs.max_budget == 250.0
    assert prefs.is_flexible_deadline is False
    assert prefs.max_delivery_days == 2


def test_deadline_enum_options():
    assert DeliveryDeadlineOption.to_days("Within 1 day") == 1
    assert DeliveryDeadlineOption.to_days("Within 2 days") == 2
    assert DeliveryDeadlineOption.to_days("Within 3 days") == 3
    assert DeliveryDeadlineOption.to_days("Flexible delivery time") is None


# ---------------------------------------------------------------------------
# 2. Lowest-Cost Courier Satisfying Both Preferences
# ---------------------------------------------------------------------------

def test_lowest_cost_courier_satisfying_both_preferences():
    # Quotes:
    # Q1: ₹150, 2 days (meets both: budget 200, deadline 2 days)
    # Q2: ₹180, 1 day  (meets both: budget 200, deadline 2 days)
    # Q3: ₹250, 1 day  (over budget: 250 > 200)
    # Q4: ₹120, 4 days (misses deadline: 4 > 2)
    q1 = _make_dummy_quote("Q1", "air_std", "Air Standard", 150.0, days=2)
    q2 = _make_dummy_quote("Q2", "air_exp", "Air Express", 180.0, days=1)
    q3 = _make_dummy_quote("Q3", "premium", "Premium Courier", 250.0, days=1)
    q4 = _make_dummy_quote("Q4", "surface", "Surface Eco", 120.0, days=4)

    prefs = UserShippingPreferences.create(max_budget=200.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q1, q2, q3, q4], prefs)

    assert res.is_exact_match is True
    assert res.primary_recommendation is not None
    assert res.primary_recommendation.courier_code == "air_std"
    assert res.primary_recommendation.total_cost == 150.0
    assert res.primary_recommendation.budget_savings == 50.0
    assert "Fits your budget: INR 50.00 below your limit" in res.primary_recommendation.budget_status_text


# ---------------------------------------------------------------------------
# 3. Multiple Couriers Satisfying Both Preferences (Tie-Breaking)
# ---------------------------------------------------------------------------

def test_multiple_couriers_equal_cost_tie_breaker():
    # Both cost ₹150 and meet budget ₹200 and deadline 3 days.
    # Q1 takes 2 days, Q2 takes 1 day.
    # Q2 should win the tie-breaker because it delivers faster (1 day < 2 days).
    q1 = _make_dummy_quote("Q1", "carrier_b", "Carrier B", 150.0, days=2)
    q2 = _make_dummy_quote("Q2", "carrier_a", "Carrier A", 150.0, days=1)

    prefs = UserShippingPreferences.create(max_budget=200.0, is_flexible_budget=False, deadline_choice="Within 3 days")
    res = build_advisor_recommendation([q1, q2], prefs)

    assert res.is_exact_match is True
    assert res.primary_recommendation.courier_code == "carrier_a"
    assert res.primary_recommendation.estimated_delivery_days == 1


# ---------------------------------------------------------------------------
# 4. Closest-Match: Budget-Compliant but Late Alternatives
# ---------------------------------------------------------------------------

def test_no_exact_match_budget_ok_late():
    # User budget ₹100, deadline within 1 day.
    # Q1: ₹80, 3 days (under budget by 20, but 2 days late)
    # Q2: ₹95, 2 days (under budget by 5, but 1 day late)
    q1 = _make_dummy_quote("Q1", "slow_cheap", "Slow Cheap", 80.0, days=3)
    q2 = _make_dummy_quote("Q2", "medium", "Medium Speed", 95.0, days=2)

    prefs = UserShippingPreferences.create(max_budget=100.0, is_flexible_budget=False, deadline_choice="Within 1 day")
    res = build_advisor_recommendation([q1, q2], prefs)

    assert res.is_exact_match is False
    assert res.best_compromise is not None
    # Q2 has delay 1 day vs Q1 delay 2 days. Both have 1 violation (late).
    # Q2 delay penalty is smaller than Q1 delay penalty.
    assert res.best_compromise.courier_code == "medium"
    assert res.best_compromise.meets_budget is True
    assert res.best_compromise.meets_deadline is False
    assert res.best_compromise.delay_days == 1
    assert "Closest match: stays within your budget" in res.trade_off_explanation


# ---------------------------------------------------------------------------
# 5. Closest-Match: Deadline-Compliant but Over-Budget Alternatives
# ---------------------------------------------------------------------------

def test_no_exact_match_fast_over_budget():
    # User budget ₹100, deadline within 2 days.
    # Q1: ₹120, 1 day (meets deadline, over budget by ₹20)
    # Q2: ₹180, 1 day (meets deadline, over budget by ₹80)
    q1 = _make_dummy_quote("Q1", "air_1", "Air Express 1", 120.0, days=1)
    q2 = _make_dummy_quote("Q2", "air_2", "Air Express 2", 180.0, days=1)

    prefs = UserShippingPreferences.create(max_budget=100.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q1, q2], prefs)

    assert res.is_exact_match is False
    assert res.best_compromise is not None
    assert res.best_compromise.courier_code == "air_1"
    assert res.best_compromise.budget_overrun == 20.0
    assert "exceeds your budget by INR 20.00" in res.trade_off_explanation


# ---------------------------------------------------------------------------
# 6. Both Groups Present: Strongest Candidates from Both Groups Identified
# ---------------------------------------------------------------------------

def test_closest_match_both_groups_present():
    # User budget ₹100, deadline 2 days.
    # Group 1 (Budget OK, Late): Q1 (₹80, 4 days - late by 2 days)
    # Group 2 (Fast, Over Budget): Q2 (₹110, 1 day - over budget by ₹10)
    q1 = _make_dummy_quote("Q1", "surface_eco", "Surface Eco", 80.0, days=4)
    q2 = _make_dummy_quote("Q2", "express_air", "Express Air", 110.0, days=1)

    prefs = UserShippingPreferences.create(max_budget=100.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q1, q2], prefs)

    assert res.is_exact_match is False
    assert len(res.budget_ok_late) == 1
    assert res.budget_ok_late[0].courier_code == "surface_eco"
    assert len(res.fast_over_budget) == 1
    assert res.fast_over_budget[0].courier_code == "express_air"

    # Both groups exist, lowest cost and fastest are clearly captured
    assert res.lowest_cost_alternative.courier_code == "surface_eco"
    assert res.fastest_alternative.courier_code == "express_air"


# ---------------------------------------------------------------------------
# 7. Single Violation Preferred Over Double Violation
# ---------------------------------------------------------------------------

def test_single_violation_strictly_preferred_over_double_violation():
    # User budget ₹100, deadline 2 days.
    # Q1: ₹110, 2 days (1 violation: over budget by ₹10, meets deadline)
    # Q2: ₹140, 5 days (2 violations: over budget by ₹40 AND 3 days late)
    q1 = _make_dummy_quote("Q1", "air_slight_over", "Air", 110.0, days=2)
    q2 = _make_dummy_quote("Q2", "bad_option", "Slow Expensive", 140.0, days=5)

    prefs = UserShippingPreferences.create(max_budget=100.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q1, q2], prefs)

    assert res.best_compromise.courier_code == "air_slight_over"
    assert res.all_evaluated[0].violation_count == 1
    assert res.all_evaluated[1].violation_count == 2
    assert res.all_evaluated[0].closest_match_score > res.all_evaluated[1].closest_match_score


# ---------------------------------------------------------------------------
# 8. Flexible Budget or Flexible Deadline
# ---------------------------------------------------------------------------

def test_flexible_budget_only_evaluates_deadline():
    q1 = _make_dummy_quote("Q1", "air", "Air", 350.0, days=1)
    q2 = _make_dummy_quote("Q2", "surface", "Surface", 120.0, days=4)

    # Flexible budget, strict deadline 2 days
    prefs = UserShippingPreferences.create(max_budget=None, is_flexible_budget=True, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q1, q2], prefs)

    assert res.is_exact_match is True
    # Q1 meets 1-day deadline, price is ignored as budget is flexible
    assert res.primary_recommendation.courier_code == "air"
    assert res.primary_recommendation.meets_budget is True


def test_flexible_deadline_only_evaluates_budget():
    q1 = _make_dummy_quote("Q1", "expensive", "Fast Expensive", 400.0, days=1)
    q2 = _make_dummy_quote("Q2", "cheap", "Slow Cheap", 150.0, days=5)

    # Budget ₹200, flexible deadline
    prefs = UserShippingPreferences.create(max_budget=200.0, is_flexible_budget=False, deadline_choice="Flexible delivery time")
    res = build_advisor_recommendation([q1, q2], prefs)

    assert res.is_exact_match is True
    # Q2 meets budget ₹150 <= ₹200
    assert res.primary_recommendation.courier_code == "cheap"
    assert res.primary_recommendation.meets_deadline is True


# ---------------------------------------------------------------------------
# 9. No Confirmed Serviceable Courier
# ---------------------------------------------------------------------------

def test_no_confirmed_serviceable_courier():
    q_unserviceable = _make_dummy_quote("Q_UN", "unsvc", "Unserviceable Courier", 0.0, days=None, is_serviceable=False)
    prefs = UserShippingPreferences.create(max_budget=300.0, is_flexible_budget=False, deadline_choice="Within 2 days")

    res = build_advisor_recommendation([q_unserviceable], prefs)
    assert res.has_serviceable_couriers is False
    assert res.is_exact_match is False
    assert res.primary_recommendation is None
    assert "No verified serviceable courier is available for this route" in res.headline


# ---------------------------------------------------------------------------
# 10. Missing Delivery SLA Handled Safely Without Fabrication
# ---------------------------------------------------------------------------

def test_missing_sla_not_treated_as_confirmed_deadline_match():
    # Q1 has no estimated delivery days
    q1 = _make_dummy_quote("Q1", "no_sla", "No SLA Courier", 150.0, days=None)

    prefs = UserShippingPreferences.create(max_budget=200.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q1], prefs)

    assert res.is_exact_match is False
    assert res.all_evaluated[0].has_sla_estimate is False
    assert res.all_evaluated[0].meets_deadline is False
    assert "cannot confirm requested deadline" in res.all_evaluated[0].deadline_status_text


# ---------------------------------------------------------------------------
# 11. Verified vs Simulated Data Labelling
# ---------------------------------------------------------------------------

def test_data_verification_labelling():
    q_demo = _make_dummy_quote("Q_DEMO", "demo", "Demo Courier", 100.0, days=2, is_demo=True, source_type="DEMO")
    q_se = _make_dummy_quote("Q_SE", "se", "ShipEngine Courier", 120.0, days=2, is_demo=False, source_type="SHIPENGINE_SANDBOX")

    prefs = UserShippingPreferences.create(max_budget=None, is_flexible_budget=True, deadline_choice="Flexible delivery time")
    res = build_advisor_recommendation([q_demo, q_se], prefs)

    comp_demo = [c for c in res.all_evaluated if c.courier_code == "demo"][0]
    comp_se = [c for c in res.all_evaluated if c.courier_code == "se"][0]

    assert "Simulated Demo Data" in comp_demo.data_verification_text
    assert "ShipEngine Sandbox API" in comp_se.data_verification_text


# ---------------------------------------------------------------------------
# 12. Correct Price and Delivery-Time Differences
# ---------------------------------------------------------------------------

def test_relative_diff_and_plain_language_hints():
    # Primary will be Q1 (₹100, 3 days)
    # Alternative Q2 is ₹150, 1 day (costs ₹50 more, 2 days earlier)
    # Alternative Q3 is ₹75, 5 days (saves ₹25, 2 days later)
    q1 = _make_dummy_quote("Q1", "rec", "Recommended", 100.0, days=3)
    q2 = _make_dummy_quote("Q2", "fast", "Faster Express", 150.0, days=1)
    q3 = _make_dummy_quote("Q3", "cheap", "Economy Slow", 75.0, days=5)

    prefs = UserShippingPreferences.create(max_budget=200.0, is_flexible_budget=False, deadline_choice="Flexible delivery time")
    res = build_advisor_recommendation([q1, q2, q3], prefs)

    assert res.primary_recommendation.courier_code == "cheap"  # Lowest cost ₹75

    comp_rec = [c for c in res.all_evaluated if c.courier_code == "rec"][0]
    # Primary is ₹75, rec is ₹100 -> diff = +25, days = -2 (arrives earlier than primary)
    assert comp_rec.cost_diff_vs_recommended == 25.0
    assert "Costs INR 25.00 more" in comp_rec.plain_language_hint


# ---------------------------------------------------------------------------
# 13. Switching Couriers & Trade-Off Comparison
# ---------------------------------------------------------------------------

def test_user_switching_courier_tradeoffs():
    q_cheapest = _make_dummy_quote("Q1", "cheap", "Economy Surface", 80.0, days=5)
    q_fast = _make_dummy_quote("Q2", "fast", "Express Air", 140.0, days=1)

    prefs = UserShippingPreferences.create(max_budget=200.0, is_flexible_budget=False, deadline_choice="Flexible delivery time")
    res = build_advisor_recommendation([q_cheapest, q_fast], prefs)

    assert res.primary_recommendation.courier_code == "cheap"
    comp_fast = [c for c in res.all_evaluated if c.courier_code == "fast"][0]
    assert comp_fast.cost_diff_vs_recommended == 60.0
    assert comp_fast.days_diff_vs_recommended == -4  # 4 days faster!
    assert "Costs INR 60.00 more but is estimated to arrive 4 day(s) earlier" in comp_fast.plain_language_hint


# ---------------------------------------------------------------------------
# 14. Violation Flags for Selected Courier Confirmation Gate
# ---------------------------------------------------------------------------

def test_violation_flags_for_user_selection_confirmation():
    q_cheap = _make_dummy_quote("Q1", "cheap", "Economy", 90.0, days=4)
    q_fast_overbudget = _make_dummy_quote("Q2", "fast", "Air Express", 160.0, days=1)

    prefs = UserShippingPreferences.create(max_budget=100.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q_cheap, q_fast_overbudget], prefs)

    # Q_cheap misses deadline (4 > 2), but meets budget (90 <= 100) -> 1 violation
    # Q_fast meets deadline (1 <= 2), but exceeds budget (160 > 100) -> 1 violation
    comp_cheap = [c for c in res.all_evaluated if c.courier_code == "cheap"][0]
    comp_fast = [c for c in res.all_evaluated if c.courier_code == "fast"][0]

    assert comp_cheap.violation_count == 1
    assert comp_cheap.meets_deadline is False
    assert comp_cheap.delay_days == 2

    assert comp_fast.violation_count == 1
    assert comp_fast.meets_budget is False
    assert comp_fast.budget_overrun == 60.0


# ---------------------------------------------------------------------------
# 15. Unserviceable Couriers Never Recommended in Closest Match
# ---------------------------------------------------------------------------

def test_unserviceable_couriers_never_recommended():
    q_unserviceable = _make_dummy_quote("Q_UN", "unsvc", "Unserviceable Express", 0.0, days=1, is_serviceable=False)
    q_overbudget = _make_dummy_quote("Q_OVER", "serviceable_over", "Serviceable Expensive", 350.0, days=2, is_serviceable=True)

    prefs = UserShippingPreferences.create(max_budget=100.0, is_flexible_budget=False, deadline_choice="Within 2 days")
    res = build_advisor_recommendation([q_unserviceable, q_overbudget], prefs)

    assert res.has_serviceable_couriers is True
    # Primary closest match MUST be the serviceable courier, never the unserviceable one!
    assert res.primary_recommendation.courier_code == "serviceable_over"
    assert res.primary_recommendation.is_serviceable is True
    assert len(res.unserviceable) == 1
    assert res.unserviceable[0].courier_code == "unsvc"

