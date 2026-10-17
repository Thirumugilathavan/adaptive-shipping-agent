"""
Unit tests for Courier Ranking Engine (Part A).
Tests ranking modes, constraint filters, missing estimates, tie-breaking, and explanations.
"""

import pytest
from src.domain.models import (
    Address,
    CostBreakdown,
    CourierQuote,
    Dimensions,
    OrderInput,
    PackageType,
    WeightResult,
)
from src.domain.ranking import (
    RankingMode,
    RankingPreferences,
    rank_couriers,
)
from src.providers.demo_adapter import DemoShippingAdapter


def make_test_order(
    parcel_weight_kg: float = 2.0,
    dest_pin: str = "560034",
    package_type: PackageType = PackageType.BOX,
) -> OrderInput:
    return OrderInput(
        order_reference="ORD-RANK-001",
        sender=Address(
            name="Origin Hub",
            phone="9876543210",
            street_line1="Warehouse 1",
            city="New Delhi",
            state="Delhi",
            pin_code="110020",
        ),
        recipient=Address(
            name="Dest Buyer",
            phone="9123456780",
            street_line1="Apartment 4B",
            city="Bengaluru",
            state="Karnataka",
            pin_code=dest_pin,
        ),
        parcel_weight_kg=parcel_weight_kg,
        dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        package_type=package_type,
    )


def test_ranking_three_couriers_by_lowest_cost():
    """Verify lowest eligible total cost ranking mode."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    res = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))

    assert res.selected_winner is not None
    assert res.total_eligible == 3
    # Surface Standard is cheaper than Air Express and Heavy Cargo for a standard 2 kg parcel
    assert res.selected_winner.quote.courier_code == "DEMO_SURFACE_STD"
    assert res.ranked_candidates[0].rank == 1
    assert res.ranked_candidates[0].total_cost < res.ranked_candidates[1].total_cost
    assert res.cost_margin_over_runner_up is not None
    assert res.cost_margin_over_runner_up > 0.0


def test_ranking_by_fastest_delivery():
    """Verify fastest delivery ranking mode selects the shortest verified SLA."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    res = rank_couriers(quotes, RankingPreferences(mode=RankingMode.FASTEST_DELIVERY))

    assert res.selected_winner is not None
    # Air Express has 2 days transit, Surface has 5 days, Cargo has 7 days
    assert res.selected_winner.quote.courier_code == "DEMO_AIR_EXPRESS"
    assert res.selected_winner.estimated_delivery_days == 2
    assert res.days_margin_over_runner_up == 3  # 5 - 2 = 3 days faster than runner-up


def test_ranking_balanced_mode():
    """Verify balanced mode calculates composite score with configurable weights."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    # 50% cost weight, 50% speed weight
    prefs = RankingPreferences(mode=RankingMode.BALANCED, cost_weight=0.5, speed_weight=0.5)
    res = rank_couriers(quotes, prefs)

    assert res.selected_winner is not None
    assert len(res.ranked_candidates) == 3
    for candidate in res.ranked_candidates:
        assert 0.0 <= candidate.balanced_score <= 100.0


def test_exclude_unserviceable_destination_and_unsupported_package_type():
    """
    Remote PIN '790001' excludes Air Express.
    FLYER package type excludes Heavy Cargo.
    Only Surface Standard remains eligible.
    """
    order = make_test_order(dest_pin="790001", package_type=PackageType.FLYER)
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    res = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))

    assert res.total_eligible == 1
    assert res.total_ineligible == 2
    assert res.selected_winner is not None
    assert res.selected_winner.quote.courier_code == "DEMO_SURFACE_STD"

    ineligible_codes = [iq.courier_code for iq in res.ineligible_candidates]
    assert "DEMO_AIR_EXPRESS" in ineligible_codes
    assert "DEMO_ECONOMY_CARGO" in ineligible_codes


def test_missing_cost_and_delivery_estimates():
    """Quotes with missing cost breakdown or missing delivery estimates must be excluded."""
    dummy_quote = CourierQuote(
        quote_id="Q-INCOMPLETE",
        courier_code="INCOMPLETE_CARRIER",
        courier_name="Incomplete Carrier",
        service_type="Mystery",
        estimated_delivery_days=None,  # Missing SLA
        is_serviceable=True,
        is_eligible=True,
        weight_result=None,
        cost_breakdown=None,  # Missing cost
        input_hash="hash",
    )

    # When ranked by lowest cost, missing cost is excluded
    res_cost = rank_couriers([dummy_quote], RankingPreferences(mode=RankingMode.LOWEST_COST))
    assert res_cost.total_eligible == 0
    assert len(res_cost.ineligible_candidates) == 1
    assert "Missing cost breakdown" in res_cost.ineligible_candidates[0].rejection_reasons[0]

    # When ranked by fastest delivery, missing SLA is excluded
    res_speed = rank_couriers([dummy_quote], RankingPreferences(mode=RankingMode.FASTEST_DELIVERY))
    assert res_speed.total_eligible == 0
    assert any("Missing estimated delivery days" in r for r in res_speed.ineligible_candidates[0].rejection_reasons)


def test_budget_constraint_filtering():
    """Quotes exceeding maximum budget must be marked ineligible."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    # Budget ₹250.00: Surface Standard is ~₹237, while Air (~₹526) and Cargo (~₹325) exceed ₹250
    prefs = RankingPreferences(mode=RankingMode.LOWEST_COST, max_budget=250.0)
    res = rank_couriers(quotes, prefs)

    assert res.total_eligible == 1
    assert res.selected_winner.quote.courier_code == "DEMO_SURFACE_STD"
    assert res.total_ineligible == 2
    for inel in res.ineligible_candidates:
        assert any("exceeds maximum budget" in r for r in inel.rejection_reasons)


def test_delivery_deadline_constraint_filtering():
    """Quotes slower than maximum transit days deadline must be marked ineligible."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    # Max 2 days deadline: Only Air Express can deliver in 2 days
    prefs = RankingPreferences(mode=RankingMode.LOWEST_COST, max_delivery_days=2)
    res = rank_couriers(quotes, prefs)

    assert res.total_eligible == 1
    assert res.selected_winner.quote.courier_code == "DEMO_AIR_EXPRESS"
    assert res.total_ineligible == 2


def test_no_eligible_courier_scenario():
    """When budget is unrealistically tight (e.g. ₹50), no couriers are eligible."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    prefs = RankingPreferences(mode=RankingMode.LOWEST_COST, max_budget=50.0)
    res = rank_couriers(quotes, prefs)

    assert res.selected_winner is None
    assert res.total_eligible == 0
    assert res.total_ineligible == 3
    assert "No couriers are eligible" in res.decision_explanation


def test_deterministic_tie_breaking():
    """
    Two synthetic quotes with identical total cost.
    Tie must be broken deterministically by SLA days, then courier_code.
    """
    cost_data = CostBreakdown(
        base_freight=100.0,
        fuel_surcharge=10.0,
        fuel_surcharge_rate_pct=10.0,
        cod_fee=0.0,
        handling_fee=0.0,
        subtotal_before_tax=110.0,
        tax_amount=19.8,
        tax_rate_pct=18.0,
        total_cost=129.80,
    )
    weight_data = WeightResult(
        actual_weight_kg=1.0,
        volumetric_weight_kg=1.0,
        chargeable_weight_kg=1.0,
        billing_slab_kg=1.0,
        divisor_used=5000.0,
        is_volumetric_penalty=False,
    )

    quote_b = CourierQuote(
        quote_id="Q-B",
        courier_code="COURIER_B",
        courier_name="Courier B",
        service_type="Standard",
        estimated_delivery_days=3,
        is_serviceable=True,
        is_eligible=True,
        weight_result=weight_data,
        cost_breakdown=cost_data,
        input_hash="hash",
    )
    quote_a = CourierQuote(
        quote_id="Q-A",
        courier_code="COURIER_A",
        courier_name="Courier A",
        service_type="Express",
        estimated_delivery_days=2,  # Faster
        is_serviceable=True,
        is_eligible=True,
        weight_result=weight_data,
        cost_breakdown=cost_data,
        input_hash="hash",
    )

    # Identical cost: COURIER_A wins because it delivers in 2 days vs 3 days
    res = rank_couriers([quote_b, quote_a], RankingPreferences(mode=RankingMode.LOWEST_COST))
    assert res.selected_winner.quote.courier_code == "COURIER_A"
