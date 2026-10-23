"""
Unit and integration tests for shipping label verification, demo PDF fallback generation,
honest status reporting, and carrier compliance constraints.
"""

import pytest
from src.domain.models import Address, CourierQuote, Dimensions, OrderInput, PackageType
from src.persistence.state_machine import ApprovalRecord
from src.domain.verification import ShipmentVerificationService, VerificationStatus
from src.providers.base import PickupResult, ShipmentResult
from src.providers.demo_adapter import DemoShippingAdapter


@pytest.fixture
def sample_order():
    return OrderInput(
        order_reference="ORD-LABEL-TEST-001",
        sender=Address(
            name="Alpha Hub",
            phone="9876543210",
            street_line1="12 Industrial Area",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560103",
        ),
        recipient=Address(
            name="Beta Enterprise",
            phone="9123456780",
            street_line1="45 Tech Park",
            city="Pune",
            state="Maharashtra",
            pin_code="411001",
        ),
        parcel_weight_kg=2.5,
        dimensions=Dimensions(length_cm=30.0, width_cm=20.0, height_cm=15.0),
        package_type=PackageType.BOX,
        is_cod=False,
    )


@pytest.fixture
def sample_quote(sample_order):
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(sample_order)
    return quotes[0]


@pytest.fixture
def sample_approval(sample_quote):
    return ApprovalRecord(
        approved_by="Auditor / Compliance Test",
        quote_id=sample_quote.quote_id,
        total_cost=250.0,
        notes="Automated verification test",
    )


@pytest.fixture
def ver_service():
    return ShipmentVerificationService()


def test_supported_carrier_issued_label(ver_service, sample_order, sample_quote, sample_approval):
    """
    1. Supported carrier-issued label format:
    Verifies that when carrier supplies an automated label format containing valid references,
    the label correlation checkpoint passes with official carrier attributes.
    """
    live_quote = sample_quote.model_copy(update={"is_demo_data": False})
    shipment_res = ShipmentResult(
        success=True,
        shipment_id="SHP-CARRIER-LIVE-123",
        order_reference=sample_order.order_reference,
        courier_code=live_quote.courier_code,
        tracking_number="LIVE-TRK-987654321",
        label_status="AVAILABLE_PDF",
        label_url_or_data=f"https://api.shipengine.com/labels/LIVE-TRK-987654321.pdf?ref={sample_order.order_reference}",
        is_demo_data=False,
    )

    report = ver_service.verify_shipment(
        order=sample_order,
        quote=live_quote,
        approval=sample_approval,
        shipment_result=shipment_res,
    )

    assert report.label_details is not None
    assert report.label_details.is_carrier_issued is True
    assert report.label_details.is_demo_label is False
    assert report.label_details.carrier_label_status == VerificationStatus.PASS
    assert report.label_details.provenance_status == VerificationStatus.PASS
    assert report.label_details.file_generated is True
    assert report.label_details.label_references_matched is True

    crit3 = next(c for c in report.checks if c.criterion_id == "CRIT_3_LABEL_CORRELATION")
    assert crit3.passed is True
    assert crit3.status == VerificationStatus.PASS


def test_unsupported_carrier_label_with_successful_demo_pdf_generation(
    ver_service, sample_order, sample_quote, sample_approval
):
    """
    2. Unsupported carrier-issued label with successful local demo PDF generation:
    When provider cannot issue carrier labels in demo mode, but shipment data is complete,
    generates a printable PDF demo label and marks individual indicators honestly.
    """
    shipment_res = ShipmentResult(
        success=True,
        shipment_id="DEMO-SHP-UNSUP-001",
        order_reference=sample_order.order_reference,
        courier_code=sample_quote.courier_code,
        tracking_number="DEMO-TRK-ABCD1234EF",
        label_status="UNSUPPORTED",
        label_url_or_data=None,
        is_demo_data=True,
    )

    report = ver_service.verify_shipment(
        order=sample_order,
        quote=sample_quote,
        approval=sample_approval,
        shipment_result=shipment_res,
    )

    assert report.label_details is not None
    # Local PDF must be successfully generated
    assert report.label_details.file_generated is True
    assert report.label_details.local_pdf_status == VerificationStatus.PASS

    # Carrier label support must honestly be UNSUPPORTED
    assert report.label_details.carrier_label_status == VerificationStatus.UNSUPPORTED
    assert report.label_details.is_carrier_issued is False

    # Provenance must honestly be DEMO ONLY
    assert report.label_details.provenance_status == VerificationStatus.DEMO_ONLY
    assert report.label_details.is_demo_label is True

    # Reassuring message must be present
    assert "PDF demo label generated successfully" in report.label_details.summary_message
    assert "The selected provider does not supply an official carrier label in this mode" in report.label_details.summary_message

    # Criterion 3 should pass in demo mode
    crit3 = next(c for c in report.checks if c.criterion_id == "CRIT_3_LABEL_CORRELATION")
    assert crit3.passed is True

    # Subchecks must be populated
    assert len(crit3.subchecks) >= 5
    sub_map = {sc["id"]: sc for sc in crit3.subchecks}
    assert sub_map["SUB_PDF_FILE_GEN"]["status"] == "PASS"
    assert sub_map["SUB_CARRIER_SUPPORT"]["status"] == "UNSUPPORTED"
    assert sub_map["SUB_LABEL_PROVENANCE"]["status"] == "DEMO ONLY"


def test_mismatched_order_and_awb_references(ver_service, sample_order, sample_quote, sample_approval):
    """
    3. Mismatched order and AWB references:
    Verifies that if shipment record has a mismatched order reference, the check fails.
    """
    mismatched_shipment = ShipmentResult(
        success=True,
        shipment_id="DEMO-SHP-MISMATCH-001",
        order_reference="DIFFERENT-ORDER-999",  # Does NOT match sample_order.order_reference
        courier_code=sample_quote.courier_code,
        tracking_number="DEMO-TRK-MISMATCH",
        label_status="AVAILABLE_HTML",
        label_url_or_data="<html><body>AWB: DEMO-TRK-MISMATCH Ref: DIFFERENT-ORDER-999</body></html>",
        is_demo_data=True,
    )

    report = ver_service.verify_shipment(
        order=sample_order,
        quote=sample_quote,
        approval=sample_approval,
        shipment_result=mismatched_shipment,
    )

    # Check 2 (AWB Order Match) must FAIL
    crit2 = next(c for c in report.checks if c.criterion_id == "CRIT_2_AWB_ORDER_MATCH")
    assert crit2.passed is False
    assert crit2.status in (VerificationStatus.FAIL, VerificationStatus.FAILED)

    # Check 3 (Label Correlation) must FAIL because order reference is missing from label
    crit3 = next(c for c in report.checks if c.criterion_id == "CRIT_3_LABEL_CORRELATION")
    assert crit3.passed is False


def test_missing_shipment_data_causing_failed_or_unverified_generation(
    ver_service, sample_order, sample_quote, sample_approval
):
    """
    4. Missing shipment data causing failed or unverified PDF generation:
    When required shipment fields are missing (e.g. no dimensions),
    PDF generation cannot proceed and status must be NOT VERIFIED / FAIL.
    """
    incomplete_order = sample_order.model_copy()
    incomplete_order.__dict__["dimensions"] = None  # Missing dimensions!

    shipment_res = ShipmentResult(
        success=True,
        shipment_id="DEMO-SHP-INCOMPLETE-001",
        order_reference=incomplete_order.order_reference,
        courier_code=sample_quote.courier_code,
        tracking_number="DEMO-TRK-INCOMPLETE",
        label_status="UNSUPPORTED",
        label_url_or_data=None,
        is_demo_data=True,
    )

    report = ver_service.verify_shipment(
        order=incomplete_order,
        quote=sample_quote,
        approval=sample_approval,
        shipment_result=shipment_res,
    )

    assert report.label_details is not None
    assert report.label_details.file_generated is False
    assert report.label_details.local_pdf_status == VerificationStatus.NOT_VERIFIED

    crit3 = next(c for c in report.checks if c.criterion_id == "CRIT_3_LABEL_CORRELATION")
    assert crit3.passed is False
    assert crit3.status == VerificationStatus.NOT_VERIFIED
    assert "Missing required shipment data" in crit3.details


def test_local_demo_label_never_passes_official_carrier_checks_for_live_shipment(
    ver_service, sample_order, sample_quote, sample_approval
):
    """
    5. The constraint that locally generated demo labels never pass official carrier-label checks:
    When is_demo_data is False (live shipment), an unsupported carrier label must NEVER
    pass as verified, even though local demo PDF can be generated.
    """
    live_quote = sample_quote.model_copy(update={"is_demo_data": False})
    live_shipment_res = ShipmentResult(
        success=True,
        shipment_id="SHP-LIVE-REAL-001",
        order_reference=sample_order.order_reference,
        courier_code=live_quote.courier_code,
        tracking_number="REAL-CARRIER-AWB-12345",
        label_status="UNSUPPORTED",  # Live carrier doesn't return label
        label_url_or_data=None,
        is_demo_data=False,          # Live shipment!
    )

    report = ver_service.verify_shipment(
        order=sample_order,
        quote=live_quote,
        approval=sample_approval,
        shipment_result=live_shipment_res,
    )

    # Local demo PDF may have been generated for debugging, but must NOT satisfy official carrier verification
    crit3 = next(c for c in report.checks if c.criterion_id == "CRIT_3_LABEL_CORRELATION")
    assert crit3.passed is False
    assert crit3.status == VerificationStatus.UNSUPPORTED
    assert "cannot substitute for official carrier label" in crit3.details
    assert report.overall_status != VerificationStatus.VERIFIED
