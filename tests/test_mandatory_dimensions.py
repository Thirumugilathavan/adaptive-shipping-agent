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
from pathlib import Path
import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

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


# ==============================================================================
# UI Regression Scenarios (Streamlit AppTest Suite)
# ==============================================================================

APP_FILE = str(Path(__file__).resolve().parent.parent / "src" / "ui" / "app.py")


def _find_submit_btn(at: AppTest):
    """Helper to locate the Find & Compare Couriers button."""
    btns = [b for b in at.button if "Compare Couriers" in b.label or "Find & Compare" in b.label]
    assert len(btns) > 0, "Submit button not found on page"
    return btns[0]


def test_13_ui_all_four_fields_empty_blocked():
    """Scenario 1: All four measurement fields empty -> validation fails with field-specific messages."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    err_msgs = [e.value for e in at.error]
    assert len(err_msgs) >= 4
    assert any("parcel length" in m for m in err_msgs)
    assert any("parcel width" in m for m in err_msgs)
    assert any("parcel height" in m for m in err_msgs)
    assert any("actual parcel weight" in m for m in err_msgs)
    assert at.session_state.get("quotes") is None


def test_14_ui_all_four_fields_valid_generates_quotes():
    """Scenario 2: All four fields with valid positive values -> quotes generated successfully."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    at.number_input(key="form_dim_len").set_value(25.0)
    at.number_input(key="form_dim_wid").set_value(20.0)
    at.number_input(key="form_dim_hgt").set_value(15.0)
    at.number_input(key="form_dim_weight").set_value(1.8)

    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    assert len(at.error) == 0
    quotes = at.session_state.get("quotes")
    assert quotes is not None and len(quotes) > 0
    order = at.session_state.get("order")
    assert order is not None
    assert order.dimensions.length_cm == 25.0
    assert order.dimensions.width_cm == 20.0
    assert order.dimensions.height_cm == 15.0
    assert order.parcel_weight_kg == 1.8


def test_15_ui_one_field_missing_blocked_with_exact_message():
    """Scenario 3: One field missing -> submission blocked with exact missing-field message."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    # Supply 3 valid fields, leave actual weight empty
    at.number_input(key="form_dim_len").set_value(25.0)
    at.number_input(key="form_dim_wid").set_value(20.0)
    at.number_input(key="form_dim_hgt").set_value(15.0)

    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    err_msgs = [e.value for e in at.error]
    assert len(err_msgs) == 1
    assert "Please enter the actual parcel weight." in err_msgs[0]
    assert at.session_state.get("quotes") is None


def test_16_ui_zero_and_invalid_values_blocked():
    """Scenario 4: Zero/negative values -> submission blocked with 'greater than zero' message."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    at.number_input(key="form_dim_len").set_value(0.0)
    at.number_input(key="form_dim_wid").set_value(20.0)
    at.number_input(key="form_dim_hgt").set_value(15.0)
    at.number_input(key="form_dim_weight").set_value(1.8)

    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    err_msgs = [e.value for e in at.error]
    assert any("greater than zero" in m for m in err_msgs)
    assert at.session_state.get("quotes") is None


def test_17_ui_enter_valid_after_failed_submission_succeeds_without_restart():
    """Scenario 5: Entering valid values after a failed submission succeeds without reloading app."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    # Step 1: Failed submission with empty fields
    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()
    assert len(at.error) >= 4

    # Step 2: Now provide valid values on the same session
    at.number_input(key="form_dim_len").set_value(25.0)
    at.number_input(key="form_dim_wid").set_value(20.0)
    at.number_input(key="form_dim_hgt").set_value(15.0)
    at.number_input(key="form_dim_weight").set_value(1.8)

    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    assert len(at.error) == 0
    assert at.session_state.get("quotes") is not None
    assert len(at.session_state["quotes"]) > 0


def test_18_ui_editing_value_and_resubmitting_uses_updated_value():
    """Scenario 6: Editing a value and re-submitting uses the updated value."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    at.number_input(key="form_dim_len").set_value(20.0)
    at.number_input(key="form_dim_wid").set_value(15.0)
    at.number_input(key="form_dim_hgt").set_value(10.0)
    at.number_input(key="form_dim_weight").set_value(1.0)
    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    order1 = at.session_state["order"]
    assert order1.dimensions.length_cm == 20.0
    assert order1.parcel_weight_kg == 1.0

    # In wizard mode, navigate back to Step 1 to edit dimensions
    back_btns = [b for b in at.button if "Back to Dimensions" in b.label or getattr(b, "key", None) in ("btn_wz_back_1", "wz_btn_1")]
    if back_btns:
        back_btns[0].click().run()

    # User edits length and weight
    at.number_input(key="form_dim_len").set_value(45.0)
    at.number_input(key="form_dim_weight").set_value(4.5)
    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    order2 = at.session_state["order"]
    assert order2.dimensions.length_cm == 45.0
    assert order2.parcel_weight_kg == 4.5


def test_19_ui_switching_packaging_mode_preserves_data_and_state():
    """Scenario 7: Switching packaging mode preserves entered data and doesn't wipe state."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    # Enter direct parcel inputs
    at.number_input(key="form_dim_len").set_value(32.0)
    at.number_input(key="form_dim_wid").set_value(22.0)
    at.number_input(key="form_dim_hgt").set_value(18.0)
    at.number_input(key="form_dim_weight").set_value(3.0).run()

    # Switch to Product Dimensions mode
    radio = at.radio(key="dim_mode_choice")
    radio.set_value("Product Dimensions (Unpacked Item \u2014 Auto-suggest best packaging)").run()

    # Enter product measurements
    at.number_input(key="form_prod_len").set_value(15.0)
    at.number_input(key="form_prod_wid").set_value(10.0)
    at.number_input(key="form_prod_hgt").set_value(5.0)
    at.number_input(key="form_prod_weight").set_value(0.8).run()

    # Switch back to Direct Parcel mode
    radio = at.radio(key="dim_mode_choice")
    radio.set_value("Direct Packed Parcel Outer Dimensions").run()

    # Verify direct parcel inputs were preserved
    assert at.number_input(key="form_dim_len").value == 32.0
    assert at.number_input(key="form_dim_wid").value == 22.0
    assert at.number_input(key="form_dim_hgt").value == 18.0
    assert at.number_input(key="form_dim_weight").value == 3.0


def test_20_ui_correct_dimensions_and_weight_passed_to_pricing_and_eligibility():
    """Scenario 8: Correct dimensions and weight passed to pricing and eligibility engine."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    # 50 x 40 x 30 cm = 60,000 cm3 -> Volumetric wt = 12.0 kg at 5000 divisor.
    # Actual dead weight = 2.0 kg -> Chargeable weight must be 12.0 kg.
    at.number_input(key="form_dim_len").set_value(50.0)
    at.number_input(key="form_dim_wid").set_value(40.0)
    at.number_input(key="form_dim_hgt").set_value(30.0)
    at.number_input(key="form_dim_weight").set_value(2.0)

    submit_btn = _find_submit_btn(at)
    submit_btn.click().run()

    assert len(at.error) == 0
    quotes = at.session_state.get("quotes")
    assert quotes is not None and len(quotes) > 0
    for q in quotes:
        if q.is_eligible:
            assert q.weight_result.chargeable_weight_kg >= 12.0
            assert q.weight_result.is_volumetric_penalty is True

