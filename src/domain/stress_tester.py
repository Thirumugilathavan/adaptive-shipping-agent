"""
Shipping Decision Stress Tester and Recommendation Fragility Analysis.
Evaluates the stability and sensitivity of courier recommendations when dimensions,
fuel surcharges, packaging, or delivery constraints experience real-world variance.

ALL OUTPUTS ARE PROMINENTLY MARKED AS SIMULATION DATA.
DOES NOT MODIFY ORIGINAL RECORDS, REAL QUOTES, OR SHIPMENT DATABASE STATE.
"""

from __future__ import annotations

import copy
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from src.domain.models import CourierQuote, Dimensions, OrderInput, WeightResult
from src.domain.packaging import DemoPackagingOption, evaluate_packaging_alternatives
from src.domain.pricing import CourierPricingRule, calculate_landed_cost
from src.domain.ranking import (
    RankedCourierQuote,
    RankingMode,
    RankingPreferences,
    RankingResult,
    rank_couriers,
)
from src.domain.weight import compute_weight_breakdown
from src.providers.demo_adapter import DEMO_COURIERS, DemoCourierConfig


class StressScenario(BaseModel):
    """
    Hypothetical scenario specification for evaluating recommendation stability.
    """
    scenario_id: str
    scenario_name: str
    category: str = Field(..., description="'DIMENSION', 'PRICE', 'DEADLINE', or 'BUDGET'")
    dimension_multiplier: float = Field(default=1.0, gt=0.0, description="Scale factor for L, W, H")
    dimension_offset_cm: float = Field(default=0.0, ge=0.0, description="Fixed padding/bulge offset in cm")
    fuel_surcharge_delta_pct: float = Field(default=0.0, description="Additive change in fuel surcharge %")
    max_delivery_days_override: Optional[int] = Field(default=None, ge=1, description="Strict SLA constraint")
    max_budget_override: Optional[float] = Field(default=None, gt=0.0, description="Strict budget constraint")
    is_simulation: bool = Field(default=True, description="Always True: Marks data as non-live simulation")
    disclaimer: str = Field(
        default="STRESS TEST SIMULATION: Hypothetical scenario for decision robustness analysis.",
        description="Clear disclosure for user"
    )


class ScenarioEvaluationOutcome(BaseModel):
    """Result of running a single stress test scenario."""
    scenario: StressScenario
    adjusted_dimensions: Dimensions
    winner_code: Optional[str] = None
    winner_name: Optional[str] = None
    winner_cost: Optional[float] = None
    winner_transit_days: Optional[int] = None
    winner_chargeable_weight_kg: Optional[float] = None
    rank_switched: bool
    cost_difference_from_baseline: Optional[float] = None
    causative_factor: Optional[str] = None
    transition_explanation: str
    is_eligible_winner_present: bool = True
    ranked_quotes: list[RankedCourierQuote] = Field(default_factory=list)


class FragilityAnalysisReport(BaseModel):
    """
    Distinctive Innovation: Recommendation Fragility Analysis.
    Quantifies decision sensitivity, price margins, and packaging stabilization impact.
    """
    is_fragile: bool = Field(..., description="True if a small (<=5%) measurement or price change flips the winner")
    most_sensitive_input: str = Field(..., description="'DIMENSIONS', 'FUEL_PRICE', 'DELIVERY_SLA', or 'ROBUST'")
    critical_price_margin_inr: Optional[float] = Field(
        default=None,
        description="Cost difference over runner-up before winner loses its position"
    )
    fragility_verdict: str = Field(..., description="Executive summary of recommendation defensibility")
    packaging_stabilization_effect: str = Field(
        ...,
        description="Evaluation of whether packaging optimization dampens volumetric volatility"
    )
    packaging_savings_potential_inr: float = 0.0
    insufficient_evidence_flags: list[str] = Field(
        default_factory=list,
        description="Data quality warnings (e.g. sole candidate, virtual tie, missing SLA)"
    )
    limitations_disclaimer: str = (
        "LIMITATIONS OF ANALYSIS: Stress testing simulates deterministic permutations of dimensions, "
        "fuel surcharges, and delivery deadlines. It measures scenario robustness across the tested set, "
        "not empirical real-world probability distributions."
    )


class StressTestSummaryReport(BaseModel):
    """Comprehensive auditable decision report for the stress test suite."""
    baseline_order_reference: str
    baseline_winner_code: str
    baseline_winner_name: str
    baseline_winner_cost: float
    baseline_winner_sla_days: int
    baseline_ranking_mode: RankingMode
    total_scenarios_evaluated: int
    scenarios_baseline_maintained: int
    scenario_stability_pct: float = Field(
        ...,
        description="Percentage of evaluated scenarios where baseline winner retained rank #1"
    )
    switched_scenarios_count: int
    ineligible_scenarios_count: int
    scenario_outcomes: list[ScenarioEvaluationOutcome]
    fragility_analysis: FragilityAnalysisReport
    is_simulation: bool = True
    disclaimer: str = (
        "SCENARIO ROBUSTNESS DISCLOSURE: Scenario stability indicates the fraction of evaluated "
        "deterministic scenarios in which the baseline winner remains #1. It is NOT an empirical "
        "statistical probability of real-world carrier performance."
    )


def apply_scenario_to_dimensions(dims: Dimensions, scenario: StressScenario) -> Dimensions:
    """Apply scenario scaling and fixed offset to parcel dimensions without mutating input."""
    new_l = round((dims.length_cm * scenario.dimension_multiplier) + scenario.dimension_offset_cm, 2)
    new_w = round((dims.width_cm * scenario.dimension_multiplier) + scenario.dimension_offset_cm, 2)
    new_h = round((dims.height_cm * scenario.dimension_multiplier) + scenario.dimension_offset_cm, 2)
    return Dimensions(length_cm=new_l, width_cm=new_w, height_cm=new_h)


def generate_standard_stress_scenarios() -> list[StressScenario]:
    """
    Construct standard matrix of reproducible stress scenarios:
    - Dimensional measurement variations: -20%, -10%, -5%, +5%, +10%, +20%, and +2 cm packaging bulge.
    - Quote/Fuel variations: +5%, +10%.
    - Delivery deadline tightening: strict 2 days.
    - Budget limits: ₹300 cap.
    """
    scenarios: list[StressScenario] = [
        # Dimensions
        StressScenario(
            scenario_id="DIM_MINUS_20",
            scenario_name="Dimensions -20% (Compact Compression)",
            category="DIMENSION",
            dimension_multiplier=0.80,
        ),
        StressScenario(
            scenario_id="DIM_MINUS_10",
            scenario_name="Dimensions -10% (Tighter Packaging)",
            category="DIMENSION",
            dimension_multiplier=0.90,
        ),
        StressScenario(
            scenario_id="DIM_MINUS_05",
            scenario_name="Dimensions -5% (Minor Fit Adjustment)",
            category="DIMENSION",
            dimension_multiplier=0.95,
        ),
        StressScenario(
            scenario_id="DIM_PLUS_05",
            scenario_name="Dimensions +5% (Minor Bulge / Tape)",
            category="DIMENSION",
            dimension_multiplier=1.05,
        ),
        StressScenario(
            scenario_id="DIM_PLUS_10",
            scenario_name="Dimensions +10% (Carrier Audit Adjustment)",
            category="DIMENSION",
            dimension_multiplier=1.10,
        ),
        StressScenario(
            scenario_id="DIM_PLUS_20",
            scenario_name="Dimensions +20% (Loose Over-packaging)",
            category="DIMENSION",
            dimension_multiplier=1.20,
        ),
        StressScenario(
            scenario_id="DIM_BULGE_2CM",
            scenario_name="Dimensional Bulge (+2 cm All Sides)",
            category="DIMENSION",
            dimension_multiplier=1.0,
            dimension_offset_cm=2.0,
        ),
        # Fuel Surcharges
        StressScenario(
            scenario_id="FUEL_PLUS_05",
            scenario_name="Fuel Surcharge Spike (+5%)",
            category="PRICE",
            fuel_surcharge_delta_pct=5.0,
        ),
        StressScenario(
            scenario_id="FUEL_PLUS_10",
            scenario_name="Fuel Surcharge Spike (+10%)",
            category="PRICE",
            fuel_surcharge_delta_pct=10.0,
        ),
        # Delivery SLA Constraints
        StressScenario(
            scenario_id="SLA_STRICT_2D",
            scenario_name="Urgent Delivery Deadline (Max 2 Days)",
            category="DEADLINE",
            max_delivery_days_override=2,
        ),
        StressScenario(
            scenario_id="SLA_STRICT_3D",
            scenario_name="Standard Express Deadline (Max 3 Days)",
            category="DEADLINE",
            max_delivery_days_override=3,
        ),
        # Budget Constraints
        StressScenario(
            scenario_id="BUDGET_CAP_300",
            scenario_name="Tight Shipping Budget Cap (₹300.00)",
            category="BUDGET",
            max_budget_override=300.0,
        ),
    ]
    return scenarios


def evaluate_courier_under_scenario(
    config: DemoCourierConfig,
    order: OrderInput,
    adjusted_dims: Dimensions,
    scenario: StressScenario,
) -> Optional[CourierQuote]:
    """
    Recalculate courier quote under perturbed scenario inputs.
    Reuses existing domain weight and pricing engines without duplicating logic.
    """
    # Check physical constraints first
    dest_pin = order.recipient.pin_code
    for prefix in config.blocked_destination_pin_prefixes:
        if dest_pin.startswith(prefix):
            return None
    for suffix in config.blocked_destination_pin_suffixes:
        if dest_pin.endswith(suffix):
            return None
    if order.package_type not in config.allowed_package_types:
        return None
    if order.parcel_weight_kg > config.max_weight_kg:
        return None
    if order.is_cod and order.cod_amount > config.max_cod_amount:
        return None

    # Compute weight using courier's own volumetric rule
    weight_res = compute_weight_breakdown(
        parcel_weight_kg=order.parcel_weight_kg,
        dimensions=adjusted_dims,
        rule=config.volumetric_rule,
    )

    # Adjust pricing rule for fuel delta if present
    pricing = copy.deepcopy(config.pricing_rule)
    if scenario.fuel_surcharge_delta_pct != 0.0:
        pricing.fuel_surcharge_pct = max(0.0, pricing.fuel_surcharge_pct + scenario.fuel_surcharge_delta_pct)

    cost = calculate_landed_cost(
        weight_result=weight_res,
        pricing_rule=pricing,
        is_cod=order.is_cod,
        cod_amount=order.cod_amount,
        billing_step_kg=config.volumetric_rule.billing_step_kg,
    )

    return CourierQuote(
        quote_id=f"STRESS-{config.courier_code}-{scenario.scenario_id}",
        courier_code=config.courier_code,
        courier_name=config.courier_name,
        service_type=config.service_type,
        estimated_delivery_days=config.pricing_rule.estimated_delivery_days,
        is_serviceable=True,
        is_eligible=True,
        weight_result=weight_res,
        cost_breakdown=cost,
        source_type="STRESS_TEST_SIMULATION",
        source_id="stress-tester-v1",
        input_hash="simulation-hash",
        decision_explanation=f"Simulated quote under scenario '{scenario.scenario_name}'.",
        is_demo_data=True,
        missing_evidence=["Simulated stress scenario"],
    )


class ShippingDecisionStressTester:
    """
    Reusable domain engine for evaluating recommendation stability under perturbation.
    Includes Recommendation Fragility Analysis and packaging stabilization checks.
    """

    def __init__(self, couriers: Optional[list[DemoCourierConfig]] = None):
        self._couriers = couriers or DEMO_COURIERS

    def run_stress_test(
        self,
        order: OrderInput,
        baseline_preferences: Optional[RankingPreferences] = None,
        custom_scenarios: Optional[list[StressScenario]] = None,
    ) -> StressTestSummaryReport:
        """
        Execute comprehensive stress testing without mutating original order or state.
        """
        prefs = baseline_preferences or RankingPreferences(mode=RankingMode.LOWEST_COST)
        scenarios = custom_scenarios or generate_standard_stress_scenarios()

        # 1. Baseline Evaluation
        baseline_quotes: list[CourierQuote] = []
        for cfg in self._couriers:
            q = evaluate_courier_under_scenario(
                config=cfg,
                order=order,
                adjusted_dims=order.dimensions,
                scenario=StressScenario(
                    scenario_id="BASELINE",
                    scenario_name="Baseline Unperturbed",
                    category="BASELINE",
                ),
            )
            if q:
                baseline_quotes.append(q)

        baseline_rank_res = rank_couriers(baseline_quotes, prefs)
        if not baseline_rank_res.selected_winner:
            raise ValueError("Cannot perform stress testing: No couriers are eligible under baseline order conditions.")

        base_winner = baseline_rank_res.selected_winner
        base_code = base_winner.quote.courier_code
        base_cost = base_winner.total_cost

        # 2. Iterate Scenarios
        outcomes: list[ScenarioEvaluationOutcome] = []
        maintained_count = 0
        switched_count = 0
        ineligible_count = 0

        # Sensitivity tracking
        dimension_switches = 0
        price_switches = 0
        deadline_switches = 0
        is_minor_shift_fragile = False

        for sc in scenarios:
            adj_dims = apply_scenario_to_dimensions(order.dimensions, sc)

            # Scenario preferences (apply overrides if present)
            sc_prefs = copy.deepcopy(prefs)
            if sc.max_delivery_days_override is not None:
                sc_prefs.max_delivery_days = sc.max_delivery_days_override
            if sc.max_budget_override is not None:
                sc_prefs.max_budget = sc.max_budget_override

            sc_quotes: list[CourierQuote] = []
            for cfg in self._couriers:
                q = evaluate_courier_under_scenario(cfg, order, adj_dims, sc)
                if q:
                    sc_quotes.append(q)

            rank_res = rank_couriers(sc_quotes, sc_prefs)
            sc_winner = rank_res.selected_winner

            if not sc_winner:
                ineligible_count += 1
                outcomes.append(
                    ScenarioEvaluationOutcome(
                        scenario=sc,
                        adjusted_dimensions=adj_dims,
                        winner_code=None,
                        winner_name=None,
                        winner_cost=None,
                        winner_transit_days=None,
                        winner_chargeable_weight_kg=None,
                        rank_switched=True,
                        cost_difference_from_baseline=None,
                        causative_factor=f"ALL_INELIGIBLE_UNDER_{sc.category}",
                        transition_explanation=f"No couriers met operational criteria under scenario '{sc.scenario_name}'.",
                        is_eligible_winner_present=False,
                        ranked_quotes=[],
                    )
                )
                continue

            winner_switched = sc_winner.quote.courier_code != base_code
            cost_diff = round(sc_winner.total_cost - base_cost, 2)

            if winner_switched:
                switched_count += 1
                causative_factor = f"{sc.category}_VARIATION"
                explanation = (
                    f"Recommendation switched from '{base_winner.quote.courier_name}' to "
                    f"'{sc_winner.quote.courier_name}' (Cost: ₹{sc_winner.total_cost:.2f}, "
                    f"SLA: {sc_winner.estimated_delivery_days}d). "
                    f"Triggered by {sc.scenario_name}."
                )
                if sc.category == "DIMENSION":
                    dimension_switches += 1
                    if sc.scenario_id in ("DIM_PLUS_05", "DIM_MINUS_05"):
                        is_minor_shift_fragile = True
                elif sc.category == "PRICE":
                    price_switches += 1
                    if sc.scenario_id == "FUEL_PLUS_05":
                        is_minor_shift_fragile = True
                elif sc.category == "DEADLINE":
                    deadline_switches += 1
            else:
                maintained_count += 1
                causative_factor = None
                explanation = (
                    f"Baseline winner '{base_winner.quote.courier_name}' maintained rank #1 "
                    f"(Cost: ₹{sc_winner.total_cost:.2f}, Δ ₹{cost_diff:+.2f})."
                )

            outcomes.append(
                ScenarioEvaluationOutcome(
                    scenario=sc,
                    adjusted_dimensions=adj_dims,
                    winner_code=sc_winner.quote.courier_code,
                    winner_name=sc_winner.quote.courier_name,
                    winner_cost=sc_winner.total_cost,
                    winner_transit_days=sc_winner.estimated_delivery_days,
                    winner_chargeable_weight_kg=sc_winner.chargeable_weight_kg,
                    rank_switched=winner_switched,
                    cost_difference_from_baseline=cost_diff,
                    causative_factor=causative_factor,
                    transition_explanation=explanation,
                    is_eligible_winner_present=True,
                    ranked_quotes=rank_res.ranked_candidates,
                )
            )

        total_scenarios = len(scenarios)
        stability_pct = round((maintained_count / total_scenarios) * 100.0, 1) if total_scenarios > 0 else 0.0

        # 3. Recommendation Fragility Analysis (Part C)
        fragility_report = self._perform_fragility_analysis(
            order=order,
            baseline_result=baseline_rank_res,
            is_minor_fragile=is_minor_shift_fragile,
            dim_switches=dimension_switches,
            price_switches=price_switches,
            deadline_switches=deadline_switches,
            stability_pct=stability_pct,
        )

        return StressTestSummaryReport(
            baseline_order_reference=order.order_reference,
            baseline_winner_code=base_code,
            baseline_winner_name=base_winner.quote.courier_name,
            baseline_winner_cost=base_cost,
            baseline_winner_sla_days=base_winner.estimated_delivery_days,
            baseline_ranking_mode=prefs.mode,
            total_scenarios_evaluated=total_scenarios,
            scenarios_baseline_maintained=maintained_count,
            scenario_stability_pct=stability_pct,
            switched_scenarios_count=switched_count,
            ineligible_scenarios_count=ineligible_count,
            scenario_outcomes=outcomes,
            fragility_analysis=fragility_report,
            is_simulation=True,
        )

    def _perform_fragility_analysis(
        self,
        order: OrderInput,
        baseline_result: RankingResult,
        is_minor_fragile: bool,
        dim_switches: int,
        price_switches: int,
        deadline_switches: int,
        stability_pct: float,
    ) -> FragilityAnalysisReport:
        """
        Evaluate recommendation fragility, critical price margins, and packaging damping.
        """
        base_winner = baseline_result.selected_winner
        runner_up = baseline_result.ranked_candidates[1] if len(baseline_result.ranked_candidates) > 1 else None

        # Critical price margin: how much winner's cost can increase before runner-up wins
        critical_margin = baseline_result.cost_margin_over_runner_up

        # Sensitivity determination
        switch_scores = {
            "DIMENSIONS": dim_switches,
            "FUEL_PRICE": price_switches,
            "DELIVERY_SLA": deadline_switches,
        }
        most_sensitive = max(switch_scores, key=lambda k: switch_scores[k])
        if switch_scores[most_sensitive] == 0:
            most_sensitive = "ROBUST"

        # Evidence checks
        evidence_flags: list[str] = []
        if len(baseline_result.ranked_candidates) <= 1:
            evidence_flags.append("Sole eligible candidate: No comparative margin could be evaluated.")
        elif critical_margin is not None and critical_margin <= 5.0:
            evidence_flags.append(
                f"Virtual tie: Winner leads runner-up by only ₹{critical_margin:.2f}. "
                f"Minor carrier surcharge variances will flip the ranking."
            )
        if any(q.is_demo_data for q in baseline_result.ranked_candidates):
            evidence_flags.append("Quotes derived from demo adapter: Real carrier quote required before booking.")

        # Packaging stabilization effect:
        # Test if optimal demo packaging dampens volumetric volatility
        pack_opts = evaluate_packaging_alternatives(
            product_dimensions=Dimensions(
                length_cm=round(order.dimensions.length_cm * 0.7, 1),
                width_cm=round(order.dimensions.width_cm * 0.7, 1),
                height_cm=round(order.dimensions.height_cm * 0.7, 1),
            ),
            product_net_weight_kg=order.parcel_weight_kg,
            current_outer_dimensions=order.dimensions,
        )
        feasible_pack = [p for p in pack_opts if p.is_feasible and p.estimated_freight_savings_inr > 0]
        if feasible_pack:
            best_pack = feasible_pack[0]
            savings_potential = best_pack.estimated_freight_savings_inr
            pack_effect = (
                f"Downsizing to '{best_pack.box_name}' eliminates {best_pack.volume_reduction_pct:.1f}% dead space, "
                f"reducing volumetric penalty and providing an estimated ₹{savings_potential:.2f} buffer against "
                f"dimensional expansion audit penalties."
            )
        else:
            savings_potential = 0.0
            pack_effect = "Current packaging is tightly fitted; packaging downsizing provides minimal additional stabilization."

        # Verdict
        if is_minor_fragile:
            verdict = (
                f"FRAGILE RECOMMENDATION: Winner '{base_winner.quote.courier_name}' switches under "
                f"minor (<=5%) measurement or surcharge adjustments. Margin over runner-up is "
                f"{f'₹{critical_margin:.2f}' if critical_margin else 'minimal'}."
            )
        elif stability_pct >= 80.0:
            verdict = (
                f"HIGHLY STABLE RECOMMENDATION: Winner '{base_winner.quote.courier_name}' maintained "
                f"rank #1 in {stability_pct}% of tested stress permutations."
            )
        else:
            verdict = (
                f"MODERATE ROBUSTNESS ({stability_pct}% scenario stability): Winner is vulnerable primarily "
                f"to {most_sensitive.lower()} variations."
            )

        return FragilityAnalysisReport(
            is_fragile=is_minor_fragile,
            most_sensitive_input=most_sensitive,
            critical_price_margin_inr=critical_margin,
            fragility_verdict=verdict,
            packaging_stabilization_effect=pack_effect,
            packaging_savings_potential_inr=savings_potential,
            insufficient_evidence_flags=evidence_flags,
        )
