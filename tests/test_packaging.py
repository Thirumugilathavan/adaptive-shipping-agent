"""
Unit tests for alternative packaging optimization and 3D rotational fit.
"""

from src.domain.models import Dimensions, PackageType, VolumetricRule
from src.domain.packaging import (
    DemoPackagingOption,
    check_3d_fit,
    evaluate_packaging_alternatives,
)


def test_3d_fit_rotational_success():
    """
    Product: 18 x 8 x 12 cm
    Internal Box: 19 x 14 x 9 cm
    Padding: 0.5 cm
    Product with padding: 18.5 x 8.5 x 12.5 cm (sorted: 18.5, 12.5, 8.5)
    Box sorted: 19.0, 14.0, 9.0
    18.5 <= 19.0, 12.5 <= 14.0, 8.5 <= 9.0 -> FITS via rotation!
    """
    prod = Dimensions(length_cm=18.0, width_cm=8.0, height_cm=12.0)
    box_int = Dimensions(length_cm=19.0, width_cm=14.0, height_cm=9.0)

    fits, reason = check_3d_fit(prod, box_int, padding_cm=0.5)
    assert fits is True
    assert reason is None


def test_3d_fit_too_large():
    """Product exceeds internal box dimensions under any rotation."""
    prod = Dimensions(length_cm=30.0, width_cm=10.0, height_cm=10.0)
    box_int = Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0)

    fits, reason = check_3d_fit(prod, box_int, padding_cm=0.5)
    assert fits is False
    assert "exceeds box capacity" in reason


def test_evaluate_packaging_alternatives_savings():
    """
    Product: 14 x 10 x 5 cm (net weight: 0.4 kg)
    Currently packaged in a huge box: 40 x 30 x 20 cm (volume: 24,000 cm3, vol wt: 4.8 kg, slab: 5.0 kg)
    Demo Small Box: 20 x 15 x 10 cm (volume: 3,000 cm3, vol wt: 0.6 kg, slab: 1.0 kg)
    Expected billable weight reduction: 5.0 kg -> 1.0 kg = 4.0 kg saved!
    """
    prod = Dimensions(length_cm=14.0, width_cm=10.0, height_cm=5.0)
    current_dims = Dimensions(length_cm=40.0, width_cm=30.0, height_cm=20.0)

    results = evaluate_packaging_alternatives(
        product_dimensions=prod,
        product_net_weight_kg=0.4,
        current_outer_dimensions=current_dims,
        volumetric_rule=VolumetricRule(divisor=5000.0, billing_step_kg=0.5),
        per_kg_freight_estimate_inr=50.0,
    )

    # At least one feasible option found
    feasible_options = [r for r in results if r.is_feasible]
    assert len(feasible_options) > 0

    best = feasible_options[0]
    assert best.weight_saved_kg > 0.0
    assert best.volume_reduction_pct > 50.0
    assert best.estimated_freight_savings_inr > 0.0
    assert "DEMO" in best.disclaimer
