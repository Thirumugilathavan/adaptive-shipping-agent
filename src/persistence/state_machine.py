"""
Shipment Lifecycle Finite State Machine (FSM).
Enforces valid state progressions, guards against unauthorized dispatch,
and handles timeouts and uncertain outcomes safely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from src.domain.models import ShipmentStatus


class InvalidStateTransitionError(Exception):
    """Raised when an illegal shipment lifecycle transition is attempted."""
    pass


class HumanApprovalRequiredError(Exception):
    """Raised when shipment creation is attempted without explicit human approval."""
    pass


class ApprovalRecord(BaseModel):
    """Immutable proof of explicit human approval."""
    approved_by: str = Field(..., min_length=2, description="Name or identifier of human operator")
    approved_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of approval"
    )
    quote_id: str = Field(..., description="Selected quote ID being approved")
    total_cost: float = Field(..., ge=0.0, description="Agreed landed cost at time of approval")
    notes: Optional[str] = Field(default=None, description="Optional operator notes")


# Directed Graph of allowable transitions
VALID_TRANSITIONS: dict[ShipmentStatus, set[ShipmentStatus]] = {
    ShipmentStatus.DRAFT: {ShipmentStatus.QUOTED, ShipmentStatus.CANCELLED},
    ShipmentStatus.QUOTED: {ShipmentStatus.PENDING_APPROVAL, ShipmentStatus.CANCELLED},
    ShipmentStatus.PENDING_APPROVAL: {
        ShipmentStatus.DISPATCHING,
        ShipmentStatus.QUOTED,      # Allow re-quoting if prices drifted or user changed inputs
        ShipmentStatus.CANCELLED,
    },
    ShipmentStatus.DISPATCHING: {
        ShipmentStatus.CREATED,
        ShipmentStatus.CREATION_UNCERTAIN,  # On network timeout or 5xx
        ShipmentStatus.FAILED,              # On explicit 4xx rejection
    },
    ShipmentStatus.CREATION_UNCERTAIN: {
        ShipmentStatus.CREATED,             # Carrier confirmed shipment exists post-reconciliation
        ShipmentStatus.FAILED,              # Carrier confirmed no record exists post-reconciliation
        ShipmentStatus.PENDING_APPROVAL,    # Safe reset after verified failure so user can re-try
    },
    ShipmentStatus.CREATED: {ShipmentStatus.CANCELLED},
    ShipmentStatus.CANCELLED: set(),         # Terminal state
    ShipmentStatus.FAILED: set(),            # Terminal state
}


class ShipmentStateMachine:
    """Controls and validates lifecycle transitions for a shipment."""

    @staticmethod
    def validate_transition(
        current_status: ShipmentStatus,
        target_status: ShipmentStatus,
        approval: Optional[ApprovalRecord] = None,
    ) -> None:
        """
        Validate whether transitioning from current_status to target_status is permitted.
        Raises InvalidStateTransitionError or HumanApprovalRequiredError if illegal.
        """
        allowed_targets = VALID_TRANSITIONS.get(current_status, set())
        if target_status not in allowed_targets:
            raise InvalidStateTransitionError(
                f"Illegal state transition from '{current_status.value}' to '{target_status.value}'. "
                f"Allowed transitions from '{current_status.value}' are: "
                f"{[s.value for s in allowed_targets] or 'None (terminal state)'}."
            )

        # Guard: Cannot begin dispatch without explicit human approval
        if target_status == ShipmentStatus.DISPATCHING:
            if not approval or not approval.approved_by.strip():
                raise HumanApprovalRequiredError(
                    "Shipment dispatch blocked: Explicit human approval with operator identifier is required."
                )

    @staticmethod
    def can_transition(current_status: ShipmentStatus, target_status: ShipmentStatus) -> bool:
        """Check if a transition is reachable from current status."""
        return target_status in VALID_TRANSITIONS.get(current_status, set())
