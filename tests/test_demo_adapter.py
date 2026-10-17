"""
Unit tests for Provider Abstraction Layer, DemoShippingAdapter, and ShipSandboxClient.
Covers contract consistency, multi-courier pricing scenarios, bulky parcel handling,
serviceability rejections, and locked endpoint safety guards.
"""

import pytest
import httpx
from src.domain.models import (
    Address,
    Dimensions,
    OrderInput,
    PackageType,
)
from src.persistence.state_machine import ApprovalRecord
from src.providers.base import CourierProvider
from src.providers.demo_adapter import DemoShippingAdapter
from src.providers.registry import get_provider
from src.providers.shipsandbox_client import (
    ShipSandboxClient,
    UnconfirmedEndpointError,
)


def create_sample_order(
    parcel_weight_kg: float = 2.0,
    length_cm: float = 25.0,
    width_cm: float = 20.0,
    height_cm: float = 15.0,
    dest_pin: str = "560034",
    package_type: PackageType = PackageType.BOX,
    is_cod: bool = False,
    cod_amount: float = 0.0,
) -> OrderInput:
    """Helper to generate valid order inputs for testing."""
    return OrderInput(
        order_reference="TEST-ORD-001",
        sender=Address(
            name="Delhi Hub",
            phone="9876543210",
            street_line1="Ring Road",
            city="New Delhi",
            state="Delhi",
            pin_code="110020",
        ),
        recipient=Address(
            name="Bengaluru Buyer",
            phone="9123456780",
            street_line1="100 Feet Road",
            city="Bengaluru",
            state="Karnataka",
            pin_code=dest_pin,
        ),
        parcel_weight_kg=parcel_weight_kg,
        dimensions=Dimensions(length_cm=length_cm, width_cm=width_cm, height_cm=height_cm),
        package_type=package_type,
        is_cod=is_cod,
        cod_amount=cod_amount,
    )


def test_provider_contract_consistency():
    """Verify DemoShippingAdapter implements CourierProvider with required attributes."""
    adapter = DemoShippingAdapter()
    assert isinstance(adapter, CourierProvider)
    assert adapter.is_demo is True
    assert adapter.provider_code == "DEMO_SHIPPING_ADAPTER"


def test_three_demo_couriers_same_parcel_scenario():
    """
    Scenario: Same parcel across all three couriers.
    Parcel: 2.0 kg dead weight, 25 x 20 x 15 cm = 7500 cm3
    - Air Express: Divisor 5000 -> 1.5 kg vol wt -> 2.0 kg dead wt dominates -> 2.0 kg slab
    - Surface Std: Divisor 4500 -> 1.667 kg vol wt -> 2.0 kg dead wt dominates -> 2.0 kg slab
    - Heavy Cargo: Divisor 4000 -> 1.875 kg vol wt -> Min Billable Weight is 5.0 kg! -> 5.0 kg slab
    """
    order = create_sample_order(parcel_weight_kg=2.0, length_cm=25.0, width_cm=20.0, height_cm=15.0)
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    assert len(quotes) == 3
    quote_map = {q.courier_code: q for q in quotes}

    # Air Express
    air = quote_map["DEMO_AIR_EXPRESS"]
    assert air.is_serviceable is True
    assert air.is_eligible is True
    assert air.is_demo_data is True
    assert air.weight_result.billing_slab_kg == 2.0
    assert air.estimated_delivery_days == 2
    assert air.cost_breakdown is not None
    assert air.cost_breakdown.total_cost > 0.0

    # Surface Standard
    surface = quote_map["DEMO_SURFACE_STD"]
    assert surface.is_serviceable is True
    assert surface.is_eligible is True
    assert surface.weight_result.billing_slab_kg == 2.0
    assert surface.estimated_delivery_days == 5

    # Heavy Cargo - enforces 5.0 kg min billable weight
    cargo = quote_map["DEMO_ECONOMY_CARGO"]
    assert cargo.is_serviceable is True
    assert cargo.is_eligible is True
    assert cargo.weight_result.billing_slab_kg == 5.0  # Min slab enforced
    assert cargo.estimated_delivery_days == 7


def test_large_lightweight_parcel_scenario():
    """
    Scenario: Large lightweight parcel (bulky cushion).
    Dead weight: 0.5 kg
    Dimensions: 60 x 50 x 40 cm = 120,000 cm3
    - Air Express (Divisor 5000): 120,000 / 5000 = 24.0 kg slab
    - Surface Std (Divisor 4500): 120,000 / 4500 = 26.667 kg -> 27.0 kg slab
    - Heavy Cargo (Divisor 4000): 120,000 / 4000 = 30.0 kg slab
    """
    order = create_sample_order(parcel_weight_kg=0.5, length_cm=60.0, width_cm=50.0, height_cm=40.0)
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    quote_map = {q.courier_code: q for q in quotes}

    air = quote_map["DEMO_AIR_EXPRESS"]
    surface = quote_map["DEMO_SURFACE_STD"]
    cargo = quote_map["DEMO_ECONOMY_CARGO"]

    assert air.weight_result.volumetric_weight_kg == 24.0
    assert air.weight_result.billing_slab_kg == 24.0

    assert surface.weight_result.volumetric_weight_kg == pytest.approx(26.667, 0.01)
    assert surface.weight_result.billing_slab_kg == 27.0

    assert cargo.weight_result.volumetric_weight_kg == 30.0
    assert cargo.weight_result.billing_slab_kg == 30.0


def test_unserviceable_destination_remote_pin():
    """
    Destination PIN starts with '79' (Remote North-East pin e.g. 790001).
    Air Express must be marked unserviceable and ineligible.
    Cost breakdown must be None (never silently zero!).
    """
    order = create_sample_order(dest_pin="790001")
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    quote_map = {q.courier_code: q for q in quotes}
    air = quote_map["DEMO_AIR_EXPRESS"]

    assert air.is_serviceable is False
    assert air.is_eligible is False
    assert air.cost_breakdown is None  # Never silently 0
    assert "remote region" in air.unserviceable_reason

    # Surface Standard remains serviceable
    surface = quote_map["DEMO_SURFACE_STD"]
    assert surface.is_serviceable is True
    assert surface.is_eligible is True


def test_unserviceable_package_type_restriction():
    """
    Package type is FLYER (polybag).
    Heavy Cargo must be marked unserviceable because it only takes boxes/crates.
    """
    order = create_sample_order(package_type=PackageType.FLYER)
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    quote_map = {q.courier_code: q for q in quotes}
    cargo = quote_map["DEMO_ECONOMY_CARGO"]

    assert cargo.is_serviceable is False
    assert cargo.is_eligible is False
    assert cargo.cost_breakdown is None
    assert "Package type 'flyer' is not supported" in cargo.unserviceable_reason


def test_unserviceable_cod_ceiling_exceeded():
    """
    COD amount of ₹15,000 exceeds Heavy Cargo's ₹10,000 ceiling.
    Heavy Cargo must be marked unserviceable for this order.
    """
    order = create_sample_order(is_cod=True, cod_amount=15000.0)
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    quote_map = {q.courier_code: q for q in quotes}
    cargo = quote_map["DEMO_ECONOMY_CARGO"]

    assert cargo.is_serviceable is False
    assert cargo.is_eligible is False
    assert "exceeds courier collection ceiling" in cargo.unserviceable_reason


def test_create_shipment_ineligible_quote_rejected():
    """Attempting to dispatch an unserviceable or ineligible quote raises ValueError."""
    order = create_sample_order(dest_pin="790001")
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    ineligible_quote = [q for q in quotes if not q.is_eligible][0]
    approval = ApprovalRecord(approved_by="Admin", quote_id=ineligible_quote.quote_id, total_cost=0.0)

    with pytest.raises(ValueError, match="Cannot create shipment with an ineligible quote"):
        adapter.create_shipment(order, ineligible_quote, approval, "IDEM-TEST")


def test_create_shipment_success_contract():
    """Eligible quote returns ShipmentResult without fabricating label URLs."""
    order = create_sample_order()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)
    eligible_quote = quotes[0]

    approval = ApprovalRecord(
        approved_by="Operations Lead",
        quote_id=eligible_quote.quote_id,
        total_cost=eligible_quote.cost_breakdown.total_cost,
    )
    result = adapter.create_shipment(order, eligible_quote, approval, "IDEM-KEY-999")

    assert result.tracking_number.startswith("DEMO-TRK-")
    assert result.label_status == "UNSUPPORTED"
    assert result.label_url_or_data is None  # No fake URL
    assert result.is_demo_data is True


def test_shipsandbox_locked_endpoints():
    """All unconfirmed ShipSandbox endpoints must raise UnconfirmedEndpointError."""
    client = ShipSandboxClient()
    order = create_sample_order()
    dummy_quote = DemoShippingAdapter().get_rates(order)[0]
    dummy_approval = ApprovalRecord(approved_by="Admin", quote_id="Q", total_cost=100.0)

    with pytest.raises(UnconfirmedEndpointError):
        client.get_rates(order)

    with pytest.raises(UnconfirmedEndpointError):
        client.create_shipment(order, dummy_quote, dummy_approval, "IDEM")

    with pytest.raises(UnconfirmedEndpointError):
        client.download_label("SHP-123")

    with pytest.raises(UnconfirmedEndpointError):
        client.schedule_pickup("SHP-123")

    with pytest.raises(UnconfirmedEndpointError):
        client.verify_webhook(b"payload", "sig")

    with pytest.raises(UnconfirmedEndpointError):
        client.cancel_shipment("SHP-123")


def test_shipsandbox_tracking_unreachable_safe_fallback():
    """When ShipSandbox server is unreachable, get_tracking returns safe error model without fabricating data."""
    client = ShipSandboxClient(base_url="http://127.0.0.1:59999", timeout_seconds=0.1)
    tracking = client.get_tracking("TEST-TRK-001")

    assert tracking.tracking_number == "TEST-TRK-001"
    assert tracking.current_status == "UNREACHABLE"
    assert len(tracking.missing_evidence) > 0
    assert tracking.is_delivered is False


def test_provider_registry():
    """Verify registry loads appropriate providers and rejects unknown modes."""
    demo_provider = get_provider("demo")
    assert isinstance(demo_provider, DemoShippingAdapter)

    sandbox_provider = get_provider("shipsandbox")
    assert isinstance(sandbox_provider, ShipSandboxClient)

    with pytest.raises(ValueError, match="Unknown shipping provider mode"):
        get_provider("unsupported_carrier_xyz")
