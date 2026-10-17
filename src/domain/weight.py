"""
Volumetric and chargeable weight calculation engine.
Follows standard logistics formulas with fully configurable courier rules.
"""

import math
from src.domain.models import Dimensions, VolumetricRule, WeightResult


def calculate_volumetric_weight(dimensions: Dimensions, divisor: float = 5000.0) -> float:
    """
    Calculate volumetric weight in kilograms:
        Volumetric Weight (kg) = (Length cm * Width cm * Height cm) / Divisor

    Note: 5000 cm3/kg is a configurable default used for demonstration purposes.
    Different couriers and freight modes use different divisors (e.g. 4000, 5000, 6000).
    """
    if divisor <= 0.0:
        raise ValueError(f"Volumetric divisor must be strictly positive, got {divisor}")
    vol_wt = dimensions.volume_cm3 / divisor
    return round(vol_wt, 3)


def calculate_chargeable_weight(
    actual_weight_kg: float,
    volumetric_weight_kg: float,
    min_billable_weight_kg: float = 0.5,
    billing_step_kg: float = 0.5,
) -> tuple[float, float]:
    """
    Determine chargeable weight and rounded billing slab:
        Chargeable Weight = max(Actual Dead Weight, Volumetric Weight, Min Billable Weight)
        Billing Slab = Rounded UP to nearest billing step (e.g., 0.5 kg increments)
    """
    if actual_weight_kg <= 0.0:
        raise ValueError(f"Actual weight must be strictly positive, got {actual_weight_kg}")
    if min_billable_weight_kg <= 0.0:
        raise ValueError("Min billable weight must be strictly positive")
    if billing_step_kg <= 0.0:
        raise ValueError("Billing step must be strictly positive")

    raw_chargeable = max(actual_weight_kg, volumetric_weight_kg, min_billable_weight_kg)
    # Ceiling to nearest billing_step
    steps = math.ceil(round(raw_chargeable / billing_step_kg, 6))
    billing_slab = round(steps * billing_step_kg, 3)
    return round(raw_chargeable, 3), billing_slab


def compute_weight_breakdown(
    parcel_weight_kg: float,
    dimensions: Dimensions,
    rule: VolumetricRule | None = None,
) -> WeightResult:
    """
    Generate an audit-ready WeightResult comparing physical vs volumetric properties.
    """
    active_rule = rule or VolumetricRule()
    vol_wt = calculate_volumetric_weight(dimensions, divisor=active_rule.divisor)
    chargeable, slab = calculate_chargeable_weight(
        actual_weight_kg=parcel_weight_kg,
        volumetric_weight_kg=vol_wt,
        min_billable_weight_kg=active_rule.min_billable_weight_kg,
        billing_step_kg=active_rule.billing_step_kg,
    )
    is_penalty = vol_wt > parcel_weight_kg

    return WeightResult(
        actual_weight_kg=round(parcel_weight_kg, 3),
        volumetric_weight_kg=vol_wt,
        chargeable_weight_kg=chargeable,
        billing_slab_kg=slab,
        divisor_used=active_rule.divisor,
        is_volumetric_penalty=is_penalty,
    )
