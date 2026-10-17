"""
Unit tests for Quote Freshness Detection and Timeout Reconciliation Workflow (Phase 5).
Covers:
- Fresh vs expired quote checks.
- Price and serviceability drift detection.
- Invalidation of approval on quote change.
- Timeout before creation vs timeout after creation.
- Reconciling using provider reference lookup.
- Handling unconfirmed/unsupported lookup and manual operator resolution.
- Re-dispatch prevention and duplicate locking.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import pytest
import httpx

from src.domain.freshness import (
    QuoteFreshnessStatus,
    check_quote_freshness,
    compare_quote_drift,
    verify_and_refresh_quote,
)
from src.domain.models import (
    Address,
    CostBreakdown,
    CourierQuote,
    Dimensions,
    OrderInput,
    PackageType,
    ShipmentStatus,
    WeightResult,
)
from src.domain.reconciliation import (
    ApprovalInvalidatedError,
    ReconciliationResult,
    ReconciliationStatus,
    ShipmentDispatchService,
    StaleQuoteError,
)
from src.persistence.audit import AuditEventType, AuditLogger
from src.persistence.database import DatabaseManager
from src.persistence.idempotency import IdempotencyManager, ShipmentAlreadyCreatedError
from src.persistence.state_machine import ApprovalRecord, ShipmentStateMachine
from src.providers.base import CourierProvider, ShipmentResult, TrackingResult
from src.providers.demo_adapter import DemoShippingAdapter


@pytest.fixture
def test_db():
    return DatabaseManager(db_path=":memory:")


def make_test_order(ref: str = "ORD-TEST-001") -> OrderInput:
    return OrderInput(
        order_reference=ref,
        sender=Address(
            name="Origin Hub",
            phone="9876543210",
            street_line1="123 Warehouse St",
            city="New Delhi",
            state="Delhi",
            pin_code="110020",
        ),
        recipient=Address(
            name="Dest Buyer",
            phone="9123456780",
            street_line1="456 Tech Park",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560034",
        ),
        parcel_weight_kg=2.0,
        dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        package_type=PackageType.BOX,
    )


def test_quote_freshness_fresh_vs_expired():
    """Verify freshness evaluation with configurable max_age_seconds."""
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
    recent_time = (now - timedelta(seconds=300)).isoformat()  # 5 mins old
    old_time = (now - timedelta(seconds=1200)).isoformat()    # 20 mins old

    quote_fresh = CourierQuote(
        quote_id="Q-FRESH",
        courier_code="COURIER_1",
        courier_name="Courier 1",
        service_type="Air",
        estimated_delivery_days=2,
        retrieval_timestamp=recent_time,
        input_hash="hash",
    )

    quote_expired = CourierQuote(
        quote_id="Q-EXPIRED",
        courier_code="COURIER_1",
        courier_name="Courier 1",
        service_type="Air",
        estimated_delivery_days=2,
        retrieval_timestamp=old_time,
        input_hash="hash",
    )

    # Max age 900s (15 min)
    check_1 = check_quote_freshness(quote_fresh, max_age_seconds=900, current_time=now)
    assert check_1.is_fresh is True
    assert check_1.status == QuoteFreshnessStatus.FRESH

    check_2 = check_quote_freshness(quote_expired, max_age_seconds=900, current_time=now)
    assert check_2.is_fresh is False
    assert check_2.status == QuoteFreshnessStatus.EXPIRED


def test_quote_drift_detection_price_change():
    """Verify material change detection when freight or landed cost changes."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    orig_quote = adapter.get_rates(order)[0]

    # Create a simulated refreshed quote with higher cost
    altered_cost = orig_quote.cost_breakdown.model_copy(
        update={"total_cost": orig_quote.cost_breakdown.total_cost + 30.0}
    )
    refreshed_quote = orig_quote.model_copy(update={"cost_breakdown": altered_cost})

    drift = compare_quote_drift(orig_quote, refreshed_quote)
    assert drift.is_price_changed is True
    assert drift.is_material_change is True
    assert drift.requires_renewed_approval is True
    assert "increased by ₹30.00" in drift.explanation


def test_quote_drift_detection_serviceability_loss():
    """Verify material change detection when a courier becomes newly unserviceable."""
    order = make_test_order()
    adapter = DemoShippingAdapter()
    orig_quote = adapter.get_rates(order)[0]

    unserviceable_quote = orig_quote.model_copy(
        update={
            "is_serviceable": False,
            "is_eligible": False,
            "cost_breakdown": None,
            "unserviceable_reason": "Carrier network operational blackout",
        }
    )

    drift = compare_quote_drift(orig_quote, unserviceable_quote)
    assert drift.is_serviceability_changed is True
    assert drift.is_material_change is True
    assert drift.requires_renewed_approval is True


def test_dispatch_blocks_approval_on_price_drift(test_db):
    """
    If price drifted before dispatch, attempt_dispatch must raise ApprovalInvalidatedError
    and record an audit event.
    """
    order = make_test_order("ORD-DRIFT-001")
    # Insert order in DB for foreign key constraint
    test_db.insert_order({
        "order_id": "ORD-001",
        "order_reference": order.order_reference,
        "sender_json": "{}",
        "recipient_json": "{}",
        "parcel_weight_kg": 2.0,
        "length_cm": 20.0,
        "width_cm": 20.0,
        "height_cm": 20.0,
        "package_type": "box",
        "is_cod": 0,
        "cod_amount": 0.0,
        "created_at": "2026-10-17T12:00:00Z",
    })

    audit = AuditLogger(test_db)
    idem = IdempotencyManager(test_db)
    service = ShipmentDispatchService(test_db, audit, idem)

    adapter = DemoShippingAdapter()
    orig_quote = adapter.get_rates(order)[0]

    # Insert quote in DB
    test_db.insert_quotes([{
        "quote_id": orig_quote.quote_id,
        "order_reference": order.order_reference,
        "courier_code": orig_quote.courier_code,
        "courier_name": orig_quote.courier_name,
        "service_type": orig_quote.service_type,
        "estimated_delivery_days": 2,
        "total_cost": orig_quote.cost_breakdown.total_cost,
        "quote_data_json": "{}",
        "source_type": "DEMO_ADAPTER",
        "input_hash": "hash",
        "created_at": "2026-10-17T12:00:00Z",
    }])

    # Mock provider that returns increased price
    class DriftingProvider(DemoShippingAdapter):
        def get_rates(self, o):
            rates = super().get_rates(o)
            altered = rates[0].cost_breakdown.model_copy(
                update={"total_cost": rates[0].cost_breakdown.total_cost + 45.0}
            )
            rates[0] = rates[0].model_copy(update={"cost_breakdown": altered})
            return rates

    drifting_provider = DriftingProvider()
    approval = ApprovalRecord(
        approved_by="Jane Operator",
        quote_id=orig_quote.quote_id,
        total_cost=orig_quote.cost_breakdown.total_cost,
    )

    with pytest.raises(ApprovalInvalidatedError, match="Quote terms changed since approval"):
        service.attempt_dispatch(order, orig_quote, approval, drifting_provider)

    # Confirm audit trail logged approval invalidation
    trail = audit.get_trail(order.order_reference)
    event_types = [t["event_type"] for t in trail]
    assert AuditEventType.APPROVAL_INVALIDATED.value in event_types


# Custom Mock Providers for Deterministic Timeout Testing
class TimeoutBeforeCreationProvider(DemoShippingAdapter):
    """Simulates network timeout before the carrier creates the shipment."""
    def create_shipment(self, order, quote, approval, idempotency_key):
        raise httpx.TimeoutException("Connection timed out before request reached carrier")

    def lookup_shipment_by_reference(self, order_reference):
        # Carrier confirms no shipment exists
        return None


class TimeoutAfterCreationProvider(DemoShippingAdapter):
    """Simulates carrier successfully creating shipment, but response dropped due to read timeout."""
    def create_shipment(self, order, quote, approval, idempotency_key):
        # Carrier creates it internally first
        res = ShipmentResult(
            shipment_id=f"REAL-CARRIER-SHP-{order.order_reference}",
            order_reference=order.order_reference,
            courier_code=quote.courier_code,
            tracking_number="AWB-CARRIER-RECONCILED-999",
            label_status="UNSUPPORTED",
            is_demo_data=True,
        )
        self._created_shipments[order.order_reference] = res
        # Then client disconnects / read timeout happens
        raise httpx.ReadTimeout("Read timed out while waiting for carrier response")


class UnsupportedLookupTimeoutProvider(DemoShippingAdapter):
    """Simulates carrier that times out and does NOT support reference-based lookup."""
    def create_shipment(self, order, quote, approval, idempotency_key):
        raise httpx.ConnectTimeout("Connection dropped")

    def lookup_shipment_by_reference(self, order_reference):
        raise NotImplementedError("Carrier API does not support reference-based lookup")


def setup_order_in_db(test_db, order_ref: str, quote_id: str, cost: float):
    test_db.insert_order({
        "order_id": f"ID-{order_ref}",
        "order_reference": order_ref,
        "sender_json": "{}",
        "recipient_json": "{}",
        "parcel_weight_kg": 2.0,
        "length_cm": 20.0,
        "width_cm": 20.0,
        "height_cm": 20.0,
        "package_type": "box",
        "is_cod": 0,
        "cod_amount": 0.0,
        "created_at": "2026-10-17T12:00:00Z",
    })
    test_db.insert_quotes([{
        "quote_id": quote_id,
        "order_reference": order_ref,
        "courier_code": "DEMO_AIR_EXPRESS",
        "courier_name": "Demo Air Express",
        "service_type": "Air",
        "estimated_delivery_days": 2,
        "total_cost": cost,
        "quote_data_json": "{}",
        "source_type": "DEMO_ADAPTER",
        "input_hash": "hash",
        "created_at": "2026-10-17T12:00:00Z",
    }])


def test_timeout_before_creation_and_reconciliation(test_db):
    """
    Scenario: Network timeout happens BEFORE carrier creates booking.
    1. Dispatch transitions to CREATION_UNCERTAIN.
    2. Retry is blocked by idempotency manager.
    3. Reconcile queries carrier -> carrier confirms no booking was made.
    4. Shipment is safely reset to PENDING_APPROVAL.
    """
    order = make_test_order("ORD-TIMEOUT-BEFORE-001")
    adapter = DemoShippingAdapter()
    quote = adapter.get_rates(order)[0]
    setup_order_in_db(test_db, order.order_reference, quote.quote_id, quote.cost_breakdown.total_cost)

    audit = AuditLogger(test_db)
    idem = IdempotencyManager(test_db)
    service = ShipmentDispatchService(test_db, audit, idem)

    approval = ApprovalRecord(
        approved_by="Operator A",
        quote_id=quote.quote_id,
        total_cost=quote.cost_breakdown.total_cost,
    )

    provider = TimeoutBeforeCreationProvider()

    # Dispatch times out
    res = service.attempt_dispatch(order, quote, approval, provider)
    assert res.status == ShipmentStatus.CREATION_UNCERTAIN
    assert res.is_uncertain is True
    assert res.requires_reconciliation is True

    # Immediate retry is blocked
    with pytest.raises(Exception):
        idem.check_can_dispatch(order.order_reference, "ANY-KEY")

    # Run reconciliation
    recon = service.reconcile_uncertain_shipment(order.order_reference, provider)
    assert recon.status == ReconciliationStatus.RECONCILED_NOT_CREATED
    assert recon.final_shipment_status == ShipmentStatus.PENDING_APPROVAL
    assert recon.requires_manual_resolution is False


def test_timeout_after_creation_and_reconciliation(test_db):
    """
    Scenario: Carrier successfully creates booking, but response times out on client.
    1. Dispatch transitions to CREATION_UNCERTAIN.
    2. Reconcile queries carrier -> carrier returns existing booking.
    3. Shipment transitions to CREATED with verified tracking reference.
    4. No duplicate shipment was created!
    """
    order = make_test_order("ORD-TIMEOUT-AFTER-001")
    adapter = DemoShippingAdapter()
    quote = adapter.get_rates(order)[0]
    setup_order_in_db(test_db, order.order_reference, quote.quote_id, quote.cost_breakdown.total_cost)

    audit = AuditLogger(test_db)
    idem = IdempotencyManager(test_db)
    service = ShipmentDispatchService(test_db, audit, idem)

    approval = ApprovalRecord(
        approved_by="Operator B",
        quote_id=quote.quote_id,
        total_cost=quote.cost_breakdown.total_cost,
    )

    provider = TimeoutAfterCreationProvider()

    # Dispatch times out
    res = service.attempt_dispatch(order, quote, approval, provider)
    assert res.status == ShipmentStatus.CREATION_UNCERTAIN

    # Reconcile establishes existing booking
    recon = service.reconcile_uncertain_shipment(order.order_reference, provider)
    assert recon.status == ReconciliationStatus.RECONCILED_CREATED
    assert recon.final_shipment_status == ShipmentStatus.CREATED
    assert recon.tracking_number == "AWB-CARRIER-RECONCILED-999"

    # Subsequent retry is blocked with ShipmentAlreadyCreatedError
    with pytest.raises(ShipmentAlreadyCreatedError):
        idem.check_can_dispatch(order.order_reference, "KEY")


def test_unsupported_lookup_requires_manual_resolution(test_db):
    """
    Scenario: Carrier times out and does NOT support reference lookup.
    1. Automated reconciliation reports RECONCILIATION_UNSUPPORTED.
    2. Shipment remains CREATION_UNCERTAIN.
    3. Operator manually resolves after contacting carrier support.
    """
    order = make_test_order("ORD-MANUAL-001")
    adapter = DemoShippingAdapter()
    quote = adapter.get_rates(order)[0]
    setup_order_in_db(test_db, order.order_reference, quote.quote_id, quote.cost_breakdown.total_cost)

    audit = AuditLogger(test_db)
    idem = IdempotencyManager(test_db)
    service = ShipmentDispatchService(test_db, audit, idem)

    approval = ApprovalRecord(
        approved_by="Operator C",
        quote_id=quote.quote_id,
        total_cost=quote.cost_breakdown.total_cost,
    )

    provider = UnsupportedLookupTimeoutProvider()

    # Dispatch times out
    res = service.attempt_dispatch(order, quote, approval, provider)
    assert res.status == ShipmentStatus.CREATION_UNCERTAIN

    # Automated reconciliation cannot establish outcome
    recon = service.reconcile_uncertain_shipment(order.order_reference, provider)
    assert recon.status == ReconciliationStatus.RECONCILIATION_UNSUPPORTED
    assert recon.final_shipment_status == ShipmentStatus.CREATION_UNCERTAIN
    assert recon.requires_manual_resolution is True

    # Operator manually confirms booking with verified AWB from phone/email
    manual_recon = service.resolve_uncertain_shipment_manually(
        order_reference=order.order_reference,
        resolution="CONFIRMED_BOOKED",
        actor="Lead Dispatcher (Priya)",
        tracking_number="VERIFIED-PHONE-AWB-888",
        notes="Confirmed with carrier support ticket #4419",
    )
    assert manual_recon.final_shipment_status == ShipmentStatus.CREATED
    assert manual_recon.tracking_number == "VERIFIED-PHONE-AWB-888"

    # Confirm audit trail captured manual resolution
    trail = audit.get_trail(order.order_reference)
    event_types = [t["event_type"] for t in trail]
    assert AuditEventType.MANUAL_RESOLUTION_APPLIED.value in event_types
