"""
Courier Ranking and Multi-Criteria Recommendation Engine.
Provides deterministic ranking under Lowest Cost, Fastest Delivery, and Balanced modes,
enforces operational constraints, and provides comprehensive decision explanations.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from src.domain.models import CourierQuote, PackageType


class RankingMode(str, Enum):
    """Supported courier selection strategies."""
    LOWEST_COST = "LOWEST_COST"
    FASTEST_DELIVERY = "FASTEST_DELIVERY"
    BALANCED = "BALANCED"


class RankingPreferences(BaseModel):
    """User preferences and operational constraints for courier ranking."""
    mode: RankingMode = Field(default=RankingMode.LOWEST_COST, description="Primary ranking strategy")
    cost_weight: float = Field(default=0.6, ge=0.0, le=1.0, description="Weight for cost in balanced mode")
    speed_weight: float = Field(default=0.4, ge=0.0, le=1.0, description="Weight for speed in balanced mode")
    max_budget: Optional[float] = Field(default=None, gt=0.0, description="Maximum allowable landed shipping cost in INR")
    max_delivery_days: Optional[int] = Field(default=None, ge=1, description="Strict deadline: maximum transit days allowed")
    allowed_package_types: Optional[list[PackageType]] = Field(default=None, description="Permitted packaging formats")
    require_cod: Optional[bool] = Field(default=None, description="Whether COD serviceability is mandatory")


class RankedCourierQuote(BaseModel):
    """An eligible courier quote scored and ranked."""
    rank: int
    quote: CourierQuote
    total_cost: float
    estimated_delivery_days: int
    chargeable_weight_kg: float
    balanced_score: float = Field(default=0.0, description="Composite score (0-100, 100 is best)")
    normalized_cost_penalty: float = Field(default=0.0, description="Normalized cost penalty (0 is cheapest)")
    normalized_speed_penalty: float = Field(default=0.0, description="Normalized transit penalty (0 is fastest)")
    source_type: str
    is_demo_data: bool
    selection_rationale: str


class IneligibleCourierQuote(BaseModel):
    """A courier quote excluded from ranking with explicit justification."""
    quote_id: str
    courier_code: str
    courier_name: str
    service_type: str
    rejection_reasons: list[str]
    is_serviceable: bool
    source_type: str
    is_demo_data: bool


class RankingResult(BaseModel):
    """Complete output of the ranking engine, including winners and excluded options."""
    mode: RankingMode
    preferences: RankingPreferences
    selected_winner: Optional[RankedCourierQuote] = None
    ranked_candidates: list[RankedCourierQuote] = Field(default_factory=list)
    ineligible_candidates: list[IneligibleCourierQuote] = Field(default_factory=list)
    decision_explanation: str
    cost_margin_over_runner_up: Optional[float] = None
    days_margin_over_runner_up: Optional[int] = None
    total_eligible: int = 0
    total_ineligible: int = 0
    has_demo_data: bool = True
    disclaimer: str = (
        "RANKING DISCLOSURE: Evaluated based on provided constraints and active provider quotes. "
        "Demo quotes are simulated and cannot be used for commercial fulfillment."
    )


def filter_quote_eligibility(
    quote: CourierQuote,
    preferences: RankingPreferences,
) -> tuple[bool, list[str]]:
    """
    Evaluate whether a quote satisfies all operational and user constraints.
    Returns (is_eligible, list_of_rejection_reasons).
    Never silently treats missing costs or SLA days as zero.
    """
    reasons: list[str] = []

    # 1. Base Serviceability Check
    if not quote.is_serviceable:
        reasons.append(quote.unserviceable_reason or "Courier reported route is not serviceable.")

    # 2. Landed Cost Check - Missing cost cannot be treated as zero
    if quote.cost_breakdown is None:
        reasons.append("Missing cost breakdown. Unpriced quotes cannot be recommended.")
    else:
        # Budget constraint
        if preferences.max_budget is not None and quote.cost_breakdown.total_cost > preferences.max_budget:
            reasons.append(
                f"Total landed cost (₹{quote.cost_breakdown.total_cost:.2f}) exceeds maximum budget (₹{preferences.max_budget:.2f})."
            )

    # 3. Delivery SLA Check
    if quote.estimated_delivery_days is None:
        if preferences.mode == RankingMode.FASTEST_DELIVERY:
            reasons.append("Missing estimated delivery days. Cannot evaluate for fastest delivery ranking.")
        elif preferences.max_delivery_days is not None:
            reasons.append("Missing delivery days estimate when a strict delivery deadline constraint is active.")
    else:
        # Delivery deadline constraint
        if preferences.max_delivery_days is not None and quote.estimated_delivery_days > preferences.max_delivery_days:
            reasons.append(
                f"Estimated transit time ({quote.estimated_delivery_days} days) exceeds deadline constraint ({preferences.max_delivery_days} days)."
            )

    is_eligible = len(reasons) == 0
    return is_eligible, reasons


def rank_couriers(
    quotes: list[CourierQuote],
    preferences: Optional[RankingPreferences] = None,
) -> RankingResult:
    """
    Rank eligible couriers according to the specified mode and constraints.
    Enforces deterministic tie-breaking and generates comprehensive explanations.
    """
    prefs = preferences or RankingPreferences()
    eligible_raw: list[CourierQuote] = []
    ineligible_list: list[IneligibleCourierQuote] = []

    # Filter quotes
    for q in quotes:
        eligible, reasons = filter_quote_eligibility(q, prefs)
        if eligible:
            eligible_raw.append(q)
        else:
            ineligible_list.append(
                IneligibleCourierQuote(
                    quote_id=q.quote_id,
                    courier_code=q.courier_code,
                    courier_name=q.courier_name,
                    service_type=q.service_type,
                    rejection_reasons=reasons,
                    is_serviceable=q.is_serviceable,
                    source_type=q.source_type,
                    is_demo_data=q.is_demo_data,
                )
            )

    if not eligible_raw:
        mode_label = prefs.mode.value.replace("_", " ").title()
        explanation = (
            f"No couriers are eligible under the requested {mode_label} strategy and constraints. "
            f"{len(ineligible_list)} couriers were evaluated and excluded due to serviceability or constraint violations."
        )
        return RankingResult(
            mode=prefs.mode,
            preferences=prefs,
            selected_winner=None,
            ranked_candidates=[],
            ineligible_candidates=ineligible_list,
            decision_explanation=explanation,
            cost_margin_over_runner_up=None,
            days_margin_over_runner_up=None,
            total_eligible=0,
            total_ineligible=len(ineligible_list),
            has_demo_data=any(q.is_demo_data for q in quotes),
        )

    # Calculate min and max for normalization in Balanced mode
    costs = [q.cost_breakdown.total_cost for q in eligible_raw if q.cost_breakdown]  # type: ignore
    min_cost = min(costs) if costs else 0.0
    max_cost = max(costs) if costs else 0.0
    cost_span = max(max_cost - min_cost, 0.001)

    days_list = [q.estimated_delivery_days for q in eligible_raw if q.estimated_delivery_days is not None]
    min_days = min(days_list) if days_list else 1
    max_days = max(days_list) if days_list else 1
    days_span = max(float(max_days - min_days), 0.001)

    # Normalize weights
    total_weight = (prefs.cost_weight + prefs.speed_weight) or 1.0
    w_cost = prefs.cost_weight / total_weight
    w_speed = prefs.speed_weight / total_weight

    scored_candidates: list[dict[str, Any]] = []
    for q in eligible_raw:
        cost = q.cost_breakdown.total_cost  # type: ignore
        days = q.estimated_delivery_days or max_days
        chg_wt = q.weight_result.chargeable_weight_kg if q.weight_result else 0.0

        # Normalization (0.0 is best, 1.0 is worst)
        norm_cost = (cost - min_cost) / cost_span
        norm_speed = (days - min_days) / days_span

        # Balanced penalty: 0.0 is perfect, 1.0 is worst.
        balanced_penalty = (w_cost * norm_cost) + (w_speed * norm_speed)
        balanced_score = round(100.0 * (1.0 - balanced_penalty), 2)

        scored_candidates.append(
            {
                "quote": q,
                "total_cost": cost,
                "days": days,
                "chg_wt": chg_wt,
                "norm_cost": round(norm_cost, 4),
                "norm_speed": round(norm_speed, 4),
                "balanced_score": balanced_score,
                "courier_code": q.courier_code,
            }
        )

    # Sort deterministically based on active mode
    if prefs.mode == RankingMode.LOWEST_COST:
        # Primary: Lowest cost. Tie-breaker: faster SLA, then courier_code
        scored_candidates.sort(key=lambda x: (x["total_cost"], x["days"], x["courier_code"]))
    elif prefs.mode == RankingMode.FASTEST_DELIVERY:
        # Primary: Fastest delivery. Tie-breaker: lower cost, then courier_code
        scored_candidates.sort(key=lambda x: (x["days"], x["total_cost"], x["courier_code"]))
    elif prefs.mode == RankingMode.BALANCED:
        # Primary: Highest balanced score (descending). Tie-breaker: lower cost, faster days, courier_code
        scored_candidates.sort(key=lambda x: (-x["balanced_score"], x["total_cost"], x["days"], x["courier_code"]))

    ranked_quotes: list[RankedCourierQuote] = []
    for idx, item in enumerate(scored_candidates, start=1):
        q = item["quote"]
        if idx == 1:
            if prefs.mode == RankingMode.LOWEST_COST:
                rationale = f"Ranked #1: Lowest landed shipping cost (₹{item['total_cost']:.2f})."
            elif prefs.mode == RankingMode.FASTEST_DELIVERY:
                rationale = f"Ranked #1: Fastest verified transit SLA ({item['days']} days transit)."
            else:
                rationale = (
                    f"Ranked #1: Optimal balanced trade-off (Score: {item['balanced_score']}/100, "
                    f"Cost: ₹{item['total_cost']:.2f}, SLA: {item['days']} days)."
                )
        else:
            rationale = f"Ranked #{idx} alternative option."

        ranked_quotes.append(
            RankedCourierQuote(
                rank=idx,
                quote=q,
                total_cost=item["total_cost"],
                estimated_delivery_days=item["days"],
                chargeable_weight_kg=item["chg_wt"],
                balanced_score=item["balanced_score"],
                normalized_cost_penalty=item["norm_cost"],
                normalized_speed_penalty=item["norm_speed"],
                source_type=q.source_type,
                is_demo_data=q.is_demo_data,
                selection_rationale=rationale,
            )
        )

    winner = ranked_quotes[0]
    runner_up = ranked_quotes[1] if len(ranked_quotes) > 1 else None

    cost_margin = round(runner_up.total_cost - winner.total_cost, 2) if runner_up else None
    days_margin = (runner_up.estimated_delivery_days - winner.estimated_delivery_days) if runner_up else None

    # Construct human-readable decision explanation
    if runner_up:
        if prefs.mode == RankingMode.LOWEST_COST:
            diff_text = f"saves ₹{cost_margin:.2f} compared to runner-up '{runner_up.quote.courier_name}'"
        elif prefs.mode == RankingMode.FASTEST_DELIVERY:
            diff_text = f"delivers {abs(days_margin or 0)} days faster than runner-up '{runner_up.quote.courier_name}'"
        else:
            diff_text = f"scored {winner.balanced_score} vs {runner_up.balanced_score} for runner-up '{runner_up.quote.courier_name}'"
        explanation = (
            f"Recommended '{winner.quote.courier_name}' under {prefs.mode.value} strategy. "
            f"It {diff_text} for a chargeable weight of {winner.chargeable_weight_kg:.1f} kg. "
            f"Total landed cost is ₹{winner.total_cost:.2f} with an estimated transit time of {winner.estimated_delivery_days} days."
        )
    else:
        explanation = (
            f"Recommended '{winner.quote.courier_name}' as the sole eligible courier satisfying all operational constraints. "
            f"Landed cost: ₹{winner.total_cost:.2f}, SLA: {winner.estimated_delivery_days} days."
        )

    return RankingResult(
        mode=prefs.mode,
        preferences=prefs,
        selected_winner=winner,
        ranked_candidates=ranked_quotes,
        ineligible_candidates=ineligible_list,
        decision_explanation=explanation,
        cost_margin_over_runner_up=cost_margin,
        days_margin_over_runner_up=days_margin,
        total_eligible=len(ranked_quotes),
        total_ineligible=len(ineligible_list),
        has_demo_data=any(q.is_demo_data for q in quotes),
    )
