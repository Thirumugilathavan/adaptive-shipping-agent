"""
Personalized Shipping Advisor & Closest-Match Fallback Engine.
Evaluates courier quotes against user budget and delivery deadline constraints,
provides transparent trade-off analysis, deterministic closest-match ranking,
and plain-language recommendation rationales.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.domain.models import CourierQuote


class DeliveryDeadlineOption(str, Enum):
    WITHIN_1_DAY = "Within 1 day"
    WITHIN_2_DAYS = "Within 2 days"
    WITHIN_3_DAYS = "Within 3 days"
    FLEXIBLE = "Flexible delivery time"

    @classmethod
    def to_days(cls, option: str) -> Optional[int]:
        if "1 day" in option:
            return 1
        if "2 day" in option:
            return 2
        if "3 day" in option:
            return 3
        return None

    @classmethod
    def from_days(cls, days: Optional[int]) -> str:
        if days == 1:
            return cls.WITHIN_1_DAY.value
        if days == 2:
            return cls.WITHIN_2_DAYS.value
        if days == 3:
            return cls.WITHIN_3_DAYS.value
        return cls.FLEXIBLE.value


class UserShippingPreferences(BaseModel):
    """User-specified budget and delivery time constraints."""
    max_budget: Optional[float] = Field(default=None, description="Max preferred shipping cost in INR or None if flexible")
    max_delivery_days: Optional[int] = Field(default=None, ge=1, description="Max transit days allowed or None if flexible")
    is_flexible_budget: bool = Field(default=True, description="Whether user opted for flexible budget")
    is_flexible_deadline: bool = Field(default=True, description="Whether user opted for flexible delivery time")

    @classmethod
    def create(
        cls,
        max_budget: Optional[float] = None,
        is_flexible_budget: bool = True,
        deadline_choice: str = "Flexible delivery time",
    ) -> UserShippingPreferences:
        """Helper to construct preferences from UI input values."""
        days = DeliveryDeadlineOption.to_days(deadline_choice)
        eff_budget = None if is_flexible_budget or max_budget is None or max_budget <= 0 else float(max_budget)
        return cls(
            max_budget=eff_budget,
            max_delivery_days=days,
            is_flexible_budget=is_flexible_budget or (eff_budget is None),
            is_flexible_deadline=(days is None),
        )


class CourierComplianceCategory(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    BEST_COMPROMISE = "BEST_COMPROMISE"
    LOWEST_COST = "LOWEST_COST"
    FASTEST_DELIVERY = "FASTEST_DELIVERY"
    OTHER_ALTERNATIVE = "OTHER_ALTERNATIVE"
    UNSERVICEABLE = "UNSERVICEABLE"


class CourierPreferenceCompliance(BaseModel):
    """Detailed compliance and trade-off profile for a single courier quote."""
    quote: CourierQuote
    courier_code: str
    courier_name: str
    service_type: str
    total_cost: float
    currency: str = "INR"
    estimated_delivery_days: Optional[int] = None
    is_serviceable: bool = True

    # Budget evaluation
    meets_budget: bool = True
    budget_overrun: float = 0.0      # > 0 if over budget
    budget_savings: float = 0.0      # > 0 if under budget

    # Deadline evaluation
    meets_deadline: bool = True
    delay_days: int = 0              # > 0 if delivery days > deadline
    has_sla_estimate: bool = True

    # Overall match flags
    meets_both: bool = True
    violation_count: int = 0         # 0 = exact match, 1 = single violation, 2 = violates both

    # Closest match penalty & score (0 to 100, 100 is best)
    composite_penalty: float = 0.0
    closest_match_score: float = 100.0

    # Categorization tags
    tags: list[str] = Field(default_factory=list)

    # Narrative explanations
    main_advantage: str = ""
    main_disadvantage: str = ""
    plain_language_hint: str = ""
    budget_status_text: str = ""
    deadline_status_text: str = ""
    data_verification_text: str = ""

    # Relative diff vs primary recommendation (populated after primary is chosen)
    cost_diff_vs_recommended: Optional[float] = None
    cost_pct_vs_recommended: Optional[float] = None
    days_diff_vs_recommended: Optional[int] = None


class AdvisorRecommendationResult(BaseModel):
    """Output of the Smart Personalized Advisor."""
    preferences: UserShippingPreferences
    has_serviceable_couriers: bool = True
    is_exact_match: bool = True
    primary_recommendation: Optional[CourierPreferenceCompliance] = None

    # Closest match candidate groupings
    best_compromise: Optional[CourierPreferenceCompliance] = None
    lowest_cost_alternative: Optional[CourierPreferenceCompliance] = None
    fastest_alternative: Optional[CourierPreferenceCompliance] = None

    # Group lists
    exact_matches: list[CourierPreferenceCompliance] = Field(default_factory=list)
    budget_ok_late: list[CourierPreferenceCompliance] = Field(default_factory=list)
    fast_over_budget: list[CourierPreferenceCompliance] = Field(default_factory=list)
    violates_both_candidates: list[CourierPreferenceCompliance] = Field(default_factory=list)
    all_evaluated: list[CourierPreferenceCompliance] = Field(default_factory=list)
    unserviceable: list[CourierQuote] = Field(default_factory=list)

    # High-level summary
    headline: str = ""
    summary_explanation: str = ""
    trade_off_explanation: str = ""
    suggested_actions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core Evaluation & Recommendation Engine
# ---------------------------------------------------------------------------

def evaluate_quote_compliance(
    quote: CourierQuote,
    preferences: UserShippingPreferences,
    min_market_cost: float,
    max_market_cost: float,
    min_market_days: int,
    max_market_days: int,
    weight_budget_overrun: float = 0.5,
    weight_delay: float = 0.5,
) -> CourierPreferenceCompliance:
    """
    Evaluate a single quote against user preferences and compute deterministic
    overrun, delay, and closest-match trade-off score.
    """
    total_cost = quote.cost_breakdown.total_cost if quote.cost_breakdown else 0.0
    currency = quote.cost_breakdown.currency if quote.cost_breakdown else "INR"
    sla_days = quote.estimated_delivery_days

    # 1. Budget Evaluation
    if preferences.is_flexible_budget or preferences.max_budget is None:
        meets_budget = True
        budget_overrun = 0.0
        budget_savings = 0.0
        b_text = f"Total cost is {currency} {total_cost:,.2f} (Flexible budget)"
    else:
        if total_cost <= preferences.max_budget:
            meets_budget = True
            budget_overrun = 0.0
            budget_savings = round(preferences.max_budget - total_cost, 2)
            b_text = f"Fits your budget: {currency} {budget_savings:,.2f} below your limit of {currency} {preferences.max_budget:,.2f}."
        else:
            meets_budget = False
            budget_overrun = round(total_cost - preferences.max_budget, 2)
            budget_savings = 0.0
            b_text = f"Exceeds budget: {currency} {budget_overrun:,.2f} above your limit of {currency} {preferences.max_budget:,.2f}."

    # 2. Deadline Evaluation
    if preferences.is_flexible_deadline or preferences.max_delivery_days is None:
        meets_deadline = True
        delay_days = 0
        has_sla = (sla_days is not None)
        d_text = f"Estimated delivery in {sla_days} business days." if sla_days else "Standard delivery time."
    else:
        if sla_days is None:
            meets_deadline = False
            delay_days = 0
            has_sla = False
            d_text = "Delivery estimate unverified — cannot confirm requested deadline."
        elif sla_days <= preferences.max_delivery_days:
            meets_deadline = True
            delay_days = 0
            has_sla = True
            d_text = f"Meets your deadline: estimated delivery in {sla_days} day(s) (target: within {preferences.max_delivery_days} days)."
        else:
            meets_deadline = False
            delay_days = sla_days - preferences.max_delivery_days
            has_sla = True
            d_text = f"Misses deadline: estimated delivery in {sla_days} days ({delay_days} day(s) past your {preferences.max_delivery_days}-day target)."

    # 3. Violation count
    v_count = 0
    if not meets_budget:
        v_count += 1
    if not meets_deadline:
        v_count += 1

    meets_both = (v_count == 0)

    # 4. Deterministic Closest-Match Scoring
    # Normalization scales:
    cost_base = preferences.max_budget if (preferences.max_budget and preferences.max_budget > 0) else max(max_market_cost, 1.0)
    norm_overrun = (budget_overrun / cost_base) if budget_overrun > 0 else 0.0

    days_base = float(preferences.max_delivery_days) if (preferences.max_delivery_days and preferences.max_delivery_days > 0) else float(max_market_days or 1)
    norm_delay = (float(delay_days) / days_base) if delay_days > 0 else (0.5 if not has_sla and not preferences.is_flexible_deadline else 0.0)

    # Base penalty hierarchy:
    # 0 violations: penalty = 0
    # 1 violation: base penalty = 15.0
    # 2 violations: base penalty = 50.0 (strictly ranks 1-violation candidates ahead of 2-violation candidates)
    base_v_penalty = 0.0 if v_count == 0 else (15.0 if v_count == 1 else 50.0)
    variable_penalty = (weight_budget_overrun * norm_overrun * 20.0) + (weight_delay * norm_delay * 20.0)
    composite_penalty = round(base_v_penalty + variable_penalty, 2)
    score = round(max(0.0, 100.0 - composite_penalty), 2)

    # 5. Verification text
    if quote.source_type in ("MANUAL_SANDBOX", "SHIPSANDBOX"):
        ver_text = "Manual Sandbox Entry: Verified rate entered manually via carrier dashboard."
    elif quote.is_demo_data:
        ver_text = "Simulated Demo Data: Synthetic carrier rate produced by Demo Shipping Adapter (not live carrier quote)."
    else:
        ver_text = "Deterministic Pricing Engine: Computed using courier weight-break rules."

    return CourierPreferenceCompliance(
        quote=quote,
        courier_code=quote.courier_code,
        courier_name=quote.courier_name,
        service_type=quote.service_type,
        total_cost=total_cost,
        currency=currency,
        estimated_delivery_days=sla_days,
        is_serviceable=quote.is_serviceable,
        meets_budget=meets_budget,
        budget_overrun=budget_overrun,
        budget_savings=budget_savings,
        meets_deadline=meets_deadline,
        delay_days=delay_days,
        has_sla_estimate=has_sla,
        meets_both=meets_both,
        violation_count=v_count,
        composite_penalty=composite_penalty,
        closest_match_score=score,
        budget_status_text=b_text,
        deadline_status_text=d_text,
        data_verification_text=ver_text,
    )


def rank_compliance_candidates(
    candidates: list[CourierPreferenceCompliance],
    prefer_exact: bool = True,
) -> list[CourierPreferenceCompliance]:
    """
    Deterministically rank compliance candidates.
    Tie-breaking hierarchy:
    1. Violation count (0 < 1 < 2)
    2. Total composite penalty (lower is better) / closest_match_score (higher is better)
    3. Total landed cost (lower is better)
    4. Estimated delivery days (faster is better)
    5. Courier code (alphabetical)
    """
    def _sort_key(c: CourierPreferenceCompliance) -> tuple:
        sla = c.estimated_delivery_days if c.estimated_delivery_days is not None else 999
        return (c.violation_count, c.composite_penalty, c.total_cost, sla, c.courier_code)

    return sorted(candidates, key=_sort_key)


def build_advisor_recommendation(
    quotes: list[CourierQuote],
    preferences: UserShippingPreferences,
    weight_budget_overrun: float = 0.5,
    weight_delay: float = 0.5,
) -> AdvisorRecommendationResult:
    """
    Main entry point: Evaluates all quotes against preferences, determines exact
    vs fallback closest match, generates plain-language comparisons and trade-offs.
    """
    serviceable_quotes = [
        q for q in quotes
        if q.is_serviceable and getattr(q, "is_eligible", True) and q.cost_breakdown is not None
    ]
    unserviceable_quotes = [
        q for q in quotes
        if not q.is_serviceable or not getattr(q, "is_eligible", True) or q.cost_breakdown is None
    ]

    if not serviceable_quotes:
        return AdvisorRecommendationResult(
            preferences=preferences,
            has_serviceable_couriers=False,
            is_exact_match=False,
            primary_recommendation=None,
            best_compromise=None,
            lowest_cost_alternative=None,
            fastest_alternative=None,
            unserviceable=quotes,
            headline="No verified serviceable courier is available for this route or package.",
            summary_explanation="None of the evaluated couriers can service this destination PIN or satisfy the active package/budget constraints. No courier can be recommended.",
            suggested_actions=["Verify pickup and destination PIN codes", "Try reducing parcel dimensions or weight"],
        )

    # Market bounds for normalization
    costs = [q.cost_breakdown.total_cost for q in serviceable_quotes if q.cost_breakdown]
    min_cost = min(costs) if costs else 0.0
    max_cost = max(costs) if costs else 0.0

    days_list = [q.estimated_delivery_days for q in serviceable_quotes if q.estimated_delivery_days is not None]
    min_days = min(days_list) if days_list else 1
    max_days = max(days_list) if days_list else 1

    # Evaluate compliance for each quote
    evaluated: list[CourierPreferenceCompliance] = []
    for q in serviceable_quotes:
        comp = evaluate_quote_compliance(
            quote=q,
            preferences=preferences,
            min_market_cost=min_cost,
            max_market_cost=max_cost,
            min_market_days=min_days,
            max_market_days=max_days,
            weight_budget_overrun=weight_budget_overrun,
            weight_delay=weight_delay,
        )
        evaluated.append(comp)

    # Separate into groups
    exact_matches = [c for c in evaluated if c.meets_both]
    budget_ok_late = [c for c in evaluated if c.meets_budget and not c.meets_deadline]
    fast_over_budget = [c for c in evaluated if c.meets_deadline and not c.meets_budget]
    violates_both = [c for c in evaluated if not c.meets_budget and not c.meets_deadline]

    ranked_all = rank_compliance_candidates(evaluated)

    is_exact = len(exact_matches) > 0
    primary: Optional[CourierPreferenceCompliance] = None

    if is_exact:
        # Prioritize couriers meeting both preferences; recommend the lowest verified total cost
        ranked_exact = sorted(
            exact_matches,
            key=lambda c: (c.total_cost, c.estimated_delivery_days or 999, c.courier_code),
        )
        primary = ranked_exact[0]
        primary.tags.append(CourierComplianceCategory.EXACT_MATCH.value)

        # Build narrative
        curr = primary.currency
        if preferences.is_flexible_budget and preferences.is_flexible_deadline:
            headline = f"Best verified option: {primary.courier_name}"
            summary = f"Selected {primary.courier_name} at the lowest verified landed price of {curr} {primary.total_cost:,.2f}."
        elif not preferences.is_flexible_budget and preferences.is_flexible_deadline:
            headline = f"Fits your budget: {primary.courier_name}"
            summary = f"Meets your maximum budget of {curr} {preferences.max_budget:,.2f} at {curr} {primary.total_cost:,.2f} (saves {curr} {primary.budget_savings:,.2f})."
        elif preferences.is_flexible_budget and not preferences.is_flexible_deadline:
            headline = f"Meets your delivery deadline: {primary.courier_name}"
            summary = f"Delivers within {primary.estimated_delivery_days} day(s) (target: within {preferences.max_delivery_days} days) at {curr} {primary.total_cost:,.2f}."
        else:
            headline = f"Meets both your budget and delivery deadline: {primary.courier_name}"
            summary = (
                f"Why this wins: meets your {preferences.max_delivery_days}-day deadline "
                f"at the lowest verified price ({curr} {primary.total_cost:,.2f}, saving {curr} {primary.budget_savings:,.2f} from your budget)."
            )

        trade_off = ""
        actions: list[str] = []

    else:
        # Closest Match Fallback
        headline = "No exact match for your preferences"
        summary = (
            "We couldn't find a verified courier that meets both your budget and delivery deadline. "
            "Here are the closest available alternatives based on trade-offs."
        )

        # Primary is best compromise
        primary = ranked_all[0]
        primary.tags.append(CourierComplianceCategory.BEST_COMPROMISE.value)

        # Build explicit trade-off narrative for best compromise
        curr = primary.currency
        if primary.meets_budget and not primary.meets_deadline:
            trade_off = (
                f"Closest match: stays within your budget ({curr} {primary.total_cost:,.2f}), "
                f"but arrives in {primary.estimated_delivery_days} days ({primary.delay_days} day(s) after your {preferences.max_delivery_days}-day deadline)."
            )
        elif primary.meets_deadline and not primary.meets_budget:
            trade_off = (
                f"Closest match: meets your {preferences.max_delivery_days}-day deadline (estimated in {primary.estimated_delivery_days} day(s)), "
                f"but exceeds your budget by {curr} {primary.budget_overrun:,.2f} (total: {curr} {primary.total_cost:,.2f})."
            )
        else:
            trade_off = (
                f"Closest match: requires a compromise on both budget (+{curr} {primary.budget_overrun:,.2f}) "
                f"and delivery time (+{primary.delay_days} day(s))."
            )

        actions = [
            "Increase budget",
            "Allow more delivery time",
            "Compare alternatives",
            "Edit preferences",
        ]

    # Identify special candidate cards: Lowest Cost & Fastest Delivery
    cheapest_sorted = sorted(evaluated, key=lambda c: (c.total_cost, c.estimated_delivery_days or 999, c.courier_code))
    lowest_cost_alt = cheapest_sorted[0]
    if CourierComplianceCategory.LOWEST_COST.value not in lowest_cost_alt.tags:
        lowest_cost_alt.tags.append(CourierComplianceCategory.LOWEST_COST.value)

    with_sla = [c for c in evaluated if c.estimated_delivery_days is not None]
    if with_sla:
        fastest_sorted = sorted(with_sla, key=lambda c: (c.estimated_delivery_days, c.total_cost, c.courier_code))
        fastest_alt = fastest_sorted[0]
        if CourierComplianceCategory.FASTEST_DELIVERY.value not in fastest_alt.tags:
            fastest_alt.tags.append(CourierComplianceCategory.FASTEST_DELIVERY.value)
    else:
        fastest_alt = None

    best_compromise = primary if not is_exact else None

    # Now populate advantages, disadvantages, and relative diffs vs primary recommendation
    for c in evaluated:
        # Calculate diffs
        if primary:
            c.cost_diff_vs_recommended = round(c.total_cost - primary.total_cost, 2)
            if primary.total_cost > 0:
                c.cost_pct_vs_recommended = round((c.cost_diff_vs_recommended / primary.total_cost) * 100.0, 1)

            if c.estimated_delivery_days is not None and primary.estimated_delivery_days is not None:
                c.days_diff_vs_recommended = c.estimated_delivery_days - primary.estimated_delivery_days

        # Advantage & disadvantage narrative
        adv, dis, hint = _generate_narrative_hints(c, primary, preferences)
        c.main_advantage = adv
        c.main_disadvantage = dis
        c.plain_language_hint = hint

    return AdvisorRecommendationResult(
        preferences=preferences,
        has_serviceable_couriers=True,
        is_exact_match=is_exact,
        primary_recommendation=primary,
        best_compromise=best_compromise,
        lowest_cost_alternative=lowest_cost_alt,
        fastest_alternative=fastest_alt,
        exact_matches=exact_matches,
        budget_ok_late=budget_ok_late,
        fast_over_budget=fast_over_budget,
        violates_both_candidates=violates_both,
        all_evaluated=ranked_all,
        unserviceable=unserviceable_quotes,
        headline=headline,
        summary_explanation=summary,
        trade_off_explanation=trade_off,
        suggested_actions=actions,
    )


def _generate_narrative_hints(
    candidate: CourierPreferenceCompliance,
    primary: Optional[CourierPreferenceCompliance],
    preferences: UserShippingPreferences,
) -> Tuple[str, str, str]:
    """
    Generate clean, plain-language advantage, disadvantage, and comparative hint.
    Calculates actual differences dynamically from real numbers.
    """
    curr = candidate.currency
    cost_str = f"{curr} {candidate.total_cost:,.2f}"
    days_str = f"{candidate.estimated_delivery_days} day(s)" if candidate.estimated_delivery_days is not None else "Unspecified SLA"

    # Self-contained advantages/disadvantages
    if candidate.meets_both:
        adv = f"Satisfies all preferences at {cost_str}."
        dis = "Standard service tier; faster options may exist if budget is increased."
    elif candidate.meets_budget and not candidate.meets_deadline:
        adv = f"Stays strictly within budget at {cost_str} (saves {curr} {candidate.budget_savings:,.2f})."
        dis = f"Arrives in {days_str} ({candidate.delay_days} day(s) after requested deadline)."
    elif candidate.meets_deadline and not candidate.meets_budget:
        adv = f"Fast transit time: arrives in {days_str}."
        dis = f"Exceeds budget by {curr} {candidate.budget_overrun:,.2f}."
    else:
        adv = f"Available service on this route."
        dis = f"Exceeds budget by {curr} {candidate.budget_overrun:,.2f} and misses deadline by {candidate.delay_days} day(s)."

    # Comparative hint against primary recommendation
    if not primary or candidate.courier_code == primary.courier_code:
        if candidate.meets_both:
            hint = "Recommended choice: meets all your preferences at the lowest verified price."
        else:
            hint = "Best compromise: closest balance between your budget and delivery deadline."
        return adv, dis, hint

    diff_cost = candidate.cost_diff_vs_recommended or 0.0
    diff_days = candidate.days_diff_vs_recommended

    if diff_cost < 0:
        savings = abs(diff_cost)
        if diff_days is not None and diff_days > 0:
            hint = f"Budget-friendly alternative: saves {curr} {savings:,.2f} but arrives approximately {diff_days} day(s) later."
        elif diff_days is not None and diff_days == 0:
            hint = f"Saves {curr} {savings:,.2f} with the same estimated delivery time."
        elif diff_days is not None and diff_days < 0:
            hint = f"Saves {curr} {savings:,.2f} and arrives {abs(diff_days)} day(s) earlier."
        else:
            hint = f"Saves {curr} {savings:,.2f} compared to the recommendation (delivery time unavailable)."
    elif diff_cost > 0:
        extra = diff_cost
        if diff_days is not None and diff_days < 0:
            hint = f"Costs {curr} {extra:,.2f} more but is estimated to arrive {abs(diff_days)} day(s) earlier."
        elif diff_days is not None and diff_days == 0:
            hint = f"Costs {curr} {extra:,.2f} more with the same transit time."
        elif diff_days is not None and diff_days > 0:
            hint = f"Costs {curr} {extra:,.2f} more and takes {diff_days} day(s) longer."
        else:
            hint = f"Costs {curr} {extra:,.2f} more (delivery estimate unavailable)."
    else:
        # Same cost
        if diff_days is not None and diff_days < 0:
            hint = f"Same price ({cost_str}), but arrives {abs(diff_days)} day(s) earlier."
        elif diff_days is not None and diff_days > 0:
            hint = f"Same price ({cost_str}), but takes {diff_days} day(s) longer."
        else:
            hint = f"Equivalent price and estimated delivery time."

    return adv, dis, hint


# ---------------------------------------------------------------------------
# Interactive Alternative Recommendation Engine (Requirement 1)
# ---------------------------------------------------------------------------

class AlternativeCandidate(BaseModel):
    quote_id: str
    courier_code: str
    courier_name: str
    service_type: str
    total_cost: float
    estimated_delivery_days: Optional[int] = None
    is_serviceable: bool = True
    selection_rationale: str = ""
    cost_diff_vs_baseline: float = 0.0
    days_diff_vs_baseline: Optional[int] = None
    tradeoff_explanation: str = ""
    is_recommended: bool = False
    source_type: str = "DEMO_ADAPTER"
    is_demo_data: bool = True


class AlternativeRecommendationResult(BaseModel):
    preference_applied: str
    selected_recommendation: Optional[AlternativeCandidate] = None
    baseline_recommendation: Optional[AlternativeCandidate] = None
    is_switched: bool = False
    decision_explanation: str
    tradeoff_summary: str = ""
    eligible_alternatives: list[AlternativeCandidate] = Field(default_factory=list)
    unserviceable_candidates: list[dict[str, Any]] = Field(default_factory=list)


def find_alternative_recommendations(
    quotes: list[CourierQuote],
    current_winner_id: Optional[str] = None,
    preference_mode: str = "balanced",
    preferred_courier: Optional[str] = None,
    max_budget: Optional[float] = None,
    max_delivery_days: Optional[int] = None,
    exclude_current_winner: bool = True,
) -> AlternativeRecommendationResult:
    """
    Generate an alternative courier recommendation based on user-selected preference.
    
    Supported preference modes:
    - 'lowest_cost': prioritize lowest landed cost
    - 'fastest': prioritize fastest turnaround SLA
    - 'balanced': balance 60% cost and 40% speed
    - 'preferred_courier': prioritize a specific carrier requested by user
    - 'next_best': offer next viable candidate when user rejects first pick
    """
    if not quotes:
        return AlternativeRecommendationResult(
            preference_applied=preference_mode,
            decision_explanation="No quotes available to evaluate.",
            tradeoff_summary="Cannot generate recommendations without candidate courier quotes.",
        )

    # 1. Separate eligible and unserviceable quotes
    eligible: list[CourierQuote] = [
        q for q in quotes if q.is_serviceable and q.is_eligible and q.cost_breakdown is not None
    ]
    unserviceable: list[CourierQuote] = [
        q for q in quotes if not q.is_serviceable or not q.is_eligible or q.cost_breakdown is None
    ]

    unserviceable_list = [
        {
            "quote_id": q.quote_id,
            "courier_code": q.courier_code,
            "courier_name": q.courier_name,
            "service_type": q.service_type,
            "reason": q.unserviceable_reason or "; ".join(q.eligibility_reasons or []) or "Unserviceable for route or parcel dimensions.",
        }
        for q in unserviceable
    ]

    if not eligible:
        return AlternativeRecommendationResult(
            preference_applied=preference_mode,
            decision_explanation="No eligible couriers can service this destination PIN or satisfy parcel dimensions.",
            tradeoff_summary="All candidate couriers are unserviceable or excluded for this route.",
            unserviceable_candidates=unserviceable_list,
        )

    # 2. Identify baseline quote (current recommended pick or first eligible)
    baseline_q = next((q for q in eligible if q.quote_id == current_winner_id), None) or eligible[0]
    base_cost = baseline_q.cost_breakdown.total_cost if baseline_q.cost_breakdown else 0.0
    base_days = baseline_q.estimated_delivery_days or 3

    baseline_candidate = AlternativeCandidate(
        quote_id=baseline_q.quote_id,
        courier_code=baseline_q.courier_code,
        courier_name=baseline_q.courier_name,
        service_type=baseline_q.service_type,
        total_cost=base_cost,
        estimated_delivery_days=baseline_q.estimated_delivery_days,
        is_serviceable=True,
        selection_rationale="Current baseline winner.",
        cost_diff_vs_baseline=0.0,
        days_diff_vs_baseline=0,
        tradeoff_explanation="Original recommendation.",
        is_recommended=True,
        source_type=getattr(baseline_q, "source_type", "DEMO_ADAPTER"),
        is_demo_data=getattr(baseline_q, "is_demo_data", True),
    )

    # 3. Preference evaluation & re-ranking
    mode = (preference_mode or "balanced").strip().lower()
    selected_quote: Optional[CourierQuote] = None
    decision_reason: str = ""
    pref_applied_label = mode

    # Preference: Preferred Carrier
    if preferred_courier:
        pref_norm = preferred_courier.strip().lower()
        pref_match = next(
            (q for q in eligible if pref_norm in q.courier_code.lower() or pref_norm in q.courier_name.lower()),
            None
        )
        if pref_match:
            selected_quote = pref_match
            pref_applied_label = f"Preferred Carrier: {pref_match.courier_name}"
            decision_reason = f"Selected your preferred carrier '{pref_match.courier_name}'. Fully serviceable for this route at INR {pref_match.cost_breakdown.total_cost:.2f}."
        else:
            # Check if user requested an unserviceable courier
            unsvc_match = next(
                (q for q in unserviceable if pref_norm in q.courier_code.lower() or pref_norm in q.courier_name.lower()),
                None
            )
            if unsvc_match:
                reason = unsvc_match.unserviceable_reason or "; ".join(unsvc_match.eligibility_reasons or []) or "Unserviceable for this route."
                decision_reason = (
                    f"Requested courier '{unsvc_match.courier_name}' cannot be recommended because it is unserviceable or excluded ({reason}). "
                    f"Showing best available alternative."
                )
            else:
                decision_reason = f"Requested carrier '{preferred_courier}' is not in candidate list. Showing best alternative."

    # If no preferred courier matched or not specified, evaluate preference mode
    pref_notice = decision_reason
    if selected_quote is None:
        if mode == "fastest":
            pref_applied_label = "Fastest Delivery"
            # Sort by delivery days ascending, then cost ascending
            sorted_by_speed = sorted(
                eligible,
                key=lambda q: (q.estimated_delivery_days or 99, q.cost_breakdown.total_cost if q.cost_breakdown else 9999.0)
            )
            if exclude_current_winner and len(sorted_by_speed) > 1 and sorted_by_speed[0].quote_id == current_winner_id:
                selected_quote = sorted_by_speed[1]
            else:
                selected_quote = sorted_by_speed[0]

            sla = selected_quote.estimated_delivery_days or 1
            cost = selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0.0
            if selected_quote.quote_id == baseline_q.quote_id:
                mode_reason = f"Current recommendation is already the fastest available eligible option ({sla} Days SLA at INR {cost:.2f})."
            else:
                mode_reason = f"Prioritizes delivery turnaround: Delivers in {sla} Days (INR {cost:.2f})."

        elif mode == "lowest_cost":
            pref_applied_label = "Lowest Shipping Cost"
            sorted_by_cost = sorted(
                eligible,
                key=lambda q: (q.cost_breakdown.total_cost if q.cost_breakdown else 9999.0, q.estimated_delivery_days or 99)
            )
            if exclude_current_winner and len(sorted_by_cost) > 1 and sorted_by_cost[0].quote_id == current_winner_id:
                selected_quote = sorted_by_cost[1]
            else:
                selected_quote = sorted_by_cost[0]

            cost = selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0.0
            sla = selected_quote.estimated_delivery_days or 3
            if selected_quote.quote_id == baseline_q.quote_id:
                mode_reason = f"Current recommendation is already the lowest cost option available (INR {cost:.2f}, {sla} Days SLA)."
            else:
                mode_reason = f"Prioritizes shipping savings: Lowest landed freight cost at INR {cost:.2f} (SLA: {sla} Days)."

        elif mode in ("next_best", "reject"):
            pref_applied_label = "Alternative Choice"
            remaining = [q for q in eligible if q.quote_id != current_winner_id]
            if remaining:
                # Pick the next best eligible candidate
                selected_quote = remaining[0]
                cost = selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0.0
                sla = selected_quote.estimated_delivery_days or 3
                mode_reason = f"Alternative recommendation: {selected_quote.courier_name} at INR {cost:.2f} ({sla} Days SLA)."
            else:
                selected_quote = baseline_q
                mode_reason = f"No other eligible couriers available for this route. {baseline_q.courier_name} remains the only viable carrier."

        else:
            # Default: Balanced mode (composite 60% cost, 40% speed)
            pref_applied_label = "Balanced Cost & Speed"
            min_c = min((q.cost_breakdown.total_cost for q in eligible if q.cost_breakdown), default=1.0)
            max_c = max((q.cost_breakdown.total_cost for q in eligible if q.cost_breakdown), default=min_c + 1.0)
            min_d = min((q.estimated_delivery_days or 3 for q in eligible), default=1)
            max_d = max((q.estimated_delivery_days or 3 for q in eligible), default=min_d + 1)
            c_span = max(max_c - min_c, 1.0)
            d_span = max(max_d - min_d, 1)

            def score_q(q: CourierQuote) -> float:
                cost_norm = ((q.cost_breakdown.total_cost if q.cost_breakdown else max_c) - min_c) / c_span
                speed_norm = ((q.estimated_delivery_days or max_d) - min_d) / d_span
                return 0.6 * cost_norm + 0.4 * speed_norm

            sorted_balanced = sorted(eligible, key=score_q)
            if exclude_current_winner and len(sorted_balanced) > 1 and sorted_balanced[0].quote_id == current_winner_id:
                selected_quote = sorted_balanced[1]
            else:
                selected_quote = sorted_balanced[0]

            cost = selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0.0
            sla = selected_quote.estimated_delivery_days or 3
            mode_reason = f"Balanced trade-off between transit speed ({sla} Days) and landed freight cost (INR {cost:.2f})."

        if pref_notice:
            decision_reason = f"{pref_notice} {mode_reason}"
        else:
            decision_reason = mode_reason

    # Fallback safety
    if selected_quote is None:
        selected_quote = baseline_q

    # 4. Construct alternative candidates with trade-off calculations
    all_alt_candidates: list[AlternativeCandidate] = []
    tradeoff_summary = ""

    for q in eligible:
        cost_q = q.cost_breakdown.total_cost if q.cost_breakdown else 0.0
        days_q = q.estimated_delivery_days
        diff_c = round(cost_q - base_cost, 2)
        diff_d = (days_q - base_days) if days_q is not None and base_days is not None else None

        # Build trade-off explanation
        if diff_c < 0 and diff_d is not None and diff_d > 0:
            trade_text = f"Saves INR {abs(diff_c):.2f} but delivers {diff_d} day(s) later."
        elif diff_c < 0 and diff_d is not None and diff_d <= 0:
            trade_text = f"Saves INR {abs(diff_c):.2f} with faster or equal delivery time."
        elif diff_c > 0 and diff_d is not None and diff_d < 0:
            trade_text = f"Delivers {abs(diff_d)} day(s) faster for an additional INR {diff_c:.2f}."
        elif diff_c > 0 and diff_d is not None and diff_d >= 0:
            trade_text = f"Costs INR {diff_c:.2f} more and takes equal or longer transit."
        elif diff_c == 0:
            trade_text = "Equivalent freight cost."
        else:
            trade_text = f"Cost variance: INR {diff_c:+.2f}."

        is_sel = (q.quote_id == selected_quote.quote_id)
        cand = AlternativeCandidate(
            quote_id=q.quote_id,
            courier_code=q.courier_code,
            courier_name=q.courier_name,
            service_type=q.service_type,
            total_cost=cost_q,
            estimated_delivery_days=days_q,
            is_serviceable=True,
            selection_rationale="Recommended alternative" if is_sel else "Eligible alternative",
            cost_diff_vs_baseline=diff_c,
            days_diff_vs_baseline=diff_d,
            tradeoff_explanation=trade_text,
            is_recommended=is_sel,
            source_type=getattr(q, "source_type", "DEMO_ADAPTER"),
            is_demo_data=getattr(q, "is_demo_data", True),
        )
        all_alt_candidates.append(cand)
        if is_sel:
            tradeoff_summary = trade_text

    selected_cand = next((c for c in all_alt_candidates if c.quote_id == selected_quote.quote_id), None)
    is_switched = (selected_quote.quote_id != baseline_q.quote_id)

    return AlternativeRecommendationResult(
        preference_applied=pref_applied_label,
        selected_recommendation=selected_cand,
        baseline_recommendation=baseline_candidate,
        is_switched=is_switched,
        decision_explanation=decision_reason,
        tradeoff_summary=tradeoff_summary,
        eligible_alternatives=all_alt_candidates,
        unserviceable_candidates=unserviceable_list,
    )
