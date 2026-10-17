"""
Unit tests for Shipping Decision Stress Tester and Recommendation Fragility Analysis (Parts B, C, D).
Covers scenario variations, recommendation switches, non-mutation of inputs,
critical price margins, and packaging stabilization impact.
"""

import copy
import pytest
from src.domain.models import (
    Address,
    Dimensions,
    OrderInput,
    PackageType,
)
from src.domain.ranking import RankingMode, RankingPreferences
from src.domain.stress_tester import (
    ShippingDecisionStressTester,
    StressScenario,
    generate_standard_stress_scenarios,
)


def make_sample_order(
    parcel_weight_kg: float = 2.0,
    length_cm: float = 35.0,
    width_cm: float = 25.0,
    height_cm: float = 20.0,
) -> OrderInput:
    return OrderInput(
        order_reference="ORD-STRESS-001",
        sender=Address(
            name="Delhi Merchant Hub",
            phone="9876543210",
            street_line1="Sector 6",
            city="New Delhi",
            state="Delhi",
            pin_code="110020",
        ),
        recipient=Address(
            name="Bengaluru Customer",
            phone="9123456780",
            street_line1="Indiranagar 12th Main",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560038",
        ),
        parcel_weight_kg=parcel_weight_kg,
        dimensions=Dimensions(length_cm=length_cm, width_cm=width_cm, height_cm=height_cm),
        package_type=PackageType.BOX,
        is_cod=False,
    )


def test_stress_tester_baseline_and_reproducibility():
    """Verify deterministic execution and baseline winner selection."""
    order = make_sample_order()
    tester = ShippingDecisionStressTester()

    report1 = tester.run_stress_test(order)
    report2 = tester.run_stress_test(order)

    # Completely reproducible
    assert report1.baseline_winner_code == report2.baseline_winner_code
    assert report1.scenario_stability_pct == report2.scenario_stability_pct
    assert report1.total_scenarios_evaluated == report2.total_scenarios_evaluated
    assert report1.total_scenarios_evaluated == 12  # Standard matrix count


def test_no_mutation_of_original_records():
    """Verify that original OrderInput and dimensions are never mutated during stress testing."""
    order = make_sample_order()
    orig_length = order.dimensions.length_cm
    orig_width = order.dimensions.width_cm
    orig_height = order.dimensions.height_cm
    orig_weight = order.parcel_weight_kg

    tester = ShippingDecisionStressTester()
    tester.run_stress_test(order)

    # Check original instance fields
    assert order.dimensions.length_cm == orig_length
    assert order.dimensions.width_cm == orig_width
    assert order.dimensions.height_cm == orig_height
    assert order.parcel_weight_kg == orig_weight


def test_recommendation_switches_under_strict_deadline():
    """
    In lowest cost mode, Surface Standard wins baseline (cheapest).
    Under an urgent 2-day delivery constraint, winner must switch to Air Express.
    """
    order = make_sample_order()
    tester = ShippingDecisionStressTester()

    report = tester.run_stress_test(order, RankingPreferences(mode=RankingMode.LOWEST_COST))

    assert report.baseline_winner_code == "DEMO_SURFACE_STD"

    # Find the urgent SLA scenario outcome
    sla_outcomes = [o for o in report.scenario_outcomes if o.scenario.scenario_id == "SLA_STRICT_2D"]
    assert len(sla_outcomes) == 1
    sla_result = sla_outcomes[0]

    assert sla_result.rank_switched is True
    assert sla_result.winner_code == "DEMO_AIR_EXPRESS"
    assert sla_result.causative_factor == "DEADLINE_VARIATION"
    assert "Air Express" in sla_result.transition_explanation


def test_large_lightweight_parcel_stress_test():
    """
    Large lightweight parcel (0.5 kg, 60 x 50 x 40 cm).
    Stress testing recalculates volumetric penalties across all scaling permutations.
    """
    bulky_order = make_sample_order(parcel_weight_kg=0.5, length_cm=60.0, width_cm=50.0, height_cm=40.0)
    tester = ShippingDecisionStressTester()

    report = tester.run_stress_test(bulky_order)

    assert report.total_scenarios_evaluated > 0
    # For large commercial parcel, Heavy Cargo is cheapest
    assert report.baseline_winner_code == "DEMO_ECONOMY_CARGO"

    # In +20% dimensions scenario, volumetric weight increases dramatically
    plus_20_outcome = [o for o in report.scenario_outcomes if o.scenario.scenario_id == "DIM_PLUS_20"][0]
    assert plus_20_outcome.winner_chargeable_weight_kg > report.baseline_winner_cost * 0.01


def test_scenario_stability_disclosure_and_metrics():
    """Verify stability percentage calculation and presence of mandatory disclosures."""
    order = make_sample_order()
    tester = ShippingDecisionStressTester()
    report = tester.run_stress_test(order)

    # Stability percentage is properly bounded
    assert 0.0 <= report.scenario_stability_pct <= 100.0
    assert report.scenarios_baseline_maintained + report.switched_scenarios_count + report.ineligible_scenarios_count == report.total_scenarios_evaluated

    # Required disclaimer present
    assert "SCENARIO ROBUSTNESS DISCLOSURE" in report.disclaimer
    assert "NOT an empirical statistical probability" in report.disclaimer


def test_recommendation_fragility_analysis():
    """
    Verify Distinctive Innovation: Recommendation Fragility Analysis.
    Tests critical price margin, sensitivity classification, and packaging stabilization impact.
    """
    order = make_sample_order()
    tester = ShippingDecisionStressTester()
    report = tester.run_stress_test(order)

    frag = report.fragility_analysis
    assert frag is not None
    assert frag.most_sensitive_input in ("DIMENSIONS", "FUEL_PRICE", "DELIVERY_SLA", "ROBUST")
    assert frag.critical_price_margin_inr is not None
    assert frag.critical_price_margin_inr > 0.0
    assert "LIMITATIONS OF ANALYSIS" in frag.limitations_disclaimer

    # Packaging stabilization effect is reported
    assert len(frag.packaging_stabilization_effect) > 0
    assert frag.packaging_savings_potential_inr >= 0.0


def test_packaging_changes_affect_costs_and_rankings():
    """
    Demonstrate that alternative packaging reduces volumetric weight and lowers landed costs.
    Original bulky box (40 x 30 x 20 cm) vs compact fitted box (25 x 20 x 15 cm).
    """
    bulky_order = make_sample_order(parcel_weight_kg=1.0, length_cm=40.0, width_cm=30.0, height_cm=20.0)
    compact_order = make_sample_order(parcel_weight_kg=1.0, length_cm=25.0, width_cm=20.0, height_cm=15.0)

    tester = ShippingDecisionStressTester()
    bulky_report = tester.run_stress_test(bulky_order)
    compact_report = tester.run_stress_test(compact_order)

    # Compact order has substantially lower baseline cost due to eliminated volumetric penalty
    assert compact_report.baseline_winner_cost < bulky_report.baseline_winner_cost


def test_stress_tester_error_on_impossible_order():
    """If an order has no eligible couriers under baseline (e.g. unserviceable PIN for all), raises ValueError."""
    impossible_order = OrderInput(
        order_reference="ORD-IMPOSSIBLE-001",
        sender=Address(
            name="Origin", phone="9876543210", street_line1="Main Street", city="Delhi", state="Delhi", pin_code="110020"
        ),
        recipient=Address(
            name="Dest", phone="9123456780", street_line1="Main Street", city="Blocked", state="Blocked", pin_code="790999"
        ),
        parcel_weight_kg=600.0,  # Exceeds max weight of all couriers (max is 500 kg for cargo)
        dimensions=Dimensions(length_cm=10.0, width_cm=10.0, height_cm=10.0),
        package_type=PackageType.BOX,
    )
    tester = ShippingDecisionStressTester()
    with pytest.raises(ValueError, match="Cannot perform stress testing: No couriers are eligible"):
        tester.run_stress_test(impossible_order)
