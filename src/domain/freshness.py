"""
Quote Freshness Detection and Drift Verification Engine.
Monitors quote age, detects price/SLA/serviceability drift, and enforces
renewed human approval when material quote changes occur before booking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from src.domain.models import CourierQuote, OrderInput
from src.providers.base import CourierProvider


class QuoteFreshnessStatus(str, Enum):
    """Lifecycle validity states for a courier quote."""
    FRESH = "FRESH"
    EXPIRED = "EXPIRED"
    UNVERIFIABLE = "UNVERIFIABLE"


class QuoteFreshnessCheck(BaseModel):
    """Audit record of quote freshness evaluation."""
    quote_id: str
    status: QuoteFreshnessStatus
    retrieval_timestamp: str
    checked_at: str
    age_seconds: float
    max_age_seconds: float
    is_fresh: bool
    explanation: str
    has_provenance: bool = True
    provenance_details: dict[str, Any] = Field(default_factory=dict)


class QuoteChangeDetail(BaseModel):
    """Detailed audit of price, SLA, or serviceability drift between original and refreshed quote."""
    old_cost: float
    new_cost: float
    cost_difference: float
    is_price_changed: bool
    old_delivery_days: Optional[int]
    new_delivery_days: Optional[int]
    is_sla_changed: bool
    is_serviceability_changed: bool
    is_material_change: bool
    requires_renewed_approval: bool
    explanation: str


class QuoteRefreshResult(BaseModel):
    """Complete outcome of quote freshness check and drift comparison."""
    original_quote: CourierQuote
    refreshed_quote: Optional[CourierQuote] = None
    freshness_check: QuoteFreshnessCheck
    change_detail: Optional[QuoteChangeDetail] = None
    is_approval_invalidated: bool = False
    action_required: str = Field(
        default="PROCEED",
        description="'PROCEED', 'RENEW_APPROVAL', 'RERANK_REQUIRED', or 'UNVERIFIABLE_WARNING'"
    )


def check_quote_freshness(
    quote: CourierQuote,
    max_age_seconds: int = 900,  # 15 minutes default
    current_time: Optional[datetime] = None,
) -> QuoteFreshnessCheck:
    """
    Evaluate whether a quote is still fresh according to configurable expiry policy.
    Enforces provenance checks (source_id, retrieval_timestamp, retrieval_method).
    Never pretends a quote is fresh if it cannot be established.
    """
    now = current_time or datetime.now(timezone.utc)
    prov_dict = {
        "source_type": getattr(quote, "source_type", None),
        "source_id": getattr(quote, "source_id", None),
        "retrieval_method": getattr(quote, "retrieval_method", None),
        "evidence_url": getattr(quote, "evidence_url", None),
        "field_provenance_count": len(getattr(quote, "field_provenance", {})),
    }

    # Verify provenance presence
    if not getattr(quote, "source_id", None) or not getattr(quote, "source_type", None):
        return QuoteFreshnessCheck(
            quote_id=quote.quote_id,
            status=QuoteFreshnessStatus.UNVERIFIABLE,
            retrieval_timestamp=getattr(quote, "retrieval_timestamp", ""),
            checked_at=now.isoformat(),
            age_seconds=-1.0,
            max_age_seconds=float(max_age_seconds),
            is_fresh=False,
            explanation="Unverifiable quote: Missing source provenance identifier or source type.",
            has_provenance=False,
            provenance_details=prov_dict,
        )

    try:
        # Parse ISO 8601 UTC timestamp
        retrieved_at = datetime.fromisoformat(quote.retrieval_timestamp)
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return QuoteFreshnessCheck(
            quote_id=quote.quote_id,
            status=QuoteFreshnessStatus.UNVERIFIABLE,
            retrieval_timestamp=quote.retrieval_timestamp,
            checked_at=now.isoformat(),
            age_seconds=-1.0,
            max_age_seconds=float(max_age_seconds),
            is_fresh=False,
            explanation="Unverifiable retrieval timestamp format.",
            has_provenance=False,
            provenance_details=prov_dict,
        )

    age_seconds = max(0.0, (now - retrieved_at).total_seconds())
    is_fresh = age_seconds <= max_age_seconds

    if is_fresh:
        status = QuoteFreshnessStatus.FRESH
        explanation = f"Quote is fresh (Age: {age_seconds:.1f}s <= max allowable {max_age_seconds}s)."
    else:
        status = QuoteFreshnessStatus.EXPIRED
        explanation = f"Quote expired (Age: {age_seconds:.1f}s > max allowable {max_age_seconds}s). Re-verification required."

    return QuoteFreshnessCheck(
        quote_id=quote.quote_id,
        status=status,
        retrieval_timestamp=quote.retrieval_timestamp,
        checked_at=now.isoformat(),
        age_seconds=round(age_seconds, 1),
        max_age_seconds=float(max_age_seconds),
        is_fresh=is_fresh,
        explanation=explanation,
        has_provenance=True,
        provenance_details=prov_dict,
    )


def compare_quote_drift(
    original_quote: CourierQuote,
    refreshed_quote: CourierQuote,
    tolerance_inr: float = 0.01,
) -> QuoteChangeDetail:
    """
    Compare original and refreshed quote to detect material changes.
    Material changes include price increases/decreases, transit SLA shifts, or serviceability losses.
    """
    old_cost = original_quote.cost_breakdown.total_cost if original_quote.cost_breakdown else 0.0
    new_cost = refreshed_quote.cost_breakdown.total_cost if refreshed_quote.cost_breakdown else 0.0
    cost_diff = round(new_cost - old_cost, 2)
    is_price_changed = abs(cost_diff) > tolerance_inr

    old_days = original_quote.estimated_delivery_days
    new_days = refreshed_quote.estimated_delivery_days
    is_sla_changed = old_days != new_days

    is_serv_changed = original_quote.is_serviceable != refreshed_quote.is_serviceable or original_quote.is_eligible != refreshed_quote.is_eligible

    is_material = is_price_changed or is_sla_changed or is_serv_changed or not refreshed_quote.is_eligible

    reasons: list[str] = []
    if is_price_changed:
        direction = "increased" if cost_diff > 0 else "decreased"
        reasons.append(f"Price {direction} by ₹{abs(cost_diff):.2f} (from ₹{old_cost:.2f} to ₹{new_cost:.2f})")
    if is_sla_changed:
        reasons.append(f"Delivery SLA changed from {old_days}d to {new_days}d")
    if is_serv_changed:
        reasons.append(f"Serviceability status changed: {refreshed_quote.unserviceable_reason or 'Newly unserviceable'}")

    explanation = "; ".join(reasons) if reasons else "Quote terms remain identical."

    return QuoteChangeDetail(
        old_cost=old_cost,
        new_cost=new_cost,
        cost_difference=cost_diff,
        is_price_changed=is_price_changed,
        old_delivery_days=old_days,
        new_delivery_days=new_days,
        is_sla_changed=is_sla_changed,
        is_serviceability_changed=is_serv_changed,
        is_material_change=is_material,
        requires_renewed_approval=is_material,
        explanation=explanation,
    )


def verify_and_refresh_quote(
    quote: CourierQuote,
    order: OrderInput,
    provider: CourierProvider,
    max_age_seconds: int = 900,
    tolerance_inr: float = 0.01,
    current_time: Optional[datetime] = None,
) -> QuoteRefreshResult:
    """
    Primary freshness gate:
    1. Evaluates age against max_age_seconds.
    2. Queries provider for fresh quotes if supported.
    3. Detects price/SLA drift and flags if renewed approval is required.
    """
    freshness = check_quote_freshness(quote, max_age_seconds=max_age_seconds, current_time=current_time)

    # Attempt to query fresh quote from provider
    try:
        fresh_quotes = provider.get_rates(order)
        # Match by courier_code
        matching = [q for q in fresh_quotes if q.courier_code == quote.courier_code]
        if not matching:
            # Courier no longer returned by provider
            return QuoteRefreshResult(
                original_quote=quote,
                refreshed_quote=None,
                freshness_check=freshness,
                change_detail=None,
                is_approval_invalidated=True,
                action_required="RERANK_REQUIRED",
            )
        new_quote = matching[0]
    except Exception as exc:
        # Refresh endpoint unavailable or provider error (e.g. unconfirmed manual rate)
        return QuoteRefreshResult(
            original_quote=quote,
            refreshed_quote=None,
            freshness_check=freshness,
            change_detail=None,
            is_approval_invalidated=not freshness.is_fresh,
            action_required="UNVERIFIABLE_WARNING" if freshness.is_fresh else "RENEW_APPROVAL",
        )

    change_detail = compare_quote_drift(quote, new_quote, tolerance_inr=tolerance_inr)

    if change_detail.is_material_change:
        action = "RENEW_APPROVAL"
        approval_invalidated = True
    elif not freshness.is_fresh:
        action = "RENEW_APPROVAL"
        approval_invalidated = True
    else:
        action = "PROCEED"
        approval_invalidated = False

    return QuoteRefreshResult(
        original_quote=quote,
        refreshed_quote=new_quote,
        freshness_check=freshness,
        change_detail=change_detail,
        is_approval_invalidated=approval_invalidated,
        action_required=action,
    )
