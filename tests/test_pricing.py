"""
Unit tests for landed cost calculations: base freight, fuel surcharge, COD fee, tax.
"""

from src.domain.models import WeightResult
from src.domain.pricing import (
    CourierPricingRule,
    calculate_base_freight,
    calculate_cod_fee,
    calculate_landed_cost,
)


def get_sample_rule() -> CourierPricingRule:
    return CourierPricingRule(
        courier_code="EXPRESS_AIR",
        courier_name="Express Air Cargo",
        service_type="Air Express",
        first_slab_kg=0.5,
        first_slab_rate=50.0,
        additional_slab_rate=30.0,
        fuel_surcharge_pct=10.0,
        cod_fixed_fee=40.0,
        cod_percentage_rate=2.0,
        min_cod_fee=40.0,
        handling_fee=10.0,
        gst_tax_rate_pct=18.0,
        estimated_delivery_days=2,
    )


def test_base_freight_first_slab():
    """Weight within first 0.5 kg slab should incur only first_slab_rate."""
    rule = get_sample_rule()
    freight = calculate_base_freight(billing_slab_kg=0.5, rule=rule)
    assert freight == 50.0


def test_base_freight_multiple_slabs():
    """
    Weight of 1.5 kg:
    First slab (0.5 kg): 50.0
    Extra weight: 1.0 kg -> 2 slabs of 0.5 kg
    Extra freight: 2 * 30.0 = 60.0
    Total freight: 110.0
    """
    rule = get_sample_rule()
    freight = calculate_base_freight(billing_slab_kg=1.5, rule=rule)
    assert freight == 110.0


def test_cod_fee_prepaid():
    """Prepaid orders should have zero COD fee."""
    rule = get_sample_rule()
    fee = calculate_cod_fee(is_cod=False, cod_amount=0.0, rule=rule)
    assert fee == 0.0


def test_cod_fee_flat_minimum():
    """For small COD amount (e.g. 500 INR), 2% is 10 INR, so minimum 40 INR applies."""
    rule = get_sample_rule()
    fee = calculate_cod_fee(is_cod=True, cod_amount=500.0, rule=rule)
    assert fee == 40.0


def test_cod_fee_percentage_higher():
    """For larger COD amount (e.g. 5000 INR), 2% is 100 INR, which exceeds 40 INR minimum."""
    rule = get_sample_rule()
    fee = calculate_cod_fee(is_cod=True, cod_amount=5000.0, rule=rule)
    assert fee == 100.0


def test_full_landed_cost_calculation():
    """
    End-to-end landed cost test:
    Weight: 1.5 kg (slab=1.5)
    Base freight: 110.0
    Fuel surcharge (10%): 11.0
    COD fee (prepaid): 0.0
    Handling fee: 10.0
    Subtotal: 110 + 11 + 0 + 10 = 131.0
    GST (18%): 131 * 0.18 = 23.58
    Total cost: 131 + 23.58 = 154.58
    """
    rule = get_sample_rule()
    weight = WeightResult(
        actual_weight_kg=1.4,
        volumetric_weight_kg=1.2,
        chargeable_weight_kg=1.4,
        billing_slab_kg=1.5,
        divisor_used=5000.0,
        is_volumetric_penalty=False,
    )

    cost = calculate_landed_cost(
        weight_result=weight,
        pricing_rule=rule,
        is_cod=False,
        cod_amount=0.0,
    )

    assert cost.base_freight == 110.0
    assert cost.fuel_surcharge == 11.0
    assert cost.cod_fee == 0.0
    assert cost.handling_fee == 10.0
    assert cost.subtotal_before_tax == 131.0
    assert cost.tax_amount == 23.58
    assert cost.total_cost == 154.58
