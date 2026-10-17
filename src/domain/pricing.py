"""
Landed cost calculation engine.
Calculates transparent breakdowns: Base Freight, Fuel Surcharges, COD Fees,
Handling/Packaging fees, and GST/Taxes.
"""

from __future__ import annotations

import math
from pydantic import BaseModel, Field
from src.domain.models import CostBreakdown, WeightResult


class CourierPricingRule(BaseModel):
    """
    Configurable pricing rule for a courier service tier.
    Used for deterministic rate calculations.
    """
    courier_code: str
    courier_name: str
    service_type: str
    first_slab_kg: float = Field(default=0.5, gt=0.0)
    first_slab_rate: float = Field(..., ge=0.0)
    additional_slab_rate: float = Field(..., ge=0.0)
    fuel_surcharge_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    cod_fixed_fee: float = Field(default=40.0, ge=0.0)
    cod_percentage_rate: float = Field(default=2.0, ge=0.0, le=50.0)
    min_cod_fee: float = Field(default=40.0, ge=0.0)
    handling_fee: float = Field(default=0.0, ge=0.0)
    gst_tax_rate_pct: float = Field(default=18.0, ge=0.0, le=50.0)
    estimated_delivery_days: int = Field(default=3, ge=1)


def calculate_base_freight(
    billing_slab_kg: float,
    rule: CourierPricingRule,
    billing_step_kg: float = 0.5,
) -> float:
    """
    Calculate base freight according to first slab + additional slab steps.
    """
    if billing_slab_kg <= rule.first_slab_kg:
        return round(rule.first_slab_rate, 2)

    extra_weight = billing_slab_kg - rule.first_slab_kg
    extra_slabs = math.ceil(round(extra_weight / billing_step_kg, 6))
    freight = rule.first_slab_rate + (extra_slabs * rule.additional_slab_rate)
    return round(freight, 2)


def calculate_cod_fee(
    is_cod: bool,
    cod_amount: float,
    rule: CourierPricingRule,
) -> float:
    """
    Calculate COD fee: max of flat fee or percentage of collected cash.
    Prepaid orders incur zero COD fee.
    """
    if not is_cod or cod_amount <= 0.0:
        return 0.0

    percentage_fee = cod_amount * (rule.cod_percentage_rate / 100.0)
    cod_fee = max(rule.cod_fixed_fee, percentage_fee, rule.min_cod_fee)
    return round(cod_fee, 2)


def calculate_landed_cost(
    weight_result: WeightResult,
    pricing_rule: CourierPricingRule,
    is_cod: bool = False,
    cod_amount: float = 0.0,
    billing_step_kg: float = 0.5,
) -> CostBreakdown:
    """
    Perform a full, transparent landed cost computation.
    """
    base_freight = calculate_base_freight(
        billing_slab_kg=weight_result.billing_slab_kg,
        rule=pricing_rule,
        billing_step_kg=billing_step_kg,
    )

    fuel_surcharge = round(base_freight * (pricing_rule.fuel_surcharge_pct / 100.0), 2)
    cod_fee = calculate_cod_fee(is_cod=is_cod, cod_amount=cod_amount, rule=pricing_rule)
    handling_fee = round(pricing_rule.handling_fee, 2)

    subtotal = round(base_freight + fuel_surcharge + cod_fee + handling_fee, 2)
    tax_amount = round(subtotal * (pricing_rule.gst_tax_rate_pct / 100.0), 2)
    total_cost = round(subtotal + tax_amount, 2)

    return CostBreakdown(
        base_freight=base_freight,
        fuel_surcharge=fuel_surcharge,
        fuel_surcharge_rate_pct=pricing_rule.fuel_surcharge_pct,
        cod_fee=cod_fee,
        handling_fee=handling_fee,
        subtotal_before_tax=subtotal,
        tax_amount=tax_amount,
        tax_rate_pct=pricing_rule.gst_tax_rate_pct,
        total_cost=total_cost,
        currency="INR",
    )
