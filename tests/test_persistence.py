"""
Unit tests for Persistence, SQLite Database, FSM, Idempotency, and Audit layer.
"""

import pytest
from src.domain.models import ShipmentStatus
from src.persistence.audit import AuditEventType, AuditLogger, sanitize_secrets
from src.persistence.database import DatabaseManager
from src.persistence.idempotency import (
    DuplicateDispatchError,
    IdempotencyManager,
    ReconciliationRequiredError,
    ShipmentAlreadyCreatedError,
    generate_idempotency_key,
)
from src.persistence.state_machine import (
    ApprovalRecord,
    HumanApprovalRequiredError,
    InvalidStateTransitionError,
    ShipmentStateMachine,
)


@pytest.fixture
def test_db():
    """In-memory SQLite database for isolated fast testing."""
    return DatabaseManager(db_path=":memory:")


def test_order_and_quote_persistence(test_db):
    """Test inserting and retrieving orders and quotes in SQLite."""
    order_data = {
        "order_id": "ORD-001",
        "order_reference": "REF-1001",
        "sender_json": "{}",
        "recipient_json": "{}",
        "parcel_weight_kg": 1.5,
        "length_cm": 20.0,
        "width_cm": 15.0,
        "height_cm": 10.0,
        "package_type": "box",
        "is_cod": 0,
        "cod_amount": 0.0,
        "created_at": "2026-10-17T12:00:00Z",
    }
    test_db.insert_order(order_data)
    retrieved = test_db.get_order("REF-1001")
    assert retrieved is not None
    assert retrieved["order_reference"] == "REF-1001"
    assert retrieved["parcel_weight_kg"] == 1.5

    quotes = [
        {
            "quote_id": "Q-01",
            "order_reference": "REF-1001",
            "courier_code": "EXP_AIR",
            "courier_name": "Express Air",
            "service_type": "Air",
            "estimated_delivery_days": 2,
            "total_cost": 150.0,
            "quote_data_json": "{}",
            "source_type": "DEMO_ADAPTER",
            "input_hash": "hash123",
            "created_at": "2026-10-17T12:01:00Z",
        }
    ]
    test_db.insert_quotes(quotes)
    retrieved_quotes = test_db.get_quotes_for_order("REF-1001")
    assert len(retrieved_quotes) == 1
    assert retrieved_quotes[0]["quote_id"] == "Q-01"


def test_state_machine_valid_transitions():
    """Verify standard legal path through the lifecycle."""
    fsm = ShipmentStateMachine()
    approval = ApprovalRecord(
        approved_by="Jane Operator",
        quote_id="Q-01",
        total_cost=150.0,
    )

    # Valid progression
    fsm.validate_transition(ShipmentStatus.DRAFT, ShipmentStatus.QUOTED)
    fsm.validate_transition(ShipmentStatus.QUOTED, ShipmentStatus.PENDING_APPROVAL)
    fsm.validate_transition(ShipmentStatus.PENDING_APPROVAL, ShipmentStatus.DISPATCHING, approval=approval)
    fsm.validate_transition(ShipmentStatus.DISPATCHING, ShipmentStatus.CREATED)


def test_state_machine_blocks_unauthorized_dispatch():
    """Attempting dispatch without approval must raise HumanApprovalRequiredError."""
    fsm = ShipmentStateMachine()
    with pytest.raises(HumanApprovalRequiredError):
        fsm.validate_transition(ShipmentStatus.PENDING_APPROVAL, ShipmentStatus.DISPATCHING, approval=None)


def test_state_machine_blocks_illegal_jump():
    """Direct jump from DRAFT to CREATED must be blocked."""
    fsm = ShipmentStateMachine()
    with pytest.raises(InvalidStateTransitionError):
        fsm.validate_transition(ShipmentStatus.DRAFT, ShipmentStatus.CREATED)


def _insert_test_order_and_quote(test_db, order_ref: str, quote_id: str):
    test_db.insert_order({
        "order_id": f"ORD-{order_ref}",
        "order_reference": order_ref,
        "sender_json": "{}",
        "recipient_json": "{}",
        "parcel_weight_kg": 1.0,
        "length_cm": 10.0,
        "width_cm": 10.0,
        "height_cm": 10.0,
        "package_type": "box",
        "is_cod": 0,
        "cod_amount": 0.0,
        "created_at": "2026-10-17T12:00:00Z",
    })
    test_db.insert_quotes([{
        "quote_id": quote_id,
        "order_reference": order_ref,
        "courier_code": "EXP_AIR",
        "courier_name": "Express Air",
        "service_type": "Air",
        "estimated_delivery_days": 2,
        "total_cost": 100.0,
        "quote_data_json": "{}",
        "source_type": "DEMO_ADAPTER",
        "input_hash": "hash",
        "created_at": "2026-10-17T12:00:00Z",
    }])


def test_idempotency_duplicate_prevention(test_db):
    """Test that concurrent or repeat dispatches are safely locked out."""
    order_ref = "REF-1002"
    quote_id = "Q-02"
    _insert_test_order_and_quote(test_db, order_ref, quote_id)

    idem_mgr = IdempotencyManager(test_db)
    key = generate_idempotency_key(order_ref, quote_id)

    # First dispatch starts
    idem_mgr.record_dispatch_start(
        shipment_id="SHP-001",
        order_reference=order_ref,
        quote_id=quote_id,
        courier_code="EXP_AIR",
        idempotency_key=key,
        approved_by="Admin",
        approved_at="2026-10-17T12:00:00Z",
        created_at="2026-10-17T12:00:00Z",
    )

    # Second dispatch while in-flight raises DuplicateDispatchError
    with pytest.raises(DuplicateDispatchError):
        idem_mgr.check_can_dispatch(order_ref, key)

    # Complete dispatch to CREATED
    idem_mgr.update_dispatch_outcome(
        shipment_id="SHP-001",
        status=ShipmentStatus.CREATED,
        tracking_number="TRACK-DEMO-999",
    )

    # Subsequent retry raises ShipmentAlreadyCreatedError
    with pytest.raises(ShipmentAlreadyCreatedError):
        idem_mgr.check_can_dispatch(order_ref, key)


def test_timeout_reconciliation_guard(test_db):
    """If dispatch resulted in uncertain status, retry is blocked until reconciled."""
    order_ref = "REF-1003"
    quote_id = "Q-03"
    _insert_test_order_and_quote(test_db, order_ref, quote_id)

    idem_mgr = IdempotencyManager(test_db)
    key = generate_idempotency_key(order_ref, quote_id)

    idem_mgr.record_dispatch_start(
        shipment_id="SHP-002",
        order_reference=order_ref,
        quote_id=quote_id,
        courier_code="EXP_AIR",
        idempotency_key=key,
        approved_by="Admin",
        approved_at="2026-10-17T12:00:00Z",
        created_at="2026-10-17T12:00:00Z",
    )

    # Simulate network timeout
    idem_mgr.update_dispatch_outcome(
        shipment_id="SHP-002",
        status=ShipmentStatus.CREATION_UNCERTAIN,
    )

    with pytest.raises(ReconciliationRequiredError):
        idem_mgr.check_can_dispatch(order_ref, key)


def test_secret_sanitization():
    """Verify secret scrubber recursively redacts API keys and tokens."""
    dirty_data = {
        "order_id": "ORD-123",
        "api_key": "secret_live_key_99999",
        "auth_headers": {
            "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI...",
            "user_token": "token_abc123",
        },
        "safe_field": "Delhi to Mumbai",
        "raw_log": "Calling endpoint with Bearer secret_live_token_7777 now",
    }

    clean_data = sanitize_secrets(dirty_data)
    assert clean_data["api_key"] == "[REDACTED]"
    assert clean_data["auth_headers"]["Authorization"] == "[REDACTED]"
    assert clean_data["auth_headers"]["user_token"] == "[REDACTED]"
    assert clean_data["safe_field"] == "Delhi to Mumbai"
    assert "[REDACTED]" in clean_data["raw_log"]
    assert "secret_live_token" not in clean_data["raw_log"]
