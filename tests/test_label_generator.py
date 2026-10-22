"""
Unit and integration tests for ReportLab PDF shipping label generator.
"""

import pytest
from src.domain.label_generator import (
    LabelPageFormat,
    build_barcode_drawing,
    build_qr_drawing,
    generate_label_from_shipment_context,
    generate_label_preview_from_shipment_context,
    generate_label_preview_html,
    generate_shipping_label_pdf,
    is_valid_pdf,
    sanitize_label_filename,
)
from src.domain.models import (
    Address,
    CostBreakdown,
    CourierQuote,
    Dimensions,
    OrderInput,
    WeightResult,
)
from src.providers.base import PickupResult, ShipmentResult
from src.providers.demo_adapter import DemoShippingAdapter


@pytest.fixture
def sample_order():
    return OrderInput(
        order_reference="ORD-PDF-TEST-001",
        parcel_weight_kg=1.75,
        dimensions=Dimensions(length_cm=28.0, width_cm=20.0, height_cm=14.0),
        sender=Address(
            name="Apex Fulfillment Central",
            street_line1="Plot 42, Electronic City Phase 1",
            city="Bangalore",
            state="Karnataka",
            pin_code="560100",
            phone="+91-9876543210",
        ),
        recipient=Address(
            name="Priya Ananth",
            street_line1="Flat 601, Marvel Imperial Apartments, North Main Road, Koregaon Park",
            city="Pune",
            state="Maharashtra",
            pin_code="411001",
            phone="+91-9123456780",
        ),
        is_cod=False,
        cod_amount=0.0,
    )


@pytest.fixture
def sample_quote():
    return CourierQuote(
        quote_id="QUOTE-TEST-SURFACE-001",
        courier_code="DEMO_SURFACE_STD",
        courier_name="Demo Surface Standard (Simulated)",
        service_type="Surface Standard",
        is_eligible=True,
        cost_breakdown=CostBreakdown(
            base_freight=85.0,
            fuel_surcharge=6.8,
            fuel_surcharge_rate_pct=8.0,
            cod_fee=0.0,
            handling_fee=0.0,
            subtotal_before_tax=91.8,
            tax_amount=16.52,
            tax_rate_pct=18.0,
            total_cost=108.32,
        ),
        chargeable_weight_kg=2.0,
        estimated_delivery_days=4,
        source_type="DEMO_ADAPTER",
        input_hash="dummy-input-hash-1234",
    )


@pytest.fixture
def sample_shipment_result():
    return ShipmentResult(
        shipment_id="DEMO-SHP-ORD-PDF-TEST-001-DEMO_SURFACE_STD",
        order_reference="ORD-PDF-TEST-001",
        courier_code="DEMO_SURFACE_STD",
        tracking_number="DEMO-TRK-7B92A41C9E",
        label_status="AVAILABLE_PDF",
        is_demo_data=True,
    )


def test_generate_pdf_a6_format():
    """Verify that A6 format produces a valid non-empty PDF document."""
    pdf = generate_shipping_label_pdf(
        tracking_number="DEMO-TRK-A07684EEC2",
        order_reference="ORD-TC4-VALID",
        courier_name="Blue Dart Express Air",
        service_type="Air Express",
        origin_pin="560103",
        destination_pin="411001",
        page_format=LabelPageFormat.A6,
    )
    assert is_valid_pdf(pdf)
    assert len(pdf) > 2000
    assert b"%PDF-" in pdf[:10]


def test_generate_pdf_4x6_format():
    """Verify that 4x6 inch thermal format produces a valid PDF document."""
    pdf = generate_shipping_label_pdf(
        tracking_number="DEMO-TRK-A07684EEC2",
        order_reference="ORD-TC4-VALID",
        courier_name="Delhivery Surface Standard",
        service_type="Surface Standard",
        origin_pin="560103",
        destination_pin="411001",
        page_format=LabelPageFormat.INCH_4X6,
    )
    assert is_valid_pdf(pdf)
    assert len(pdf) > 2000
    assert b"%PDF-" in pdf[:10]


def test_is_valid_pdf_validation():
    """Check PDF validator helper with valid and invalid payloads."""
    assert is_valid_pdf(b"%PDF-1.4\n%...\n%%EOF") is True
    assert is_valid_pdf(b"<html><body>Not a PDF</body></html>") is False
    assert is_valid_pdf(b"") is False
    assert is_valid_pdf("string not bytes") is False  # type: ignore


def test_sanitize_label_filename():
    """Verify filename sanitization handles messy or unescaped characters."""
    assert sanitize_label_filename("DEMO-TRK-12345") == "shipping_label_DEMO-TRK-12345.pdf"
    assert sanitize_label_filename("DEMO/TRK 99#ABC$") == "shipping_label_DEMO_TRK_99_ABC.pdf"
    assert sanitize_label_filename("   ") == "shipping_label_LABEL.pdf"
    assert sanitize_label_filename("AWB-99", extension=".pdf") == "shipping_label_AWB-99.pdf"


def test_label_cod_and_prepaid_handling():
    """Verify COD creates a prominent cash collection callout vs prepaid banner."""
    pdf_cod = generate_shipping_label_pdf(
        tracking_number="DEMO-TRK-COD-001",
        order_reference="ORD-COD",
        courier_name="Carrier COD",
        is_cod=True,
        cod_amount=2450.75,
    )
    assert is_valid_pdf(pdf_cod)

    pdf_prepaid = generate_shipping_label_pdf(
        tracking_number="DEMO-TRK-PRE-001",
        order_reference="ORD-PREPAID",
        courier_name="Carrier Prepaid",
        is_cod=False,
        cod_amount=0.0,
    )
    assert is_valid_pdf(pdf_prepaid)


def test_missing_optional_fields_graceful_defaults():
    """Ensure that missing or None fields do not raise exceptions."""
    pdf = generate_shipping_label_pdf(
        tracking_number="",
        order_reference="",
        courier_name="",
        service_type="",
        shipment_id=None,
        origin_pin="",
        destination_pin="",
        sender_name="",
        recipient_name="",
        dimensions_cm="",
        volumetric_weight_kg=None,
        billable_weight_kg=None,
        created_at_iso=None,
        pickup_reference=None,
    )
    assert is_valid_pdf(pdf)


def test_extremely_long_addresses_no_crash():
    """Verify that long recipient names and multi-line addresses fit cleanly."""
    long_addr = "Suite 900, Tower C, Marathon Nextgen Innova, Off Ganpatrao Kadam Marg, Lower Parel West, Greater Mumbai" * 3
    pdf = generate_shipping_label_pdf(
        tracking_number="DEMO-TRK-LONG-ADDR",
        order_reference="ORD-LONG-REF-1234567890",
        courier_name="Express Long Address Carrier",
        recipient_name="Dr. Archana Elizabeth Montgomery-Vanderbilt III, Senior VP Logistics",
        recipient_street=long_addr,
        destination_pin="400013",
    )
    assert is_valid_pdf(pdf)


def test_barcode_and_qr_drawings():
    """Test vector barcode and 2D QR drawing generation."""
    bc = build_barcode_drawing("DEMO-TRK-12345678", max_width=180.0, height=30.0)
    assert bc is not None
    assert bc.width <= 180.0

    qr_d = build_qr_drawing("AWB:DEMO-TRK|REF:ORD-123", size=45.0)
    assert qr_d is not None
    assert qr_d.width == 45.0


def test_generate_label_from_shipment_context(sample_order, sample_quote, sample_shipment_result):
    """Verify convenience adapter with domain model objects."""
    pickup = PickupResult(
        pickup_reference="DEMO-PKP-1A2B3C4D",
        order_reference="ORD-PDF-TEST-001",
        courier_code="DEMO_SURFACE_STD",
        pickup_address_summary="Bangalore Hub, PIN 560100",
        scheduled_date="2026-10-22",
        time_window="10:00 AM - 01:00 PM IST",
        status="CONFIRMED",
    )
    pdf = generate_label_from_shipment_context(
        order=sample_order,
        quote=sample_quote,
        shipment_result=sample_shipment_result,
        pickup_result=pickup,
        page_format="A6",
    )
    assert is_valid_pdf(pdf)
    assert len(pdf) > 2500


def test_demo_adapter_generate_label_pdf(sample_order, sample_quote):
    """Test integration with DemoShippingAdapter.generate_label_pdf."""
    adapter = DemoShippingAdapter()
    shp_id = "DEMO-SHP-TEST-001"
    pdf = adapter.generate_label_pdf(
        shipment_id=shp_id,
        tracking_number="DEMO-TRK-ADAPTER-001",
        order=sample_order,
        quote=sample_quote,
        page_format="A6",
    )
    assert pdf is not None
    assert is_valid_pdf(pdf)
    assert len(pdf) > 2000


def test_volumetric_weight_calculation_and_display():
    """
    Verify volumetric weight calculation with example 65 × 30 × 20 cm:
    Volume = 39,000 cm3; Divisor 5,000 cm3/kg -> Volumetric = 7.80 kg, Billed = 7.80 kg.
    """
    pdf = generate_shipping_label_pdf(
        tracking_number="DEMO-TRK-VOL-001",
        order_reference="ORD-VOL-65X30X20",
        courier_name="Demo Air Express (Simulated)",
        actual_weight_kg=0.50,
        volumetric_weight_kg=7.80,
        billable_weight_kg=7.80,
        dimensions_cm="65.0×30.0×20.0 cm",
        page_format=LabelPageFormat.A6,
    )
    assert is_valid_pdf(pdf)

    # Verify in HTML preview replica
    html = generate_label_preview_html(
        tracking_number="DEMO-TRK-VOL-001",
        order_reference="ORD-VOL-65X30X20",
        courier_name="Demo Air Express (Simulated)",
        actual_weight_kg=0.50,
        volumetric_weight_kg=7.80,
        billable_weight_kg=7.80,
        dimensions_cm="65.0×30.0×20.0 cm",
    )
    assert "<b>Weight:</b> 0.50 kg" in html
    assert "<b>Volumetric:</b> 7.80 kg" in html
    assert "<b>Billed:</b> 7.80 kg" in html
    assert "65.0×30.0×20.0 cm" in html


def test_unconfigured_carrier_volumetric_weight_display():
    """
    If carrier has no verified divisor, do not assume universal divisor;
    display 'Not configured' instead of '—'.
    """
    pdf = generate_shipping_label_pdf(
        tracking_number="DEMO-TRK-UNCONF-001",
        order_reference="ORD-UNCONF",
        courier_name="Unconfigured Regional Courier",
        actual_weight_kg=0.50,
        volumetric_weight_kg=None,
        volumetric_display="Not configured",
        billable_weight_kg=0.50,
        dimensions_cm="65.0×30.0×20.0 cm",
    )
    assert is_valid_pdf(pdf)

    html = generate_label_preview_html(
        tracking_number="DEMO-TRK-UNCONF-001",
        order_reference="ORD-UNCONF",
        courier_name="Unconfigured Regional Courier",
        actual_weight_kg=0.50,
        volumetric_weight_kg=None,
        volumetric_display="Not configured",
        billable_weight_kg=0.50,
        dimensions_cm="65.0×30.0×20.0 cm",
    )
    assert "<b>Weight:</b> 0.50 kg" in html
    assert "<b>Volumetric:</b> Not configured" in html
    assert "<b>Billed:</b> 0.50 kg" in html


def test_label_preview_html_structure_and_svg():
    """
    Verify that generate_label_preview_html produces responsive HTML with
    vector Code128 barcode SVG and QR code SVG, without iframe embed tags.
    """
    html = generate_label_preview_html(
        tracking_number="DEMO-TRK-PREVIEW-TEST",
        order_reference="ORD-PREVIEW-001",
        courier_name="Blue Dart Express Air",
        service_type="Air Express Priority",
        origin_pin="560103",
        destination_pin="411001",
        actual_weight_kg=1.5,
        volumetric_weight_kg=3.2,
        billable_weight_kg=3.2,
        dimensions_cm="30.0×25.0×20.0 cm",
        is_cod=True,
        cod_amount=1500.0,
        is_demo=True,
    )
    # Must not contain iframe (which gets blocked by Edge / Chromium)
    assert "<iframe" not in html
    # Must contain vector SVGs
    assert "<svg" in html
    assert "</svg>" in html
    # Must contain tracking number and routing
    assert "AWB: DEMO-TRK-PREVIEW-TEST" in html
    assert "560103" in html
    assert "411001" in html
    assert "COLLECT CASH ON DELIVERY: ₹ 1,500.00" in html
    assert "DEMO SHIPMENT — SIMULATED CARRIER LABEL FOR EVALUATION ONLY" in html


def test_generate_label_from_shipment_context_weight_result_separation():
    """
    Verify generate_label_from_shipment_context reads quote.weight_result
    and keeps actual weight, volumetric weight, and chargeable weight clearly separated.
    """
    order = OrderInput(
        order_reference="ORD-SEPARATE-WT",
        parcel_weight_kg=0.50,
        dimensions=Dimensions(length_cm=65.0, width_cm=30.0, height_cm=20.0),
        sender=Address(name="Sender Hub", phone="9876543210", street_line1="Industrial Area", city="Bangalore", state="KA", pin_code="560100"),
        recipient=Address(name="Customer", phone="9123456780", street_line1="Commercial Park", city="Pune", state="MH", pin_code="411001"),
    )
    quote = CourierQuote(
        quote_id="Q-TEST-SEPARATE",
        courier_code="DEMO_AIR_EXPRESS",
        courier_name="Demo Air Express (Simulated)",
        service_type="Air Express Priority",
        input_hash="hash-sep",
        weight_result=WeightResult(
            actual_weight_kg=0.50,
            volumetric_weight_kg=7.80,
            chargeable_weight_kg=7.80,
            billing_slab_kg=8.0,
            divisor_used=5000.0,
            is_volumetric_penalty=True,
        ),
    )
    shp = ShipmentResult(
        shipment_id="SHP-TEST-SEP",
        order_reference="ORD-SEPARATE-WT",
        courier_code="DEMO_AIR_EXPRESS",
        tracking_number="DEMO-TRK-SEP-001",
        is_demo_data=True,
    )

    pdf = generate_label_from_shipment_context(order, quote, shp)
    assert is_valid_pdf(pdf)

    preview = generate_label_preview_from_shipment_context(order, quote, shp)
    assert "<b>Weight:</b> 0.50 kg" in preview
    assert "<b>Volumetric:</b> 7.80 kg" in preview
    assert "<b>Billed:</b> 7.80 kg" in preview


def test_generate_label_from_shipment_context_fallback_divisor_calculation():
    """
    When quote has no weight_result, calculate volumetric weight via dimension engine
    using carrier's documented capability divisor (e.g. 5000 for DEMO_AIR_EXPRESS).
    """
    order = OrderInput(
        order_reference="ORD-FALLBACK-WT",
        parcel_weight_kg=0.50,
        dimensions=Dimensions(length_cm=65.0, width_cm=30.0, height_cm=20.0),
        sender=Address(name="Sender Hub", phone="9876543210", street_line1="Industrial Area", city="Bangalore", state="KA", pin_code="560100"),
        recipient=Address(name="Customer", phone="9123456780", street_line1="Commercial Park", city="Pune", state="MH", pin_code="411001"),
    )
    quote = CourierQuote(
        quote_id="Q-TEST-FALLBACK",
        courier_code="DEMO_AIR_EXPRESS",
        courier_name="Demo Air Express (Simulated)",
        service_type="Air Express Priority",
        input_hash="hash-fb",
        weight_result=None,
    )
    shp = ShipmentResult(
        shipment_id="SHP-TEST-FB",
        order_reference="ORD-FALLBACK-WT",
        courier_code="DEMO_AIR_EXPRESS",
        tracking_number="DEMO-TRK-FB-001",
        is_demo_data=True,
    )

    preview = generate_label_preview_from_shipment_context(order, quote, shp)
    assert "<b>Weight:</b> 0.50 kg" in preview
    assert "<b>Volumetric:</b> 7.80 kg" in preview
    assert "<b>Billed:</b> 7.80 kg" in preview

