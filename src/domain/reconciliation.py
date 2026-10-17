"""
Timeout Reconciliation and Safe Shipment Dispatch Workflow Engine.
Coordinates quote freshness checks, idempotency locking, network drop recovery,
and carrier lookup reconciliation without fabricating tracking references or repeating dispatches.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
import httpx

from src.domain.freshness import (
    QuoteFreshnessStatus,
    check_quote_freshness,
    verify_and_refresh_quote,
)
from src.domain.models import CourierQuote, OrderInput, ShipmentStatus
from src.persistence.audit import AuditEventType, AuditLogger
from src.persistence.database import DatabaseManager
from src.persistence.idempotency import (
    IdempotencyManager,
    ReconciliationRequiredError,
    ShipmentAlreadyCreatedError,
    generate_idempotency_key,
)
from src.persistence.state_machine import (
    ApprovalRecord,
    HumanApprovalRequiredError,
    ShipmentStateMachine,
)
from src.providers.base import CourierProvider, ShipmentResult


class StaleQuoteError(Exception):
    """Raised when an attempt is made to dispatch with an expired quote."""
    pass


class ApprovalInvalidatedError(Exception):
    """Raised when quote terms drifted, invalidating the previous human approval."""
    pass


class ReconciliationStatus(str, Enum):
    """Outcomes of a post-timeout reconciliation lookup."""
    RECONCILED_CREATED = "RECONCILED_CREATED"
    RECONCILED_NOT_CREATED = "RECONCILED_NOT_CREATED"
    RECONCILIATION_UNSUPPORTED = "RECONCILIATION_UNSUPPORTED"
    RECONCILIATION_AMBIGUOUS = "RECONCILIATION_AMBIGUOUS"


class ReconciliationResult(BaseModel):
    """Audit outcome of reconciling an uncertain shipment."""
    order_reference: str
    shipment_id: str
    status: ReconciliationStatus
    final_shipment_status: ShipmentStatus
    tracking_number: Optional[str] = None
    requires_manual_resolution: bool = False
    explanation: str


class DispatchAttemptResult(BaseModel):
    """Result of an automated shipment dispatch operation."""
    order_reference: str
    shipment_id: str
    status: ShipmentStatus
    tracking_number: Optional[str] = None
    is_uncertain: bool = False
    requires_reconciliation: bool = False
    error_message: Optional[str] = None
    explanation: str


class ShipmentDispatchService:
    """
    Coordinates safe, idempotent, and freshness-checked shipment creation.
    Guarantees no blind retries on timeouts and prevents unauthorized dispatches.
    """

    def __init__(
        self,
        db: DatabaseManager,
        audit: AuditLogger,
        idempotency_mgr: IdempotencyManager,
        fsm: Optional[ShipmentStateMachine] = None,
    ):
        self.db = db
        self.audit = audit
        self.idem = idempotency_mgr
        self.fsm = fsm or ShipmentStateMachine()

    def attempt_dispatch(
        self,
        order: OrderInput,
        quote: CourierQuote,
        approval: ApprovalRecord,
        provider: CourierProvider,
        max_quote_age_seconds: int = 900,
        current_time: Optional[datetime] = None,
    ) -> DispatchAttemptResult:
        """
        Execute dispatch workflow with freshness verification, idempotency locks,
        and safe timeout capture.
        """
        # 1. Freshness & Drift Verification Gate
        refresh_res = verify_and_refresh_quote(
            quote=quote,
            order=order,
            provider=provider,
            max_age_seconds=max_quote_age_seconds,
            current_time=current_time,
        )

        if refresh_res.is_approval_invalidated:
            self.audit.record_event(
                event_type=AuditEventType.APPROVAL_INVALIDATED,
                actor="FreshnessEngine",
                details={
                    "quote_id": quote.quote_id,
                    "reason": refresh_res.change_detail.explanation if refresh_res.change_detail else "Quote expired",
                    "action": refresh_res.action_required,
                },
                order_reference=order.order_reference,
            )
            if refresh_res.change_detail and refresh_res.change_detail.is_price_changed:
                self.audit.record_event(
                    event_type=AuditEventType.QUOTE_PRICE_CHANGED,
                    actor="FreshnessEngine",
                    details={
                        "old_cost": refresh_res.change_detail.old_cost,
                        "new_cost": refresh_res.change_detail.new_cost,
                        "diff": refresh_res.change_detail.cost_difference,
                    },
                    order_reference=order.order_reference,
                )
            raise ApprovalInvalidatedError(
                f"Quote terms changed since approval ({refresh_res.action_required}): "
                f"{refresh_res.change_detail.explanation if refresh_res.change_detail else 'Quote expired'}. "
                f"Renewed human approval is required before booking."
            )

        if not refresh_res.freshness_check.is_fresh and refresh_res.refreshed_quote is None:
            raise StaleQuoteError(
                f"Selected quote expired ({refresh_res.freshness_check.age_seconds:.0f}s old) "
                f"and could not be refreshed. Obtain renewed quotes and approval."
            )

        # 2. Approval Matching Check
        if approval.quote_id != quote.quote_id:
            raise ApprovalInvalidatedError("Approval record quote_id does not match the active selected quote.")

        active_cost = quote.cost_breakdown.total_cost if quote.cost_breakdown else 0.0
        if abs(approval.total_cost - active_cost) > 0.01:
            raise ApprovalInvalidatedError(
                f"Approval record cost (₹{approval.total_cost:.2f}) does not match quote landed cost (₹{active_cost:.2f})."
            )

        # 3. Idempotency Check & In-Flight Lock
        idempotency_key = generate_idempotency_key(order.order_reference, quote.quote_id)
        self.idem.check_can_dispatch(order.order_reference, idempotency_key)

        shipment_id = f"SHP-{order.order_reference}-{quote.courier_code}"
        now_iso = (current_time or datetime.now(timezone.utc)).isoformat()

        # FSM validation and state transition to DISPATCHING
        self.fsm.validate_transition(ShipmentStatus.PENDING_APPROVAL, ShipmentStatus.DISPATCHING, approval=approval)
        self.idem.record_dispatch_start(
            shipment_id=shipment_id,
            order_reference=order.order_reference,
            quote_id=quote.quote_id,
            courier_code=quote.courier_code,
            idempotency_key=idempotency_key,
            approved_by=approval.approved_by,
            approved_at=approval.approved_at,
            created_at=now_iso,
        )
        self.audit.record_event(
            event_type=AuditEventType.DISPATCH_ATTEMPTED,
            actor=approval.approved_by,
            details={"shipment_id": shipment_id, "idempotency_key": idempotency_key[:16]},
            order_reference=order.order_reference,
            shipment_id=shipment_id,
        )

        # 4. Invoke Provider with Safe Timeout Capture
        try:
            shipment_result = provider.create_shipment(order, quote, approval, idempotency_key)

            # Success: transition to CREATED
            self.idem.update_dispatch_outcome(
                shipment_id=shipment_id,
                status=ShipmentStatus.CREATED,
                tracking_number=shipment_result.tracking_number,
                label_status=shipment_result.label_status,
                label_data=shipment_result.label_url_or_data,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            self.fsm.validate_transition(ShipmentStatus.DISPATCHING, ShipmentStatus.CREATED)
            self.audit.record_event(
                event_type=AuditEventType.SHIPMENT_CREATED,
                actor=provider.provider_code,
                details={"tracking_number": shipment_result.tracking_number, "label_status": shipment_result.label_status},
                order_reference=order.order_reference,
                shipment_id=shipment_id,
            )

            return DispatchAttemptResult(
                order_reference=order.order_reference,
                shipment_id=shipment_id,
                status=ShipmentStatus.CREATED,
                tracking_number=shipment_result.tracking_number,
                is_uncertain=False,
                requires_reconciliation=False,
                explanation=f"Shipment successfully created with tracking reference {shipment_result.tracking_number}.",
            )

        except (httpx.TimeoutException, TimeoutError) as timeout_exc:
            # Network drop / timeout: transition to CREATION_UNCERTAIN
            now_err = datetime.now(timezone.utc).isoformat()
            self.idem.update_dispatch_outcome(
                shipment_id=shipment_id,
                status=ShipmentStatus.CREATION_UNCERTAIN,
                updated_at=now_err,
            )
            self.fsm.validate_transition(ShipmentStatus.DISPATCHING, ShipmentStatus.CREATION_UNCERTAIN)
            self.audit.record_event(
                event_type=AuditEventType.DISPATCH_UNCERTAIN,
                actor="SystemDispatcher",
                details={
                    "error": str(timeout_exc),
                    "error_type": type(timeout_exc).__name__,
                    "reason": "Network timeout or socket disconnect during carrier dispatch",
                },
                order_reference=order.order_reference,
                shipment_id=shipment_id,
            )

            return DispatchAttemptResult(
                order_reference=order.order_reference,
                shipment_id=shipment_id,
                status=ShipmentStatus.CREATION_UNCERTAIN,
                tracking_number=None,
                is_uncertain=True,
                requires_reconciliation=True,
                error_message=str(timeout_exc),
                explanation=(
                    "Network timeout occurred during shipment creation. Outcome is uncertain. "
                    "The system has locked retries to prevent duplicate bookings. Reconciliation required."
                ),
            )

        except Exception as general_exc:
            # Fatal client or validation error: mark FAILED
            now_err = datetime.now(timezone.utc).isoformat()
            self.idem.update_dispatch_outcome(
                shipment_id=shipment_id,
                status=ShipmentStatus.FAILED,
                updated_at=now_err,
            )
            self.fsm.validate_transition(ShipmentStatus.DISPATCHING, ShipmentStatus.FAILED)
            self.audit.record_event(
                event_type=AuditEventType.DISPATCH_FAILED,
                actor="SystemDispatcher",
                details={"error": str(general_exc), "error_type": type(general_exc).__name__},
                order_reference=order.order_reference,
                shipment_id=shipment_id,
            )
            raise

    def reconcile_uncertain_shipment(
        self,
        order_reference: str,
        provider: CourierProvider,
    ) -> ReconciliationResult:
        """
        Reconcile an uncertain shipment outcome using documented provider lookup.
        Guarantees no fabricated data and enforces valid state transitions.
        """
        sql = "SELECT shipment_id, status FROM shipments WHERE order_reference = ?"
        with self.db.connection() as conn:
            row = conn.execute(sql, (order_reference,)).fetchone()
            if not row:
                raise ValueError(f"No shipment record found for order '{order_reference}'.")

            shipment_id = row["shipment_id"]
            status = row["status"]

        if status != ShipmentStatus.CREATION_UNCERTAIN.value:
            raise ValueError(f"Shipment for order '{order_reference}' is not in uncertain state (current: '{status}').")

        # Attempt provider reference lookup
        try:
            existing_shipment = provider.lookup_shipment_by_reference(order_reference)
        except Exception as exc:
            # Provider does not support lookup or lookup failed (e.g. unconfirmed ShipSandbox)
            self.audit.record_event(
                event_type=AuditEventType.DISPATCH_UNCERTAIN,
                actor=provider.provider_code,
                details={
                    "lookup_error": str(exc),
                    "note": "Provider does not support automated reference lookup. Manual operator resolution required.",
                },
                order_reference=order_reference,
                shipment_id=shipment_id,
            )
            return ReconciliationResult(
                order_reference=order_reference,
                shipment_id=shipment_id,
                status=ReconciliationStatus.RECONCILIATION_UNSUPPORTED,
                final_shipment_status=ShipmentStatus.CREATION_UNCERTAIN,
                tracking_number=None,
                requires_manual_resolution=True,
                explanation=(
                    f"Provider '{provider.provider_name}' does not support reference-based lookup ({type(exc).__name__}). "
                    f"Shipment remains CREATION_UNCERTAIN to prevent duplicate bookings. Manual resolution required."
                ),
            )

        if existing_shipment is not None:
            # Case A: Carrier created it before timeout occurred
            now_iso = datetime.now(timezone.utc).isoformat()
            self.idem.update_dispatch_outcome(
                shipment_id=shipment_id,
                status=ShipmentStatus.CREATED,
                tracking_number=existing_shipment.tracking_number,
                label_status=existing_shipment.label_status,
                label_data=existing_shipment.label_url_or_data,
                updated_at=now_iso,
            )
            self.fsm.validate_transition(ShipmentStatus.CREATION_UNCERTAIN, ShipmentStatus.CREATED)
            self.audit.record_event(
                event_type=AuditEventType.SHIPMENT_RECONCILED,
                actor="ReconciliationService",
                details={
                    "resolution": "FOUND_EXISTING_BOOKING",
                    "tracking_number": existing_shipment.tracking_number,
                },
                order_reference=order_reference,
                shipment_id=shipment_id,
            )

            return ReconciliationResult(
                order_reference=order_reference,
                shipment_id=shipment_id,
                status=ReconciliationStatus.RECONCILED_CREATED,
                final_shipment_status=ShipmentStatus.CREATED,
                tracking_number=existing_shipment.tracking_number,
                requires_manual_resolution=False,
                explanation=f"Reconciliation established existing booking. Tracking number: {existing_shipment.tracking_number}.",
            )
        else:
            # Case B: Provider confirmed no booking exists (timeout occurred before creation)
            # Safe reset to PENDING_APPROVAL so operator can re-dispatch without creating duplicate!
            now_iso = datetime.now(timezone.utc).isoformat()
            self.idem.update_dispatch_outcome(
                shipment_id=shipment_id,
                status=ShipmentStatus.PENDING_APPROVAL,
                updated_at=now_iso,
            )
            self.fsm.validate_transition(ShipmentStatus.CREATION_UNCERTAIN, ShipmentStatus.PENDING_APPROVAL)
            self.audit.record_event(
                event_type=AuditEventType.SHIPMENT_RECONCILED,
                actor="ReconciliationService",
                details={"resolution": "CONFIRMED_NOT_CREATED", "new_status": "PENDING_APPROVAL"},
                order_reference=order_reference,
                shipment_id=shipment_id,
            )

            return ReconciliationResult(
                order_reference=order_reference,
                shipment_id=shipment_id,
                status=ReconciliationStatus.RECONCILED_NOT_CREATED,
                final_shipment_status=ShipmentStatus.PENDING_APPROVAL,
                tracking_number=None,
                requires_manual_resolution=False,
                explanation="Carrier confirmed no shipment was booked. Safe to retry after human review.",
            )

    def resolve_uncertain_shipment_manually(
        self,
        order_reference: str,
        resolution: str,  # "CONFIRMED_BOOKED" or "CONFIRMED_NOT_BOOKED"
        actor: str,
        tracking_number: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> ReconciliationResult:
        """
        Operator manual resolution when automated carrier lookup is unsupported.
        Never fabricates tracking numbers; operator must supply verified tracking reference if confirming booking.
        """
        sql = "SELECT shipment_id, status FROM shipments WHERE order_reference = ?"
        with self.db.connection() as conn:
            row = conn.execute(sql, (order_reference,)).fetchone()
            if not row:
                raise ValueError(f"No shipment found for order '{order_reference}'.")
            shipment_id = row["shipment_id"]
            current_status = row["status"]

        if current_status != ShipmentStatus.CREATION_UNCERTAIN.value:
            raise ValueError(f"Manual resolution only valid for CREATION_UNCERTAIN shipments (current: '{current_status}').")

        if resolution == "CONFIRMED_BOOKED":
            if not tracking_number or not tracking_number.strip():
                raise ValueError("Manual confirmation of booking requires a verified carrier tracking number.")

            now_iso = datetime.now(timezone.utc).isoformat()
            self.idem.update_dispatch_outcome(
                shipment_id=shipment_id,
                status=ShipmentStatus.CREATED,
                tracking_number=tracking_number.strip(),
                updated_at=now_iso,
            )
            self.fsm.validate_transition(ShipmentStatus.CREATION_UNCERTAIN, ShipmentStatus.CREATED)
            self.audit.record_event(
                event_type=AuditEventType.MANUAL_RESOLUTION_APPLIED,
                actor=actor,
                details={
                    "resolution": "CONFIRMED_BOOKED",
                    "tracking_number": tracking_number.strip(),
                    "notes": notes or "Operator verified booking with carrier",
                },
                order_reference=order_reference,
                shipment_id=shipment_id,
            )

            return ReconciliationResult(
                order_reference=order_reference,
                shipment_id=shipment_id,
                status=ReconciliationStatus.RECONCILED_CREATED,
                final_shipment_status=ShipmentStatus.CREATED,
                tracking_number=tracking_number.strip(),
                requires_manual_resolution=False,
                explanation=f"Manual resolution: Operator '{actor}' verified carrier booking with tracking '{tracking_number}'.",
            )
        elif resolution == "CONFIRMED_NOT_BOOKED":
            now_iso = datetime.now(timezone.utc).isoformat()
            self.idem.update_dispatch_outcome(
                shipment_id=shipment_id,
                status=ShipmentStatus.PENDING_APPROVAL,
                updated_at=now_iso,
            )
            self.fsm.validate_transition(ShipmentStatus.CREATION_UNCERTAIN, ShipmentStatus.PENDING_APPROVAL)
            self.audit.record_event(
                event_type=AuditEventType.MANUAL_RESOLUTION_APPLIED,
                actor=actor,
                details={
                    "resolution": "CONFIRMED_NOT_BOOKED",
                    "notes": notes or "Operator confirmed with carrier that order was not booked",
                },
                order_reference=order_reference,
                shipment_id=shipment_id,
            )

            return ReconciliationResult(
                order_reference=order_reference,
                shipment_id=shipment_id,
                status=ReconciliationStatus.RECONCILED_NOT_CREATED,
                final_shipment_status=ShipmentStatus.PENDING_APPROVAL,
                tracking_number=None,
                requires_manual_resolution=False,
                explanation=f"Manual resolution: Operator '{actor}' confirmed no booking exists. Order reset to PENDING_APPROVAL.",
            )
        else:
            raise ValueError(f"Invalid resolution mode '{resolution}'. Use 'CONFIRMED_BOOKED' or 'CONFIRMED_NOT_BOOKED'.")
