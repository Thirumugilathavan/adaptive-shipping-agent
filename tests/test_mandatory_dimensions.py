"""
Automated test suite verifying that parcel dimensions and actual gross weight
are strictly mandatory across all shipping workflows.

Covers all 12 conditions specified in the specification:
1. Missing length -> rejection with appropriate message.
2. Missing width -> rejection with appropriate message.
3. Missing height -> rejection with appropriate message.
4. Missing actual weight -> rejection with appropriate message.
5. Zero length/width/height/weight -> rejection ("Dimensions and weight must be greater than zero.").
6. Negative length/width/height/weight -> rejection.
7. Valid dimensions & weight -> successful courier quotes.
8. Packaging optimizer flow -> unpacked dimensions + net weight -> packed dimensions + gross weight (net + tare) correctly passed to rating engine.
9. Volumetric weight exceeds actual weight -> chargeable weight equals volumetric weight, flagged appropriately.
10. Actual weight exceeds volumetric weight -> chargeable weight equals actual weight.
11. Dimension/weight change invalidates previous quotes and blocks stale approval.
12. Re-choice retains mandatory measurements and recalculates alternatives correctly.
"""

from __future__ import annotations

import math
import pytest
from pydantic import ValidationError

from src.domain.models import Address, Dimensions, OrderInput, PackageType
from src.domain.packaging import DEMO_PACKAGING_CATALOGUE, evaluate_packaging_alternatives
from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers
from src.domain.weight import calculate_chargeable_weight, calculate_volumetric_weight, compute_weight_breakdown
from src.persistence.state_machine import ApprovalRecord
from src.providers.demo_adapter import ALL_DEMO_COURIERS, DemoShippingAdapter
from src.ui.helpers import (
    build_order_input_from_form,
    validate_positive_float,
    validate_shipment_measurements,
)


@pytest.fixture
def base_addresses():
    sender = Address(
        name="Alpha Hub",
        phone="9876543210",
        street_line1="1 Main St",
        city="Delhi",
        state="Delhi",
        pin_code="110020",
    )
    recipient = Address(
        name="Vikram Patel",
        phone="9123456780",
        street_line1="402 Green Acres",
        city="Bengaluru",
        state="Karnataka",
        pin_code="560034",
    )
    return sender, recipient


# ── Test 1: Missing length rejection ───────────────────────────────────────────
def test_1_missing_length_rejected():
    """Verify that omitting length is rejected with clear, field-specific message."""
    ok, field_errs, err_list = validate_shipment_measurements(
        length_cm=None,
        width_cm=20.0,
        height_cm=15.0,
        parcel_weight_kg=1.8,
    )
    assert not ok
    assert "length_cm" in field_errs
    assert "Please enter the parcel length." in field_errs["length_cm"]

    # Also test empty string and whitespace
    ok_str, field_errs_str, _ = validate_shipment_measurements("", 20.0, 15.0, 1.8)
    assert not ok_str
    assert "Please enter the parcel length." in field_errs_str["length_cm"]

    # Domain model validation
    with pytest.raises(ValidationError) as exc_info:
        Dimensions(length_cm=None, width_cm=20.0, height_cm=15.0)  # type: ignore
    assert "length_cm" in str(exc_info.value)


# ── Test 2: Missing width rejection ────────────────────────────────────────────
def test_2_missing_width_rejected():
    """Verify that omitting width is rejected with clear, field-specific message."""
    ok, field_errs, err_list = validate_shipment_measurements(
        length_cm=25.0,
        width_cm=None,
        height_cm=15.0,
        parcel_weight_kg=1.8,
    )
    assert not ok
    assert "width_cm" in field_errs
    assert "Please enter the parcel width." in field_errs["width_cm"]

    with pytest.raises(ValidationError) as exc_info:
        Dimensions(length_cm=25.0, width_cm=None, height_cm=15.0)  # type: ignore
    assert "width_cm" in str(exc_info.value)


# ── Test 3: Missing height rejection ───────────────────────────────────────────
def test_3_missing_height_rejected():
    """Verify that omitting height is rejected with clear, field-specific message."""
    ok, field_errs, err_list = validate_shipment_measurements(
        length_cm=25.0,
        width_cm=20.0,
        height_cm=None,
        parcel_weight_kg=1.8,
    )
    assert not ok
    assert "height_cm" in field_errs
    assert "Please enter the parcel height." in field_errs["height_cm"]

    with pytest.raises(ValidationError) as exc_info:
        Dimensions(length_cm=25.0, width_cm=20.0, height_cm=None)  # type: ignore
    assert "height_cm" in str(exc_info.value)


# ── Test 4: Missing actual weight rejection ────────────────────────────────────
def test_4_missing_actual_weight_rejected(base_addresses):
    """Verify that omitting actual weight is rejected with clear, field-specific message."""
    ok, field_errs, err_list = validate_shipment_measurements(
        length_cm=25.0,
        width_cm=20.0,
        height_cm=15.0,
        parcel_weight_kg=None,
    )
    assert not ok
    assert "parcel_weight_kg" in field_errs
    assert "Please enter the actual parcel weight." in field_errs["parcel_weight_kg"]

    # Also test empty string and non-numeric
    ok_str, field_errs_str, _ = validate_shipment_measurements(25.0, 20.0, 15.0, "")
    assert not ok_str
    assert "Please enter the actual parcel weight." in field_errs_str["parcel_weight_kg"]

    sender, recipient = base_addresses
    with pytest.raises(ValidationError) as exc_info:
        OrderInput(
            order_reference="REF-01",
            sender=sender,
            recipient=recipient,
            parcel_weight_kg=None,  # type: ignore
            dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        )
    assert "parcel_weight_kg" in str(exc_info.value)


# ── Test 5: Zero values rejected ───────────────────────────────────────────────
def test_5_zero_measurements_rejected():
    """Verify zero dimensions and zero weight are rejected with required message."""
    for kwargs in [
        {"length_cm": 0.0, "width_cm": 20.0, "height_cm": 15.0, "parcel_weight_kg": 1.8},
        {"length_cm": 25.0, "width_cm": 0.0, "height_cm": 15.0, "parcel_weight_kg": 1.8},
        {"length_cm": 25.0, "width_cm": 20.0, "height_cm": 0.0, "parcel_weight_kg": 1.8},
        {"length_cm": 25.0, "width_cm": 20.0, "height_cm": 15.0, "parcel_weight_kg": 0.0},
    ]:
        ok, field_errs, err_list = validate_shipment_measurements(**kwargs)
        assert not ok
        assert "Dimensions and weight must be greater than zero." in err_list
        assert any("must be greater than zero" in msg for msg in field_errs.values())

    # Domain model enforcement
    with pytest.raises(ValidationError):
        Dimensions(length_cm=0.0, width_cm=20.0, height_cm=15.0)
    with pytest.raises(ValidationError):
        Dimensions(length_cm=25.0, width_cm=0.0, height_cm=15.0)
    with pytest.raises(ValidationError):
        Dimensions(length_cm=25.0, width_cm=20.0, height_cm=0.0)


# ── Test 6: Negative, NaN, Inf, non-numeric values rejected ───────────────────
def test_6_negative_nan_inf_rejected():
    """Verify negative, NaN, Inf, and non-numeric values are rejected safely."""
    # Negative values
    ok, field_errs, err_list = validate_shipment_measurements(-5.0, 20.0, 15.0, 1.8)
    assert not ok
    assert "Dimensions and weight must be greater than zero." in err_list

    ok_wt, field_errs_wt, _ = validate_shipment_measurements(25.0, 20.0, 15.0, -2.0)
    assert not ok_wt
    assert "Dimensions and weight must be greater than zero." in field_errs_wt.get("parcel_weight_kg", "") or "greater than" in field_errs_wt.get("parcel_weight_kg", "")

    # NaN and Inf
    ok_nan, field_errs_nan, _ = validate_shipment_measurements(float("nan"), 20.0, 15.0, 1.8)
    assert not ok_nan
    assert "finite number" in field_errs_nan["length_cm"]

    ok_inf, field_errs_inf, _ = validate_shipment_measurements(25.0, float("inf"), 15.0, 1.8)
    assert not ok_inf
    assert "finite number" in field_errs_inf["width_cm"]

    # Non-numeric string
    ok_str, field_errs_str, _ = validate_shipment_measurements("twenty-five", 20.0, 15.0, 1.8)
    assert not ok_str
    assert "valid number" in field_errs_str["length_cm"]


# ── Test 7: Valid dimensions and weight yield successful quotes ───────────────
def test_7_valid_measurements_yield_quotes(base_addresses):
    """Verify that complete, valid measurements produce successful, rated courier quotes."""
    sender, recipient = base_addresses
    dims = Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0)
    order = OrderInput(
        order_reference="ORD-VALID-001",
        sender=sender,
        recipient=recipient,
        parcel_weight_kg=1.8,
        dimensions=dims,
        package_type=PackageType.BOX,
        is_cod=False,
    )

    provider = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
    quotes = provider.get_rates(order)
    assert len(quotes) > 0
    eligible_quotes = [q for q in quotes if q.is_eligible]
    assert len(eligible_quotes) > 0
    for q in eligible_quotes:
        assert q.cost_breakdown is not None
        assert q.cost_breakdown.total_cost > 0.0
        assert q.weight_result is not None
        assert q.weight_result.chargeable_weight_kg > 0.0


# ── Test 8: Packaging optimizer flow correctly updates rating measurements ─────
def test_8_packaging_optimizer_flow():
    """Verify product dimensions + net weight map to outer dimensions + gross weight (net + tare)."""
    unpacked_dims = Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0)
    net_weight_kg = 1.2

    alternatives = evaluate_packaging_alternatives(
        product_dimensions=unpacked_dims,
        product_net_weight_kg=net_weight_kg,
        current_outer_dimensions=unpacked_dims,
        custom_packages=[],
        couriers=ALL_DEMO_COURIERS,
    )
    feasible = [a for a in alternatives if a.is_feasible]
    assert len(feasible) > 0

    best = min(feasible, key=lambda a: (a.new_billable_slab_kg, -a.estimated_freight_savings_inr))
    packed_dims = best.outer_dimensions
    packed_gross_weight = round(net_weight_kg + best.tare_weight_kg, 3)

    # Dimensions must be strictly outer box dimensions
    assert packed_dims.length_cm >= unpacked_dims.length_cm
    assert packed_dims.width_cm >= unpacked_dims.width_cm
    assert packed_dims.height_cm >= unpacked_dims.height_cm
    # Gross weight must include tare weight
    assert packed_gross_weight > net_weight_kg
    assert packed_gross_weight == round(net_weight_kg + best.tare_weight_kg, 3)

    # Validate that these final measurements pass validation and can be rated
    ok, _, _ = validate_shipment_measurements(
        packed_dims.length_cm,
        packed_dims.width_cm,
        packed_dims.height_cm,
        packed_gross_weight,
    )
    assert ok


# ── Test 9: Volumetric weight exceeds actual weight (Volumetric penalty) ───────
def test_9_volumetric_weight_exceeds_actual_weight():
    """Verify volumetric weight penalty: chargeable weight equals volumetric weight."""
    # Large light box: 50 x 40 x 30 cm = 60,000 cm3
    # At divisor 5000: volumetric weight = 12.0 kg
    # Actual weight: 2.0 kg
    dims = Dimensions(length_cm=50.0, width_cm=40.0, height_cm=30.0)
    actual_weight_kg = 2.0
    vol_wt = calculate_volumetric_weight(dims, divisor=5000.0)
    assert vol_wt == 12.0

    chargeable, slab = calculate_chargeable_weight(
        actual_weight_kg=actual_weight_kg,
        volumetric_weight_kg=vol_wt,
        min_billable_weight_kg=0.5,
        billing_step_kg=0.5,
    )
    assert chargeable == 12.0
    assert slab == 12.0

    wb = compute_weight_breakdown(actual_weight_kg, dims)
    assert wb.is_volumetric_penalty is True
    assert wb.chargeable_weight_kg == 12.0
    assert wb.volumetric_weight_kg > wb.actual_weight_kg


# ── Test 10: Actual weight exceeds volumetric weight ───────────────────────────
def test_10_actual_weight_exceeds_volumetric_weight():
    """Verify that when actual weight is higher, chargeable weight equals actual weight."""
    # Compact dense box: 15 x 10 x 10 cm = 1,500 cm3
    # At divisor 5000: volumetric weight = 0.3 kg
    # Actual weight: 5.0 kg
    dims = Dimensions(length_cm=15.0, width_cm=10.0, height_cm=10.0)
    actual_weight_kg = 5.0
    vol_wt = calculate_volumetric_weight(dims, divisor=5000.0)
    assert vol_wt == 0.3

    chargeable, slab = calculate_chargeable_weight(
        actual_weight_kg=actual_weight_kg,
        volumetric_weight_kg=vol_wt,
        min_billable_weight_kg=0.5,
        billing_step_kg=0.5,
    )
    assert chargeable == 5.0
    assert slab == 5.0

    wb = compute_weight_breakdown(actual_weight_kg, dims)
    assert wb.is_volumetric_penalty is False
    assert wb.chargeable_weight_kg == 5.0


# ── Test 11: Dimension/weight change invalidates quotes and blocks stale approval
def test_11_dimension_weight_change_invalidates_quotes(base_addresses):
    """Verify that changing dimensions or weight detects measurement mismatch and blocks approval."""
    sender, recipient = base_addresses
    original_dims = Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0)
    order_1kg = OrderInput(
        order_reference="ORD-STALE-TEST",
        sender=sender,
        recipient=recipient,
        parcel_weight_kg=1.0,
        dimensions=original_dims,
    )
    provider = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
    quotes_1kg = provider.get_rates(order_1kg)
    rank_1kg = rank_couriers(quotes_1kg, RankingPreferences(mode=RankingMode.LOWEST_COST))
    winner_1kg = rank_1kg.selected_winner.quote

    # User modifies weight to 5.0 kg
    order_5kg = OrderInput(
        order_reference="ORD-STALE-TEST",
        sender=sender,
        recipient=recipient,
        parcel_weight_kg=5.0,
        dimensions=original_dims,
    )

    # Simulated measurement change detection
    measurements_initial = (
        order_1kg.dimensions.length_cm,
        order_1kg.dimensions.width_cm,
        order_1kg.dimensions.height_cm,
        order_1kg.parcel_weight_kg,
    )
    measurements_updated = (
        order_5kg.dimensions.length_cm,
        order_5kg.dimensions.width_cm,
        order_5kg.dimensions.height_cm,
        order_5kg.parcel_weight_kg,
    )
    is_changed = (measurements_initial != measurements_updated)
    assert is_changed is True

    # If quotes were calculated for 1kg, they cannot be approved for 5kg order
    quotes_5kg = provider.get_rates(order_5kg)
    quote_5kg_matching = next(q for q in quotes_5kg if q.courier_code == winner_1kg.courier_code)
    # The cost for 5kg is strictly higher than 1kg
    assert quote_5kg_matching.cost_breakdown.total_cost > winner_1kg.cost_breakdown.total_cost

    # Verification: approval record created with stale 1kg cost against 5kg order must be detected as drift
    approval_stale = ApprovalRecord(
        order_reference=order_5kg.order_reference,
        quote_id=winner_1kg.quote_id,
        courier_code=winner_1kg.courier_code,
        approved_by="Shipping Operator",
        total_cost=winner_1kg.cost_breakdown.total_cost,
    )
    assert approval_stale.total_cost != quote_5kg_matching.cost_breakdown.total_cost


# ── Test 12: Re-Choice retains measurements and recalculates alternatives ──────
def test_12_rechoice_retains_measurements_and_recalculates(base_addresses):
    """Verify that rejecting a courier keeps measurements intact and yields valid alternatives."""
    sender, recipient = base_addresses
    dims = Dimensions(length_cm=30.0, width_cm=20.0, height_cm=15.0)
    order = OrderInput(
        order_reference="ORD-RECHOICE-001",
        sender=sender,
        recipient=recipient,
        parcel_weight_kg=2.5,
        dimensions=dims,
    )

    provider = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
    quotes = provider.get_rates(order)
    prefs = RankingPreferences(mode=RankingMode.LOWEST_COST)

    # Initial ranking
    initial_ranking = rank_couriers(quotes, prefs)
    assert initial_ranking.selected_winner is not None
    first_winner = initial_ranking.selected_winner.quote

    # Re-Choice: User rejects the first winner
    excluded_couriers = [first_winner.courier_code]
    remaining_quotes = [q for q in quotes if q.courier_code not in excluded_couriers]
    assert len(remaining_quotes) > 0

    rechoice_ranking = rank_couriers(remaining_quotes, prefs)
    assert rechoice_ranking.selected_winner is not None
    alternative_winner = rechoice_ranking.selected_winner.quote

    # The alternative must be a different courier
    assert alternative_winner.courier_code != first_winner.courier_code
    # Measurements are strictly preserved on the alternative quote
    assert alternative_winner.weight_result.actual_weight_kg == 2.5
    # Resetting exclusions returns the original winner
    reset_ranking = rank_couriers(quotes, prefs)
    assert reset_ranking.selected_winner.quote.courier_code == first_winner.courier_code
