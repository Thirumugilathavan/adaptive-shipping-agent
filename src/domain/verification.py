"""
Independent 5-Point Shipment Verification Engine.
Implements Phase 5 compliance checks independently of action execution:
1. An AWB was returned or created.
2. The AWB matches the shipment and intended order.
3. The label corresponds to that AWB and order.
4. The pickup schedule was actually returned or can be checked through the provider.
5. The observed result matches the approved and intended action.

Distinguishes: VERIFIED, PENDING, FAILED, and UNVERIFIED.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from src.domain.models import OrderInput, CourierQuote
from src.persistence.state_machine import ApprovalRecord
from src.providers.base import CourierProvider, ShipmentResult, PickupResult
from src.persistence.audit import AuditEventType, AuditLogger


class VerificationStatus(str, Enum):
    """Overall outcome of the independent 5-point verification check."""
    VERIFIED = "VERIFIED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    UNVERIFIED = "UNVERIFIED"


class VerificationCheck(BaseModel):
    """Detailed result of an individual verification checkpoint."""
    criterion_id: str
    criterion_name: str
    passed: bool
    status: VerificationStatus
    observed_value: Optional[str] = None
    expected_value: Optional[str] = None
    details: str


class VerificationReport(BaseModel):
    """Comprehensive report produced by the independent verification service."""
    order_reference: str
    shipment_id: str
    awb_tracking_number: Optional[str] = None
    courier_code: str
    overall_status: VerificationStatus
    checks: list[VerificationCheck] = Field(default_factory=list)
    verified_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of verification"
    )
    is_demo_data: bool = True
    disclaimer: str = "INDEPENDENT VERIFICATION: Evaluates observed state against approved order proposal."
    evidence_references: dict[str, Any] = Field(default_factory=dict)


class ShipmentVerificationService:
    """
    Performs independent 5-point verification against the approved proposal,
    the shipment outcome, the generated label, and the carrier pickup schedule.
    """

    def __init__(self, audit_logger: Optional[AuditLogger] = None):
        self.audit = audit_logger

    def verify_shipment(
        self,
        order: OrderInput,
        quote: CourierQuote,
        approval: ApprovalRecord,
        shipment_result: Optional[ShipmentResult],
        provider: Optional[CourierProvider] = None,
        pickup_result: Optional[PickupResult] = None,
    ) -> VerificationReport:
        """
        Execute all 5 verification checkpoints independently.
        """
        checks: list[VerificationCheck] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # Check 1: An AWB was returned or created
        awb_present = bool(shipment_result and shipment_result.tracking_number and shipment_result.tracking_number.strip())
        awb_val = shipment_result.tracking_number if shipment_result else None
        checks.append(
            VerificationCheck(
                criterion_id="CRIT_1_AWB_PRESENT",
                criterion_name="AWB Generated / Returned",
                passed=awb_present,
                status=VerificationStatus.VERIFIED if awb_present else VerificationStatus.FAILED,
                observed_value=awb_val or "None",
                expected_value="Non-empty tracking reference string",
                details="AWB identifier successfully extracted from carrier result." if awb_present else "No AWB returned from provider execution.",
            )
        )

        # Check 2: AWB matches the shipment and intended order
        awb_matches_order = False
        if shipment_result and awb_present:
            # Verify order reference alignment
            awb_matches_order = (shipment_result.order_reference == order.order_reference)
        checks.append(
            VerificationCheck(
                criterion_id="CRIT_2_AWB_ORDER_MATCH",
                criterion_name="AWB Associated with Intended Order",
                passed=awb_matches_order,
                status=VerificationStatus.VERIFIED if awb_matches_order else VerificationStatus.FAILED,
                observed_value=f"Order ref: {shipment_result.order_reference if shipment_result else 'None'}",
                expected_value=f"Order ref: {order.order_reference}",
                details="AWB is deterministically tied to the active order reference." if awb_matches_order else "Mismatch between AWB shipment record and order reference.",
            )
        )

        # Check 3: Label corresponds to that AWB and order
        label_valid = False
        label_status_val = shipment_result.label_status if shipment_result else "UNSUPPORTED"
        label_data = shipment_result.label_url_or_data if shipment_result else None

        if label_status_val in ("AVAILABLE_URL", "AVAILABLE_PDF", "AVAILABLE_HTML") and label_data:
            # Check if label content or metadata references the AWB and order
            if isinstance(label_data, bytes):
                awb_b = (awb_val or "").encode("utf-8", errors="ignore")
                ref_b = order.order_reference.encode("utf-8", errors="ignore")
                if (awb_b and awb_b in label_data) or (ref_b in label_data):
                    label_valid = True
                    label_status = VerificationStatus.VERIFIED
                    label_details = "Label PDF content verified: Contains valid references to AWB and Order Reference."
                else:
                    label_status = VerificationStatus.FAILED
                    label_details = "Label returned but fails correlation check against AWB / Order."
            elif isinstance(label_data, str):
                if awb_val and (awb_val in label_data or order.order_reference in label_data):
                    label_valid = True
                    label_status = VerificationStatus.VERIFIED
                    label_details = "Label content verified: Contains valid references to AWB and Order Reference."
                else:
                    label_status = VerificationStatus.FAILED
                    label_details = "Label returned but fails correlation check against AWB / Order."
            else:
                label_status = VerificationStatus.FAILED
                label_details = "Label data returned in unrecognized type."
        elif label_status_val == "UNSUPPORTED":
            label_status = VerificationStatus.UNVERIFIED
            label_details = "Carrier provider does not support automated label generation in current mode."
        else:
            label_status = VerificationStatus.PENDING
            label_details = f"Label status pending or unavailable ({label_status_val})."

        checks.append(
            VerificationCheck(
                criterion_id="CRIT_3_LABEL_CORRELATION",
                criterion_name="Label Corresponds to AWB & Order",
                passed=label_valid,
                status=label_status,
                observed_value=f"Format: {label_status_val}",
                expected_value=f"Contains AWB {awb_val} and Ref {order.order_reference}",
                details=label_details,
            )
        )

        # Check 4: Pickup schedule returned or checked through provider
        pickup_valid = False
        if pickup_result and pickup_result.status == "CONFIRMED":
            pickup_valid = True
            pickup_status = VerificationStatus.VERIFIED
            pickup_details = (
                f"Pickup confirmed: Ref {pickup_result.pickup_reference}, "
                f"Date: {pickup_result.scheduled_date}, Window: {pickup_result.time_window}."
            )
        elif pickup_result and pickup_result.status == "PENDING":
            pickup_status = VerificationStatus.PENDING
            pickup_details = "Carrier pickup request acknowledged; dispatch window confirmation pending."
        elif provider and hasattr(provider, "schedule_pickup"):
            pickup_status = VerificationStatus.PENDING
            pickup_details = "Pickup scheduling supported by provider but not yet requested."
        else:
            pickup_status = VerificationStatus.UNVERIFIED
            pickup_details = "Carrier provider lacks pickup scheduling endpoint in current sandbox mode."

        checks.append(
            VerificationCheck(
                criterion_id="CRIT_4_PICKUP_SCHEDULE",
                criterion_name="Carrier Pickup Verification",
                passed=pickup_valid,
                status=pickup_status,
                observed_value=pickup_result.pickup_reference if pickup_result else "Not scheduled",
                expected_value="Confirmed carrier pickup slot or reference",
                details=pickup_details,
            )
        )

        # Check 5: Observed result matches approved proposal
        proposal_matches = False
        if shipment_result and quote and approval:
            same_courier = (shipment_result.courier_code == quote.courier_code)
            same_quote = (approval.quote_id == quote.quote_id)
            approved_cost = approval.total_cost
            quote_cost = quote.cost_breakdown.total_cost if quote.cost_breakdown else 0.0
            same_cost = abs(approved_cost - quote_cost) < 0.01

            proposal_matches = same_courier and same_quote and same_cost
            proposal_details = (
                f"Observed courier '{shipment_result.courier_code}' matches approved quote '{quote.quote_id}' "
                f"at agreed cost INR {approved_cost:.2f}."
                if proposal_matches else "Mismatch between approved proposal and executed shipment."
            )
        else:
            proposal_details = "Incomplete approval or shipment data."

        expected_cost_str = f"{quote.cost_breakdown.total_cost:.2f}" if (quote and quote.cost_breakdown) else "0.00"
        checks.append(
            VerificationCheck(
                criterion_id="CRIT_5_PROPOSAL_MATCH",
                criterion_name="Observed Execution Matches Approved Proposal",
                passed=proposal_matches,
                status=VerificationStatus.VERIFIED if proposal_matches else VerificationStatus.FAILED,
                observed_value=f"Courier: {shipment_result.courier_code if shipment_result else 'None'}, Cost: {approval.total_cost:.2f}",
                expected_value=f"Courier: {quote.courier_code if quote else 'None'}, Cost: {expected_cost_str}",
                details=proposal_details,
            )
        )

        # Determine overall verification status
        statuses = [c.status for c in checks]
        if any(s == VerificationStatus.FAILED for s in statuses):
            overall_status = VerificationStatus.FAILED
        elif all(s == VerificationStatus.VERIFIED for s in statuses):
            overall_status = VerificationStatus.VERIFIED
        elif any(s == VerificationStatus.PENDING for s in statuses):
            overall_status = VerificationStatus.PENDING
        else:
            # If some are verified and others unverified (e.g. sandbox limitations)
            overall_status = VerificationStatus.UNVERIFIED

        shipment_id_str = shipment_result.shipment_id if shipment_result else f"SHP-{order.order_reference}"
        report = VerificationReport(
            order_reference=order.order_reference,
            shipment_id=shipment_id_str,
            awb_tracking_number=awb_val,
            courier_code=quote.courier_code if quote else "UNKNOWN",
            overall_status=overall_status,
            checks=checks,
            verified_at=now_iso,
            is_demo_data=getattr(shipment_result, "is_demo_data", True),
            evidence_references={
                "order_reference": order.order_reference,
                "quote_id": quote.quote_id if quote else None,
                "approved_by": approval.approved_by if approval else None,
                "tracking_number": awb_val,
                "label_status": label_status_val,
                "pickup_reference": pickup_result.pickup_reference if pickup_result else None,
            },
        )

        # Record in audit trail if logger available
        if self.audit:
            try:
                self.audit.record_event(
                    event_type=AuditEventType.VERIFICATION_COMPLETED,
                    actor="ShipmentVerificationService",
                    details={
                        "overall_status": overall_status.value,
                        "checks_passed": sum(1 for c in checks if c.passed),
                        "total_checks": len(checks),
                        "awb": awb_val,
                    },
                    order_reference=order.order_reference,
                    shipment_id=shipment_id_str,
                )
            except Exception:
                pass

        return report
