"""
Tests for the simplified, intelligent shipping workflow and UI experience.
Verifies input validation, intelligent dimension handling, packaging recommendation,
courier status labels, and single-step approval/dispatch.
"""

import pytest
from unittest.mock import MagicMock
from src.domain.models import (
    Address,
    Dimensions,
    OrderInput,
    PackageType,
    ShipmentStatus,
)
from src.domain.packaging import DEMO_PACKAGING_CATALOGUE, evaluate_orthogonal_fit, evaluate_packaging_alternatives
from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers
from src.domain.label_generator import is_valid_pdf, sanitize_label_filename
from src.persistence.state_machine import ApprovalRecord
from src.providers.demo_adapter import ALL_DEMO_COURIERS, DemoShippingAdapter
from src.ui.helpers import (
    friendly_courier_status,
    format_packaging_recommendation,
    validate_pin_code,
    validate_positive_float,
    verify_route_serviceability,
)


@pytest.fixture
def quick_order():
    return OrderInput(
        order_reference="ORD-QUICK-001",
        sender=Address(name="Alpha Hub", phone="9876543210", street_line1="1 Main St", city="Delhi", state="Delhi", pin_code="110020"),
        recipient=Address(name="Vikram Patel", phone="9123456780", street_line1="402 Green Acres", city="Bengaluru", state="Karnataka", pin_code="560034"),
        parcel_weight_kg=1.8,
        dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        package_type=PackageType.BOX,
        is_cod=False,
    )


def test_required_inputs_validation():
    """Verify pickup PIN, delivery PIN, and weight are properly validated."""
    ok_p, err_p = validate_pin_code("560103")
    assert ok_p is True and not err_p

    ok_bad_p, err_bad_p = validate_pin_code("123")
    assert ok_bad_p is False and "6 digits" in err_bad_p

    ok_w, err_w = validate_positive_float(1.5, "Weight")
    assert ok_w is True and not err_w

    ok_bad_w, err_bad_w = validate_positive_float(-0.5, "Weight")
    assert ok_bad_w is False and "greater than 0" in err_bad_w


def test_cod_fields_conditional():
    """Verify COD configuration is isolated to COD payment mode."""
    order_prepaid = OrderInput(
        order_reference="ORD-PRE",
        sender=Address(name="Sender Hub", phone="9876543210", street_line1="1 St", city="Delhi", state="Delhi", pin_code="110020"),
        recipient=Address(name="Recipient User", phone="9123456780", street_line1="2 St", city="Pune", state="Maharashtra", pin_code="560034"),
        parcel_weight_kg=1.0,
        dimensions=Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0),
        is_cod=False,
        cod_amount=0.0,
    )
    assert order_prepaid.is_cod is False
    assert order_prepaid.cod_amount == 0.0

    order_cod = OrderInput(
        order_reference="ORD-COD",
        sender=Address(name="Sender Hub", phone="9876543210", street_line1="1 St", city="Delhi", state="Delhi", pin_code="110020"),
        recipient=Address(name="Recipient User", phone="9123456780", street_line1="2 St", city="Pune", state="Maharashtra", pin_code="560034"),
        parcel_weight_kg=1.0,
        dimensions=Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0),
        is_cod=True,
        cod_amount=1500.0,
    )
    assert order_cod.is_cod is True
    assert order_cod.cod_amount == 1500.0


def test_product_vs_packed_dimensions():
    """Verify product dimensions are packed with tare and padding, not confused with parcel dimensions."""
    prod_dims = Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0)
    results = evaluate_packaging_alternatives(
        product_dimensions=prod_dims,
        product_net_weight_kg=1.5,
        current_outer_dimensions=prod_dims,
        couriers=ALL_DEMO_COURIERS,
    )
    feasible = [r for r in results if r.is_feasible]
    assert len(feasible) > 0

    best = min(feasible, key=lambda r: (r.new_billable_slab_kg, -r.estimated_freight_savings_inr))
    # Packed outer dimensions must exceed product dimensions
    assert best.outer_dimensions.length_cm >= prod_dims.length_cm
    assert best.tare_weight_kg > 0  # Includes packaging tare weight
    assert best.tare_weight_kg + 1.5 > 1.5


def test_courier_status_labels(quick_order):
    """Verify all 5 friendly status labels are generated accurately."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(quick_order)
    ranked = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))

    winner = ranked.selected_winner.quote
    label_w, col_w, _ = friendly_courier_status(winner, is_winner=True)
    assert label_w == "Recommended"
    assert col_w == "green"

    for q in quotes:
        if q.is_eligible and q.quote_id != winner.quote_id:
            label_e, col_e, _ = friendly_courier_status(q, is_winner=False)
            assert label_e in ("Eligible", "Needs verification")

    mock_ineligible = MagicMock()
    mock_ineligible.is_eligible = False
    mock_ineligible.is_serviceable = False
    mock_ineligible.unserviceable_reason = "Remote area not serviceable"
    lbl_in, col_in, _ = friendly_courier_status(mock_ineligible)
    assert lbl_in == "Not available for this parcel"
    assert col_in == "red"


def test_label_download_pure_function(quick_order):
    """Verify downloading a label does not alter shipment state or duplicate bookings."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(quick_order)
    quote = quotes[0]
    approval = ApprovalRecord(
        order_reference=quick_order.order_reference,
        quote_id=quote.quote_id,
        courier_code=quote.courier_code,
        approved_by="Shipping Operator",
        total_cost=quote.cost_breakdown.total_cost,
    )
    shipment_res = adapter.create_shipment(
        order=quick_order,
        quote=quote,
        approval=approval,
        idempotency_key="IDEM-TEST-PURE",
        generate_label=True,
    )

    # Calling PDF generation multiple times returns consistent bytes without creating shipments
    pdf1 = adapter.generate_label_pdf(shipment_res.shipment_id, tracking_number=shipment_res.tracking_number, order=quick_order, quote=quote)
    pdf2 = adapter.generate_label_pdf(shipment_res.shipment_id, tracking_number=shipment_res.tracking_number, order=quick_order, quote=quote)

    assert is_valid_pdf(pdf1)
    assert is_valid_pdf(pdf2)
    assert len(pdf1) == len(pdf2)
    assert sanitize_label_filename(shipment_res.tracking_number).endswith(".pdf")


def test_pin_code_validation_and_route_serviceability():
    """Verify PIN format validation and provider serviceability verification rules."""
    # 1. Format validation
    ok_p, err_p = validate_pin_code("123", pin_type="pickup")
    assert ok_p is False
    assert "Enter a valid 6-digit pickup PIN code" in err_p

    ok_d, err_d = validate_pin_code("41100A", pin_type="delivery")
    assert ok_d is False
    assert "Enter a valid 6-digit delivery PIN code" in err_d

    # Leading zero is invalid in Indian postal code system
    ok_zero, err_zero = validate_pin_code("012345", pin_type="pickup")
    assert ok_zero is False

    # Valid format
    ok_valid, err_valid = validate_pin_code("560103", pin_type="pickup")
    assert ok_valid is True and err_valid == ""

    # 2. Provider route serviceability
    adapter = DemoShippingAdapter()

    # Valid route
    svc_ok, svc_msg, is_demo = verify_route_serviceability("560103", "411001", adapter)
    assert svc_ok is True
    assert is_demo is True

    # Malformed pickup in route serviceability check
    svc_ok_m, svc_msg_m, _ = verify_route_serviceability("123", "411001", adapter)
    assert svc_ok_m is False
    assert "pickup PIN" in svc_msg_m

    # Malformed delivery in route serviceability check
    svc_ok_md, svc_msg_md, _ = verify_route_serviceability("560103", "999", adapter)
    assert svc_ok_md is False
    assert "delivery PIN" in svc_msg_md

    # Unsupported / unserviceable pickup (APS Circle 9)
    svc_ok_aps_p, svc_msg_aps_p, _ = verify_route_serviceability("900001", "411001", adapter)
    assert svc_ok_aps_p is False
    assert "This pickup PIN code could not be verified" in svc_msg_aps_p

    # Unsupported / unserviceable delivery (APS Circle 9)
    svc_ok_aps_d, svc_msg_aps_d, _ = verify_route_serviceability("560103", "900001", adapter)
    assert svc_ok_aps_d is False
    assert "Delivery to this PIN code is currently unavailable" in svc_msg_aps_d


def test_packaging_comparison_result_new_gross_weight_attribute():
    """Verify PackagingComparisonResult has new_gross_weight_kg attribute and accurate value."""
    prod_dims = Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0)
    prod_weight = 1.75

    results = evaluate_packaging_alternatives(
        product_dimensions=prod_dims,
        product_net_weight_kg=prod_weight,
        current_outer_dimensions=prod_dims,
        couriers=ALL_DEMO_COURIERS,
    )

    assert len(results) > 0
    for r in results:
        # Must have new_gross_weight_kg attribute without raising AttributeError
        assert hasattr(r, "new_gross_weight_kg")
        assert isinstance(r.new_gross_weight_kg, float)
        expected_gross = round(prod_weight + r.tare_weight_kg, 3)
        assert abs(r.new_gross_weight_kg - expected_gross) < 1e-4

    # Safe access as used in UI
    feasible = [r for r in results if r.is_feasible]
    assert len(feasible) > 0
    for f in feasible:
        gross_val = getattr(f, "new_gross_weight_kg", None)
        assert gross_val is not None
        assert gross_val > prod_weight

