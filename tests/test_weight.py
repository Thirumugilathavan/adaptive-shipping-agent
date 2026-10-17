"""
Unit tests for volumetric and chargeable weight calculation engine.
"""

import pytest
from src.domain.models import Dimensions, VolumetricRule
from src.domain.weight import (
    calculate_chargeable_weight,
    calculate_volumetric_weight,
    compute_weight_breakdown,
)


def test_volumetric_weight_standard_divisor():
    """
    Test standard 5000 cm3/kg divisor:
    Box: 30 x 20 x 10 cm = 6000 cm3
    6000 / 5000 = 1.2 kg
    """
    dims = Dimensions(length_cm=30.0, width_cm=20.0, height_cm=10.0)
    vol_wt = calculate_volumetric_weight(dims, divisor=5000.0)
    assert vol_wt == 1.2


def test_volumetric_weight_custom_divisor():
    """
    Test courier with custom 4000 cm3/kg divisor:
    Box: 30 x 20 x 10 cm = 6000 cm3
    6000 / 4000 = 1.5 kg
    """
    dims = Dimensions(length_cm=30.0, width_cm=20.0, height_cm=10.0)
    vol_wt = calculate_volumetric_weight(dims, divisor=4000.0)
    assert vol_wt == 1.5


def test_volumetric_weight_invalid_divisor():
    """Invalid divisor <= 0 should raise ValueError."""
    dims = Dimensions(length_cm=10.0, width_cm=10.0, height_cm=10.0)
    with pytest.raises(ValueError):
        calculate_volumetric_weight(dims, divisor=0.0)


def test_chargeable_weight_dead_weight_higher():
    """
    When actual dead weight (2.5 kg) is greater than volumetric weight (1.2 kg),
    chargeable weight should equal 2.5 kg and round to 2.5 kg slab.
    """
    raw, slab = calculate_chargeable_weight(
        actual_weight_kg=2.5,
        volumetric_weight_kg=1.2,
        min_billable_weight_kg=0.5,
        billing_step_kg=0.5,
    )
    assert raw == 2.5
    assert slab == 2.5


def test_chargeable_weight_volumetric_higher():
    """
    When volumetric weight (3.2 kg) is greater than actual weight (1.0 kg),
    chargeable weight should be 3.2 kg, rounding up to 3.5 kg slab (step=0.5).
    """
    raw, slab = calculate_chargeable_weight(
        actual_weight_kg=1.0,
        volumetric_weight_kg=3.2,
        min_billable_weight_kg=0.5,
        billing_step_kg=0.5,
    )
    assert raw == 3.2
    assert slab == 3.5


def test_chargeable_weight_min_billable_enforced():
    """
    When parcel is tiny (0.1 kg actual, 0.05 kg vol),
    minimum billable weight (0.5 kg) must be applied.
    """
    raw, slab = calculate_chargeable_weight(
        actual_weight_kg=0.1,
        volumetric_weight_kg=0.05,
        min_billable_weight_kg=0.5,
        billing_step_kg=0.5,
    )
    assert raw == 0.5
    assert slab == 0.5


def test_compute_weight_breakdown_penalty_flag():
    """
    Verifies the volumetric penalty flag is True when volumetric > actual.
    """
    dims = Dimensions(length_cm=40.0, width_cm=30.0, height_cm=20.0)  # 24000 cm3 / 5000 = 4.8 kg
    rule = VolumetricRule(divisor=5000.0, billing_step_kg=0.5)

    result = compute_weight_breakdown(parcel_weight_kg=2.0, dimensions=dims, rule=rule)
    assert result.actual_weight_kg == 2.0
    assert result.volumetric_weight_kg == 4.8
    assert result.chargeable_weight_kg == 4.8
    assert result.billing_slab_kg == 5.0
    assert result.is_volumetric_penalty is True
