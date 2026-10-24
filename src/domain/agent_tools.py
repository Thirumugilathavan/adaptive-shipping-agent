"""
Agent Tools Definition & Execution Layer.
Exposes modular, observable tools for input validation, PIN serviceability,
weight calculations, quote evaluation, deterministic ranking, recommendation
explanations, human approval gating, idempotent dispatch, and label verification.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.ml.feature_schema import (
    DomesticCourierFeatures,
    PredictionModelType,
    PredictionStatus,
    ReferenceSculptureFeatures,
)
from src.ml.price_predictor import PricePredictionResult, predict_shipment_price
from src.services.price_prediction_service import (
    PricePredictionServiceResponse,
    ShipmentPricePredictionService,
)

logger = logging.getLogger("AgentTools")


# -----------------------------------------------------------------------------
# Tool 1: Input Validation Tool
# -----------------------------------------------------------------------------

class ValidateInputsToolInput(BaseModel):
    length_cm: Optional[float] = None
    width_cm: Optional[float] = None
    height_cm: Optional[float] = None
    parcel_weight_kg: Optional[float] = Field(default=None, alias="weight_kg")
    sender_pin: Optional[str] = None
    recipient_pin: Optional[str] = None
    is_cod: bool = False
    cod_amount: Optional[float] = 0.0

    model_config = {"populate_by_name": True}


def execute_validate_inputs_tool(inputs: Dict[str, Any] | ValidateInputsToolInput) -> Dict[str, Any]:
    """
    Validates dimensional inputs, physical weight, postal PIN codes, and COD settings.
    Returns structured pass/fail status with explicit per-field error messages.
    """
    from src.ui.helpers import (
        validate_pin_code,
        validate_shipment_measurements,
    )

    if isinstance(inputs, dict):
        validated = ValidateInputsToolInput(**inputs)
    else:
        validated = inputs

    is_valid_m, field_errs, err_list = validate_shipment_measurements(
        validated.length_cm, validated.width_cm, validated.height_cm, validated.parcel_weight_kg
    )

    ok_p, err_p = validate_pin_code(validated.sender_pin or "", pin_type="pickup")
    ok_d, err_d = validate_pin_code(validated.recipient_pin or "", pin_type="delivery")

    errors: list[str] = list(err_list)
    if not ok_p and err_p:
        errors.append(err_p)
        field_errs["sender_pin"] = err_p
    if not ok_d and err_d:
        errors.append(err_d)
        field_errs["recipient_pin"] = err_d

    if validated.is_cod:
        if validated.cod_amount is None or validated.cod_amount <= 0:
            msg = "Please enter a valid COD collection amount greater than ₹0."
            errors.append(msg)
            field_errs["cod_amount"] = msg

    is_success = (len(errors) == 0 and is_valid_m and ok_p and ok_d)

    return {
        "tool_name": "validate_shipment_inputs",
        "status": "SUCCESS" if is_success else "FAILED",
        "is_valid": is_success,
        "errors": errors,
        "field_errors": field_errs,
        "validated_inputs": {
            "length_cm": validated.length_cm,
            "width_cm": validated.width_cm,
            "height_cm": validated.height_cm,
            "parcel_weight_kg": validated.parcel_weight_kg,
            "sender_pin": validated.sender_pin,
            "recipient_pin": validated.recipient_pin,
            "is_cod": validated.is_cod,
            "cod_amount": validated.cod_amount if validated.is_cod else 0.0,
        } if is_success else None,
    }


# -----------------------------------------------------------------------------
# Tool 2: PIN Serviceability Tool
# -----------------------------------------------------------------------------

class PinServiceabilityCategory(str, Enum):
    SERVICEABLE = "SERVICEABLE"
    INVALID_FORMAT = "INVALID_FORMAT"
    UNSERVICEABLE_NETWORK = "UNSERVICEABLE_NETWORK"
    CARRIER_UNSERVICEABLE = "CARRIER_UNSERVICEABLE"
    MISSING_RULE_DATA = "MISSING_RULE_DATA"


class CheckPinServiceabilityToolInput(BaseModel):
    origin_pin: str
    destination_pin: str
    courier_codes: Optional[list[str]] = None


def execute_check_pin_serviceability_tool(
    inputs: Dict[str, Any] | CheckPinServiceabilityToolInput,
) -> Dict[str, Any]:
    """
    Deterministic local serviceability tool.
    Validates origin and destination PIN formats and evaluates carrier serviceability
    rules against local configurations.
    
    Explicitly distinguishes:
    1. INVALID_FORMAT: Malformed, non-numeric, or non-6-digit PIN.
    2. UNSERVICEABLE_NETWORK: Unsupported zone (e.g. Army Postal Service Circle 9) or zero couriers servicing the destination.
    3. CARRIER_UNSERVICEABLE: Destination reachable by some couriers, but restricted by specific carriers (e.g. remote PINs).
    4. MISSING_RULE_DATA: Capability rule undefined or unverified.
    5. SERVICEABLE: Fully reachable route.
    
    Disclosure: Evaluated against local capability rules and sandbox carrier profiles.
    Never claims to query a live carrier network.
    """
    from src.providers.demo_adapter import ALL_DEMO_COURIERS

    if isinstance(inputs, dict):
        validated = CheckPinServiceabilityToolInput(**inputs)
    else:
        validated = inputs

    origin = str(validated.origin_pin).strip() if validated.origin_pin else ""
    dest = str(validated.destination_pin).strip() if validated.destination_pin else ""

    # 1. Format check: Origin PIN
    if not origin or not re.fullmatch(r"^[1-9]\d{5}$", origin):
        return {
            "tool_name": "check_pin_serviceability",
            "status": "FAILED",
            "classification": PinServiceabilityCategory.INVALID_FORMAT.value,
            "is_serviceable": False,
            "message": f"Enter a valid 6-digit origin pickup PIN code (must start with digits 1-9, got '{origin}').",
            "origin_pin": origin,
            "destination_pin": dest,
            "serviceable_couriers": [],
            "unserviceable_couriers": [],
            "disclaimer": "Evaluated against deterministic local format rules. No live courier network was queried.",
        }

    # 2. Format check: Destination PIN
    if not dest or not re.fullmatch(r"^[1-9]\d{5}$", dest):
        return {
            "tool_name": "check_pin_serviceability",
            "status": "FAILED",
            "classification": PinServiceabilityCategory.INVALID_FORMAT.value,
            "is_serviceable": False,
            "message": f"Enter a valid 6-digit destination delivery PIN code (must start with digits 1-9, got '{dest}').",
            "origin_pin": origin,
            "destination_pin": dest,
            "serviceable_couriers": [],
            "unserviceable_couriers": [],
            "disclaimer": "Evaluated against deterministic local format rules. No live courier network was queried.",
        }

    # 3. Network Check: Postal Circle 9 (Army Postal Service - APS)
    # Commercial civilian couriers cannot deliver to military postal circles.
    if origin.startswith("9"):
        return {
            "tool_name": "check_pin_serviceability",
            "status": "FAILED",
            "classification": PinServiceabilityCategory.UNSERVICEABLE_NETWORK.value,
            "is_serviceable": False,
            "message": f"Origin PIN {origin} is located in an Army Postal Service (Circle 9) zone. Commercial civilian pickup is unsupported.",
            "origin_pin": origin,
            "destination_pin": dest,
            "serviceable_couriers": [],
            "unserviceable_couriers": [c.courier_code for c in ALL_DEMO_COURIERS],
            "disclaimer": "Evaluated against deterministic local network capability rules. No live courier network was queried.",
        }

    if dest.startswith("9"):
        return {
            "tool_name": "check_pin_serviceability",
            "status": "FAILED",
            "classification": PinServiceabilityCategory.UNSERVICEABLE_NETWORK.value,
            "is_serviceable": False,
            "message": f"Destination PIN {dest} is located in an Army Postal Service (Circle 9) zone. Commercial civilian delivery is unsupported across all couriers.",
            "origin_pin": origin,
            "destination_pin": dest,
            "serviceable_couriers": [],
            "unserviceable_couriers": [c.courier_code for c in ALL_DEMO_COURIERS],
            "disclaimer": "Evaluated against deterministic local network capability rules. No live courier network was queried.",
        }

    # 4. Carrier-Specific Serviceability Rules Evaluation
    serviceable_couriers: list[str] = []
    unserviceable_couriers: list[str] = []
    reasons_by_courier: dict[str, str] = {}

    target_couriers = ALL_DEMO_COURIERS
    if validated.courier_codes:
        target_couriers = [c for c in ALL_DEMO_COURIERS if c.courier_code in validated.courier_codes]

    for cfg in target_couriers:
        p_blocked = [p for p in cfg.blocked_destination_pin_prefixes if dest.startswith(p)]
        s_blocked = [s for s in cfg.blocked_destination_pin_suffixes if dest.endswith(s)]

        if p_blocked:
            unserviceable_couriers.append(cfg.courier_code)
            reasons_by_courier[cfg.courier_code] = (
                f"Destination PIN {dest} is in a remote region (prefix {p_blocked[0]}) not serviced by {cfg.courier_name}."
            )
        elif s_blocked:
            unserviceable_couriers.append(cfg.courier_code)
            reasons_by_courier[cfg.courier_code] = (
                f"Destination PIN {dest} has restricted terminal access (suffix {s_blocked[0]}) in {cfg.courier_name}."
            )
        else:
            serviceable_couriers.append(cfg.courier_code)

    # 5. Classify final outcome
    if not serviceable_couriers:
        classification = PinServiceabilityCategory.UNSERVICEABLE_NETWORK.value
        msg = f"Destination PIN {dest} is unserviceable. None of the {len(target_couriers)} configured couriers provide coverage for this route."
        is_svc = False
    elif unserviceable_couriers:
        classification = PinServiceabilityCategory.CARRIER_UNSERVICEABLE.value
        msg = (
            f"Route is partially serviceable: {len(serviceable_couriers)} courier(s) can deliver to PIN {dest}. "
            f"{len(unserviceable_couriers)} courier(s) excluded due to remote area restrictions."
        )
        is_svc = True
    else:
        classification = PinServiceabilityCategory.SERVICEABLE.value
        msg = f"Route PIN {origin} ➔ PIN {dest} is fully serviceable across all {len(serviceable_couriers)} couriers."
        is_svc = True

    return {
        "tool_name": "check_pin_serviceability",
        "status": "SUCCESS" if is_svc else "FAILED",
        "classification": classification,
        "is_serviceable": is_svc,
        "message": msg,
        "origin_pin": origin,
        "destination_pin": dest,
        "serviceable_couriers": serviceable_couriers,
        "unserviceable_couriers": unserviceable_couriers,
        "unserviceable_reasons": reasons_by_courier,
        "disclaimer": "Evaluated against local capability rules and configured sandbox profiles. No live carrier network was queried.",
    }


# -----------------------------------------------------------------------------
# Tool 3: Billable Weight Calculation Tool
# -----------------------------------------------------------------------------

class CalculateBillableWeightToolInput(BaseModel):
    length_cm: float = Field(..., gt=0.0)
    width_cm: float = Field(..., gt=0.0)
    height_cm: float = Field(..., gt=0.0)
    parcel_weight_kg: float = Field(..., gt=0.0, alias="actual_weight_kg")
    divisors: Optional[list[float]] = [5000.0, 4500.0, 4000.0]

    model_config = {"populate_by_name": True}


def execute_calculate_billable_weight_tool(
    inputs: Dict[str, Any] | CalculateBillableWeightToolInput,
) -> Dict[str, Any]:
    """
    Computes volumetric weight across carrier divisors and determines billable weight slabs.
    """
    from src.domain.models import Dimensions
    from src.domain.weight import calculate_volumetric_weight

    if isinstance(inputs, dict):
        validated = CalculateBillableWeightToolInput(**inputs)
    else:
        validated = inputs

    dims = Dimensions(
        length_cm=validated.length_cm,
        width_cm=validated.width_cm,
        height_cm=validated.height_cm,
    )
    volume_cm3 = dims.volume_cm3
    actual_kg = round(float(validated.parcel_weight_kg), 3)

    divisors = validated.divisors or [5000.0, 4500.0, 4000.0]
    breakdowns = {}
    for div in divisors:
        vol_kg = calculate_volumetric_weight(dims, divisor=div)
        chargeable_kg = max(actual_kg, vol_kg)
        breakdowns[f"divisor_{int(div)}"] = {
            "divisor": div,
            "volumetric_weight_kg": vol_kg,
            "chargeable_weight_kg": chargeable_kg,
            "is_volumetric_penalty": vol_kg > actual_kg,
        }

    standard_vol_5000 = breakdowns.get("divisor_5000", {}).get("volumetric_weight_kg", 0.0)

    return {
        "tool_name": "calculate_billable_weights",
        "status": "SUCCESS",
        "volume_cm3": volume_cm3,
        "actual_weight_kg": actual_kg,
        "standard_volumetric_weight_5000": standard_vol_5000,
        "chargeable_weight_estimated": max(actual_kg, standard_vol_5000),
        "divisor_breakdowns": breakdowns,
    }


# -----------------------------------------------------------------------------
# Tool 4: Deterministic Ranking & Recommendation Explanation Tool
# -----------------------------------------------------------------------------

class RankAndExplainRecommendationsToolInput(BaseModel):
    quotes: list[Dict[str, Any]]
    ranking_mode: str = "LOWEST_COST"
    max_budget: Optional[float] = None
    max_delivery_days: Optional[int] = None
    cost_weight: float = 0.6
    speed_weight: float = 0.4


def execute_rank_and_explain_recommendations_tool(
    inputs: Dict[str, Any] | RankAndExplainRecommendationsToolInput,
) -> Dict[str, Any]:
    """
    Ranks eligible couriers deterministically, strictly excludes unserviceable candidates,
    generates itemized comparison rows, winner reasons, and plain-language loss explanations.
    """
    from src.domain.models import CourierQuote
    from src.domain.ranking import (
        RankingMode,
        RankingPreferences,
        rank_couriers,
    )

    if isinstance(inputs, dict):
        validated = RankAndExplainRecommendationsToolInput(**inputs)
    else:
        validated = inputs

    quotes = [CourierQuote(**q) if isinstance(q, dict) else q for q in validated.quotes]

    mode_map = {
        "lowest_cost": RankingMode.LOWEST_COST,
        "fastest": RankingMode.FASTEST_DELIVERY,
        "fastest_delivery": RankingMode.FASTEST_DELIVERY,
        "balanced": RankingMode.BALANCED,
    }
    mode = mode_map.get(validated.ranking_mode.lower(), RankingMode.LOWEST_COST)

    prefs = RankingPreferences(
        mode=mode,
        max_budget=validated.max_budget,
        max_delivery_days=validated.max_delivery_days,
        cost_weight=validated.cost_weight,
        speed_weight=validated.speed_weight,
    )

    ranking_result = rank_couriers(quotes, prefs)
    winner = ranking_result.selected_winner
    winner_quote_id = winner.quote.quote_id if winner else None

    eligible_quotes = [q for q in quotes if q.is_serviceable and q.is_eligible and q.cost_breakdown is not None]
    cheapest_q = min(eligible_quotes, key=lambda q: (q.cost_breakdown.total_cost, q.estimated_delivery_days or 999)) if eligible_quotes else None
    with_sla = [q for q in eligible_quotes if q.estimated_delivery_days is not None]
    fastest_q = min(with_sla, key=lambda q: (q.estimated_delivery_days, q.cost_breakdown.total_cost)) if with_sla else None

    trade_off_text = ""
    if cheapest_q and fastest_q and cheapest_q.courier_code != fastest_q.courier_code:
        cost_diff = round(fastest_q.cost_breakdown.total_cost - cheapest_q.cost_breakdown.total_cost, 2)
        days_diff = (cheapest_q.estimated_delivery_days or 0) - (fastest_q.estimated_delivery_days or 0)
        trade_off_text = (
            f"Trade-off: Choosing {cheapest_q.courier_name} saves ₹{cost_diff:.2f} "
            f"but adds {days_diff} business day(s) to transit compared to {fastest_q.courier_name} "
            f"({cheapest_q.estimated_delivery_days} days vs {fastest_q.estimated_delivery_days} days)."
        )
    elif len(eligible_quotes) == 1:
        trade_off_text = "Single eligible courier satisfies all constraints on this route. No trade-off required."
    elif len(eligible_quotes) > 1 and cheapest_q and fastest_q and cheapest_q.courier_code == fastest_q.courier_code:
        trade_off_text = f"{cheapest_q.courier_name} offers both the lowest price and fastest transit time."

    comparison_table = []
    loss_reasons: dict[str, list[str]] = {}
    winner_reasons: list[str] = []

    if winner:
        w_quote = winner.quote
        w_cost = winner.total_cost
        w_days = winner.estimated_delivery_days

        if prefs.mode == RankingMode.LOWEST_COST:
            winner_reasons.append(f"Lowest total landed shipping cost (₹{w_cost:.2f}).")
            if ranking_result.cost_margin_over_runner_up:
                winner_reasons.append(f"Saves ₹{ranking_result.cost_margin_over_runner_up:.2f} compared to runner-up.")
        elif prefs.mode == RankingMode.FASTEST_DELIVERY:
            winner_reasons.append(f"Fastest transit delivery SLA ({w_days} business days).")
            if ranking_result.days_margin_over_runner_up:
                winner_reasons.append(f"Delivers {abs(ranking_result.days_margin_over_runner_up)} days faster than runner-up.")
        else:
            winner_reasons.append(f"Optimal balanced trade-off between price (₹{w_cost:.2f}) and speed ({w_days} days).")
            winner_reasons.append(f"Balanced score of {winner.balanced_score:.1f} out of 100.")

        winner_reasons.append("Verified 100% serviceable route with no dimensional or weight restrictions.")

    for q in quotes:
        is_win = (winner_quote_id == q.quote_id)
        reasons_list: list[str] = []

        if is_win:
            outcome = "WINNER"
            reason_text = "Recommended pick under active ranking policy."
        elif not q.is_serviceable:
            outcome = "EXCLUDED"
            r_msg = q.unserviceable_reason or "Route is not serviceable."
            reasons_list.append(f"Excluded for Serviceability: {r_msg}")
            reason_text = f"Unserviceable: {r_msg}"
        elif not q.is_eligible:
            outcome = "EXCLUDED"
            r_msg = "; ".join(q.eligibility_reasons or ["Operational restriction violated."])
            reasons_list.append(f"Excluded for Eligibility: {r_msg}")
            reason_text = f"Ineligible: {r_msg}"
        elif q.cost_breakdown is None:
            outcome = "EXCLUDED"
            reasons_list.append("Excluded: Missing cost breakdown.")
            reason_text = "Excluded: Pricing unavailable."
        else:
            outcome = "ELIGIBLE_ALTERNATIVE"
            if winner:
                c_diff = round(q.cost_breakdown.total_cost - winner.total_cost, 2)
                if c_diff > 0:
                    pct = round((c_diff / winner.total_cost) * 100.0, 1)
                    reasons_list.append(f"Landed cost is ₹{c_diff:.2f} higher (+{pct}%) than recommended {winner.quote.courier_name}.")
                elif c_diff < 0:
                    reasons_list.append(f"Cheaper by ₹{abs(c_diff):.2f}, but lost due to policy prioritization.")

                if q.estimated_delivery_days is not None and winner.estimated_delivery_days is not None:
                    d_diff = q.estimated_delivery_days - winner.estimated_delivery_days
                    if d_diff > 0:
                        reasons_list.append(f"Estimated transit takes {d_diff} additional business day(s) vs {winner.quote.courier_name}.")
                    elif d_diff < 0:
                        reasons_list.append(f"Arrives {abs(d_diff)} day(s) faster, but cost was higher.")

            if prefs.max_budget and q.cost_breakdown.total_cost > prefs.max_budget:
                reasons_list.append(f"Exceeds preferred budget limit of ₹{prefs.max_budget:.2f}.")

            if prefs.max_delivery_days and q.estimated_delivery_days and q.estimated_delivery_days > prefs.max_delivery_days:
                reasons_list.append(f"Exceeds preferred delivery deadline of {prefs.max_delivery_days} days.")

            reason_text = "; ".join(reasons_list) if reasons_list else "Eligible alternative option."

        loss_reasons[q.courier_code] = reasons_list

        cost_val = q.cost_breakdown.total_cost if q.cost_breakdown else None
        base_val = q.cost_breakdown.base_freight if q.cost_breakdown else None
        fuel_val = q.cost_breakdown.fuel_surcharge if q.cost_breakdown else None
        cod_val = q.cost_breakdown.cod_fee if q.cost_breakdown else None
        tax_val = q.cost_breakdown.tax_amount if q.cost_breakdown else None
        slab_val = q.weight_result.billing_slab_kg if q.weight_result else None
        div_val = q.weight_result.volumetric_divisor if q.weight_result else None

        comparison_table.append({
            "quote_id": q.quote_id,
            "courier_code": q.courier_code,
            "courier_name": q.courier_name,
            "service_type": q.service_type,
            "is_serviceable": q.is_serviceable,
            "is_eligible": q.is_eligible,
            "serviceability_status": "Serviceable" if q.is_serviceable else f"Unserviceable ({q.unserviceable_reason})",
            "billable_weight_kg": slab_val,
            "volumetric_divisor": div_val,
            "base_freight": base_val,
            "fuel_surcharge": fuel_val,
            "cod_fee": cod_val,
            "tax_amount": tax_val,
            "total_cost": cost_val,
            "estimated_delivery_days": q.estimated_delivery_days,
            "is_winner": is_win,
            "outcome_status": outcome,
            "reason_summary": reason_text,
        })

    return {
        "tool_name": "rank_and_explain_recommendations",
        "status": "SUCCESS" if winner else "FAILED_NO_ELIGIBLE_COURIERS",
        "has_eligible_couriers": winner is not None,
        "ranking_mode": prefs.mode.value,
        "selected_winner": winner.model_dump() if winner else None,
        "winner_quote_id": winner_quote_id,
        "winner_reasons": winner_reasons,
        "trade_off_analysis": trade_off_text,
        "loss_reasons_by_courier": loss_reasons,
        "comparison_table": comparison_table,
        "decision_explanation": ranking_result.decision_explanation,
        "total_eligible": ranking_result.total_eligible,
        "total_ineligible": ranking_result.total_ineligible,
    }


# -----------------------------------------------------------------------------
# Tool 5: Predict Shipment Price Tool (Model 1 ML Benchmark)
# -----------------------------------------------------------------------------

PREDICT_SHIPMENT_PRICE_TOOL_DEFINITION = {
    "name": "predict_shipment_price",
    "description": (
        "Estimate domestic Indian shipping price or reference sculpture freight cost using trained "
        "machine learning regression models. Produces a non-binding statistical benchmark price "
        "with 90% confidence intervals. Does NOT override or replace binding carrier provider quotes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "origin_pin": {"type": "string"},
            "destination_pin": {"type": "string"},
            "length_cm": {"type": "number"},
            "width_cm": {"type": "number"},
            "height_cm": {"type": "number"},
            "parcel_weight_kg": {"type": "number"},
            "package_type": {"type": "string", "default": "box"},
            "courier_code": {"type": "string", "default": "DEMO_AIR_EXPRESS"},
            "is_cod": {"type": "boolean", "default": False},
            "cod_amount": {"type": "number", "default": 0.0},
            "model_type": {"type": "string", "default": "domestic_courier"},
            "quote_cost": {"type": "number"},
            "quote_courier_name": {"type": "string"},
        },
        "required": ["origin_pin", "destination_pin", "length_cm", "width_cm", "height_cm", "parcel_weight_kg"],
    },
}


class PredictShipmentPriceToolInput(BaseModel):
    origin_pin: str
    destination_pin: str
    length_cm: float = Field(..., gt=0.0)
    width_cm: float = Field(..., gt=0.0)
    height_cm: float = Field(..., gt=0.0)
    parcel_weight_kg: float = Field(..., gt=0.0)
    package_type: str = Field("box")
    courier_code: str = Field("DEMO_AIR_EXPRESS")
    is_cod: bool = Field(False)
    cod_amount: float = Field(0.0, ge=0.0)
    model_type: str = Field("domestic_courier")
    quote_cost: Optional[float] = None
    quote_courier_name: Optional[str] = None
    reference_features: Optional[Dict[str, Any]] = None


def execute_predict_shipment_price_tool(
    inputs: Dict[str, Any] | PredictShipmentPriceToolInput,
) -> Dict[str, Any]:
    """
    Execute predict_shipment_price tool with strict parameter validation,
    fallback handling, and clear separation of statistical prediction from binding quotes.
    """
    if isinstance(inputs, dict):
        validated = PredictShipmentPriceToolInput(**inputs)
    else:
        validated = inputs

    service = ShipmentPricePredictionService()

    if validated.model_type in ("model2_catboost", "courier_intelligence", "model2"):
        from src.domain.model2_adapter import model2_adapter
        raw_input = {
            "origin_pin": validated.origin_pin,
            "dest_pin": validated.destination_pin,
            "length_cm": validated.length_cm,
            "width_cm": validated.width_cm,
            "height_cm": validated.height_cm,
            "parcel_weight_kg": validated.parcel_weight_kg,
            "courier_code": validated.courier_code,
        }
        p_pred = model2_adapter.predict_price(raw_input)
        d_pred = model2_adapter.predict_delivery_days(raw_input)
        cov = model2_adapter.check_historical_pin_coverage(
            validated.origin_pin, validated.destination_pin, validated.courier_code
        )
        ci = (
            (round(p_pred.predicted_price_inr - 43.34, 2), round(p_pred.predicted_price_inr + 43.34, 2))
            if p_pred.predicted_price_inr is not None
            else None
        )
        return {
            "tool_name": "predict_shipment_price",
            "model_type": "model2_catboost",
            "status": p_pred.status,
            "predicted_price": p_pred.predicted_price_inr,
            "predicted_delivery_days": d_pred.rounded_days,
            "historical_pin_coverage": cov.model_dump(),
            "currency": "INR",
            "confidence_interval": ci,
            "model_version": "2.0.0",
            "is_provider_quote": False,
            "explanation": (
                f"Model 2 CatBoost historical freight rate prediction: ₹{p_pred.predicted_price_inr or '--'} "
                f"| SLA turnaround estimate: {d_pred.rounded_days or '--'} days. "
                f"Historical synthetic coverage: {cov.status}."
            ),
            "limitations": [
                "CatBoost regression trained on 14 million historical synthetic shipment records across 10 courier brands.",
                "SLA turnaround estimate trained on 2 million records.",
                "Carrier binding quotes strictly govern actual booking and billing.",
            ],
            "advice": "Model 2 CatBoost courier intelligence benchmark. Carrier quotes strictly govern booking.",
            "error_message": p_pred.error_message,
        }

    if validated.model_type == "reference_sculpture":
        ref_dict = validated.reference_features or {
            "Artist Reputation": 0.5,
            "Height": validated.height_cm,
            "Width": validated.width_cm,
            "Weight": validated.parcel_weight_kg * 1000.0,
            "Material": "Brass",
            "Price Of Sculpture": 500.0,
            "Base Shipping Price": 20.0,
            "International": "No",
            "Express Shipment": "Yes" if "EXPRESS" in validated.courier_code.upper() else "No",
            "Installation Included": "No",
            "Transport": "Airways",
            "Fragile": "Yes",
            "Customer Information": "Working Class",
            "Remote": "No",
            "Scheduled Date": "2026-10-23",
            "Delivery Date": "2026-10-26",
        }
        ref_feat = ReferenceSculptureFeatures(**ref_dict)
        pred = predict_shipment_price(ref_feat)
        return {
            "tool_name": "predict_shipment_price",
            "model_type": "reference_sculpture",
            "status": pred.status.value,
            "predicted_price": pred.predicted_price,
            "currency": pred.currency,
            "confidence_interval": pred.confidence_interval,
            "model_version": pred.model_version,
            "is_provider_quote": False,
            "explanation": pred.explanation,
            "limitations": pred.limitations,
            "advice": "Reference model predicts specialty sculpture freight in USD. Not representative of Indian domestic parcel rates.",
        }

    domestic_feat = DomesticCourierFeatures(
        origin_pin=validated.origin_pin,
        destination_pin=validated.destination_pin,
        length_cm=validated.length_cm,
        width_cm=validated.width_cm,
        height_cm=validated.height_cm,
        parcel_weight_kg=validated.parcel_weight_kg,
        package_type=validated.package_type,
        courier_code=validated.courier_code,
        is_cod=validated.is_cod,
        cod_amount=validated.cod_amount,
    )

    active_quote = None
    if validated.quote_cost is not None:
        active_quote = {
            "cost": validated.quote_cost,
            "courier_code": validated.courier_code,
            "courier_name": validated.quote_courier_name or validated.courier_code,
        }

    resp: PricePredictionServiceResponse = service.predict_and_compare(domestic_feat, active_quote=active_quote)

    return {
        "tool_name": "predict_shipment_price",
        "model_type": "domestic_courier",
        "status": resp.prediction.status.value,
        "predicted_price": resp.prediction.predicted_price,
        "currency": resp.prediction.currency,
        "confidence_interval": resp.prediction.confidence_interval,
        "model_version": resp.prediction.model_version,
        "feature_schema_version": resp.prediction.feature_schema_version,
        "is_provider_quote": False,
        "input_summary": resp.prediction.input_summary,
        "explanation": resp.prediction.explanation,
        "limitations": resp.prediction.limitations,
        "quote_comparison": resp.quote_comparison.model_dump() if resp.quote_comparison else None,
        "agent_advice": resp.agent_advice,
        "recommended_action": resp.recommended_action,
    }


# -----------------------------------------------------------------------------
# Tool 6: Human Approval Gate Tool
# -----------------------------------------------------------------------------

class RecordHumanApprovalToolInput(BaseModel):
    order_reference: str
    quote_id: str
    courier_code: str
    approved_by: str
    total_cost: float
    notes: Optional[str] = None


def execute_record_human_approval_tool(
    inputs: Dict[str, Any] | RecordHumanApprovalToolInput,
) -> Dict[str, Any]:
    """
    Validates operator signature and price proposal, recording an immutable human approval gate record.
    """
    from src.persistence.state_machine import ApprovalRecord

    if isinstance(inputs, dict):
        validated = RecordHumanApprovalToolInput(**inputs)
    else:
        validated = inputs

    if not validated.approved_by or len(validated.approved_by.strip()) < 2:
        return {
            "tool_name": "record_human_approval",
            "status": "FAILED",
            "error": "Operator approval identity must be at least 2 characters.",
        }

    if validated.total_cost < 0:
        return {
            "tool_name": "record_human_approval",
            "status": "FAILED",
            "error": "Approved price proposal cannot be negative.",
        }

    approval = ApprovalRecord(
        order_reference=validated.order_reference,
        quote_id=validated.quote_id,
        courier_code=validated.courier_code,
        approved_by=validated.approved_by.strip(),
        total_cost=validated.total_cost,
        notes=validated.notes,
    )

    return {
        "tool_name": "record_human_approval",
        "status": "SUCCESS",
        "approval": approval.model_dump(),
    }


# -----------------------------------------------------------------------------
# Tool 7: Idempotent Dispatch Execution Tool
# -----------------------------------------------------------------------------

class ExecuteIdempotentDispatchToolInput(BaseModel):
    order: Dict[str, Any]
    quote: Dict[str, Any]
    approval: Dict[str, Any]
    provider_mode: str = "demo"


def execute_idempotent_dispatch_tool(
    inputs: Dict[str, Any] | ExecuteIdempotentDispatchToolInput,
) -> Dict[str, Any]:
    """
    Executes shipment booking under strict idempotency locks, preventing duplicate waybills.
    """
    from src.domain.models import CourierQuote, OrderInput
    from src.domain.reconciliation import ShipmentDispatchService
    from src.persistence.state_machine import ApprovalRecord
    from src.providers.registry import get_provider

    if isinstance(inputs, dict):
        validated = ExecuteIdempotentDispatchToolInput(**inputs)
    else:
        validated = inputs

    order = OrderInput(**validated.order) if isinstance(validated.order, dict) else validated.order
    quote = CourierQuote(**validated.quote) if isinstance(validated.quote, dict) else validated.quote
    approval = ApprovalRecord(**validated.approval) if isinstance(validated.approval, dict) else validated.approval

    if not quote.is_serviceable or not quote.is_eligible:
        return {
            "tool_name": "execute_idempotent_dispatch",
            "status": "FAILED",
            "error": "Cannot dispatch an unserviceable or ineligible courier quote.",
        }

    provider = get_provider(validated.provider_mode)
    service = ShipmentDispatchService()

    try:
        res = service.attempt_dispatch(order, quote, approval, provider)
        return {
            "tool_name": "execute_idempotent_dispatch",
            "status": "SUCCESS",
            "shipment_id": res.shipment_id,
            "tracking_number": res.tracking_number,
            "label_status": res.label_status,
            "label_data": res.label_data,
        }
    except Exception as exc:
        return {
            "tool_name": "execute_idempotent_dispatch",
            "status": "FAILED",
            "error": str(exc),
        }


# -----------------------------------------------------------------------------
# Tool 8: Label Generation & AWB Verification Tool
# -----------------------------------------------------------------------------

class GenerateAndVerifyLabelToolInput(BaseModel):
    order: Dict[str, Any]
    quote: Dict[str, Any]
    shipment_id: str
    tracking_number: str
    label_url_or_data: Optional[str] = None
    pickup_reference: Optional[str] = None


def execute_generate_and_verify_label_tool(
    inputs: Dict[str, Any] | GenerateAndVerifyLabelToolInput,
) -> Dict[str, Any]:
    """
    Generates printable shipping labels and executes the independent 5-point post-dispatch audit.
    """
    from src.domain.models import CourierQuote, OrderInput
    from src.domain.verification import ShipmentVerificationService
    from src.providers.base import ShipmentResult

    if isinstance(inputs, dict):
        validated = GenerateAndVerifyLabelToolInput(**inputs)
    else:
        validated = inputs

    order = OrderInput(**validated.order) if isinstance(validated.order, dict) else validated.order
    quote = CourierQuote(**validated.quote) if isinstance(validated.quote, dict) else validated.quote

    ship_res = ShipmentResult(
        success=True,
        shipment_id=validated.shipment_id,
        tracking_number=validated.tracking_number,
        label_status="AVAILABLE",
        label_url_or_data=validated.label_url_or_data or f"data:application/pdf;base64,DEMO_LABEL_{validated.tracking_number}",
        order_reference=order.order_reference,
        courier_code=quote.courier_code,
        pickup_reference=validated.pickup_reference or f"PKP-{validated.tracking_number[-8:]}",
        pickup_scheduled_window="10:00 AM - 01:00 PM IST",
    )

    verifier = ShipmentVerificationService()
    report = verifier.verify_shipment(order, quote, ship_res)

    return {
        "tool_name": "generate_and_verify_label",
        "status": "SUCCESS" if report.is_verified else "FAILED_VERIFICATION",
        "overall_status": report.overall_status.value,
        "is_verified": report.is_verified,
        "tracking_number": validated.tracking_number,
        "pickup_reference": ship_res.pickup_reference,
        "pickup_scheduled_window": ship_res.pickup_scheduled_window,
        "verification_summary": report.summary,
        "checkpoints": [c.model_dump() for c in report.checkpoints],
    }


def execute_predict_model2_intelligence_tool(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dedicated Model 2 Courier Intelligence Tool.
    Computes CatBoost rate benchmark, SLA transit forecast, and historical PIN coverage.
    """
    from src.domain.model2_adapter import model2_adapter

    p_pred = model2_adapter.predict_price(inputs)
    d_pred = model2_adapter.predict_delivery_days(inputs)
    origin = inputs.get("origin_pin") or inputs.get("sender_pin") or inputs.get("pickup_pin") or ""
    dest = inputs.get("destination_pin") or inputs.get("dest_pin") or inputs.get("recipient_pin") or inputs.get("delivery_pin") or ""
    courier = inputs.get("courier_name") or inputs.get("courier_code") or "BlueDart"

    cov = model2_adapter.check_historical_pin_coverage(origin, dest, courier)

    status_str = "SUCCESS" if p_pred.status == "SUCCESS" else p_pred.status

    return {
        "tool_name": "predict_model2_intelligence",
        "status": status_str,
        "price_prediction": p_pred.model_dump(),
        "delivery_prediction": d_pred.model_dump(),
        "pin_coverage": cov.model_dump(),
        "is_catboost_available": model2_adapter.is_catboost_available(),
        "disclaimer": (
            "Model 2 estimates are non-binding statistical benchmarks. "
            "Binding contractual courier quotes always govern dispatch booking."
        ),
    }


# -----------------------------------------------------------------------------
# Tool Registry & Unified Dispatcher
# -----------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Any] = {
    "validate_shipment_inputs": execute_validate_inputs_tool,
    "check_pin_serviceability": execute_check_pin_serviceability_tool,
    "calculate_billable_weights": execute_calculate_billable_weight_tool,
    "rank_and_explain_recommendations": execute_rank_and_explain_recommendations_tool,
    "predict_shipment_price": execute_predict_shipment_price_tool,
    "predict_model2_intelligence": execute_predict_model2_intelligence_tool,
    "predict_courier_intelligence": execute_predict_model2_intelligence_tool,
    "record_human_approval": execute_record_human_approval_tool,
    "execute_idempotent_dispatch": execute_idempotent_dispatch_tool,
    "generate_and_verify_label": execute_generate_and_verify_label_tool,
}


def dispatch_tool(tool_name: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Unified entrypoint for invoking any agent tool in the orchestrator."""
    executor = TOOL_REGISTRY.get(tool_name)
    if not executor:
        raise ValueError(f"Unknown agent tool '{tool_name}'. Available tools: {list(TOOL_REGISTRY.keys())}")
    return executor(inputs)
