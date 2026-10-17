"""
Alternative Packaging Optimizer.
Evaluates 3D rotational fit against configurable packaging options,
quantifying volumetric weight reduction, dead space elimination, and potential cost savings.

IMPORTANT: All predefined box sizes are explicitly labelled as DEMO PACKAGING OPTIONS
for demonstration purposes only. They are not official courier standards.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field
from src.domain.models import Dimensions, PackageType, VolumetricRule, WeightResult
from src.domain.weight import compute_weight_breakdown


class DemoPackagingOption(BaseModel):
    """
    Configurable packaging option.
    Explicitly labeled as a demonstration option.
    """
    box_id: str
    box_name: str
    outer_dimensions: Dimensions
    internal_dimensions: Dimensions
    tare_weight_kg: float = Field(default=0.05, ge=0.0, description="Weight of empty packaging")
    package_type: PackageType = PackageType.BOX
    is_demo_option: bool = Field(
        default=True,
        description="Explicit flag: Demo packaging option, not an official courier rule"
    )
    disclaimer: str = Field(
        default="DEMO PACKAGING OPTION: For demonstration and comparison only.",
        description="User-facing disclaimer"
    )


class PackagingComparisonResult(BaseModel):
    """Audit-ready evaluation of an alternative packaging option."""
    box_id: str
    box_name: str
    package_type: PackageType
    is_feasible: bool
    rejection_reason: Optional[str] = None
    original_volume_cm3: float
    new_volume_cm3: float
    volume_difference_cm3: float
    volume_reduction_pct: float
    original_billable_slab_kg: float
    new_billable_slab_kg: float
    weight_saved_kg: float
    estimated_freight_savings_inr: float
    disclaimer: str = "DEMO PACKAGING EVALUATION: Non-binding simulation."


# Default catalogue of demo packaging options (fully customizable)
DEMO_PACKAGING_CATALOGUE: list[DemoPackagingOption] = [
    DemoPackagingOption(
        box_id="DEMO-FLYER-S",
        box_name="Demo Small Polybag Flyer (15 x 12 x 3 cm)",
        outer_dimensions=Dimensions(length_cm=15.0, width_cm=12.0, height_cm=3.0),
        internal_dimensions=Dimensions(length_cm=14.5, width_cm=11.5, height_cm=2.5),
        tare_weight_kg=0.02,
        package_type=PackageType.FLYER,
    ),
    DemoPackagingOption(
        box_id="DEMO-BOX-S",
        box_name="Demo Small Corrugated Box (20 x 15 x 10 cm)",
        outer_dimensions=Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0),
        internal_dimensions=Dimensions(length_cm=19.0, width_cm=14.0, height_cm=9.0),
        tare_weight_kg=0.08,
        package_type=PackageType.BOX,
    ),
    DemoPackagingOption(
        box_id="DEMO-BOX-M",
        box_name="Demo Medium Box (25 x 20 x 15 cm)",
        outer_dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        internal_dimensions=Dimensions(length_cm=24.0, width_cm=19.0, height_cm=14.0),
        tare_weight_kg=0.12,
        package_type=PackageType.BOX,
    ),
    DemoPackagingOption(
        box_id="DEMO-BOX-L",
        box_name="Demo Large Box (35 x 25 x 20 cm)",
        outer_dimensions=Dimensions(length_cm=35.0, width_cm=25.0, height_cm=20.0),
        internal_dimensions=Dimensions(length_cm=33.5, width_cm=23.5, height_cm=18.5),
        tare_weight_kg=0.20,
        package_type=PackageType.BOX,
    ),
    DemoPackagingOption(
        box_id="DEMO-BOX-XL",
        box_name="Demo Extra-Large Box (45 x 35 x 30 cm)",
        outer_dimensions=Dimensions(length_cm=45.0, width_cm=35.0, height_cm=30.0),
        internal_dimensions=Dimensions(length_cm=43.0, width_cm=33.0, height_cm=28.0),
        tare_weight_kg=0.35,
        package_type=PackageType.BOX,
    ),
]


def check_3d_fit(
    product_dims: Dimensions,
    package_internal_dims: Dimensions,
    padding_cm: float = 0.5,
) -> tuple[bool, Optional[str]]:
    """
    Check if a product fits inside internal package dimensions under any orthogonal 3D rotation.
    Sorts dimensions descending (bounding box invariant test).
    """
    effective_product = sorted(
        [
            product_dims.length_cm + padding_cm,
            product_dims.width_cm + padding_cm,
            product_dims.height_cm + padding_cm,
        ],
        reverse=True,
    )
    box_capacity = sorted(
        [
            package_internal_dims.length_cm,
            package_internal_dims.width_cm,
            package_internal_dims.height_cm,
        ],
        reverse=True,
    )

    for prod_dim, box_dim in zip(effective_product, box_capacity):
        if prod_dim > box_dim:
            return False, f"Product dimension ({prod_dim:.1f} cm with padding) exceeds box capacity ({box_dim:.1f} cm)"

    return True, None


def evaluate_packaging_alternatives(
    product_dimensions: Dimensions,
    product_net_weight_kg: float,
    current_outer_dimensions: Dimensions,
    volumetric_rule: VolumetricRule | None = None,
    per_kg_freight_estimate_inr: float = 50.0,
    packaging_catalogue: list[DemoPackagingOption] | None = None,
) -> list[PackagingComparisonResult]:
    """
    Compare current packaging against available demo packaging options.
    Returns ranked alternatives highlighting potential dead space reduction and cost savings.
    """
    rule = volumetric_rule or VolumetricRule()
    catalogue = packaging_catalogue or DEMO_PACKAGING_CATALOGUE

    # Current benchmark
    current_weight = compute_weight_breakdown(product_net_weight_kg, current_outer_dimensions, rule)
    current_vol = current_outer_dimensions.volume_cm3
    results: list[PackagingComparisonResult] = []

    for option in catalogue:
        fits, reason = check_3d_fit(product_dimensions, option.internal_dimensions)
        new_vol = option.outer_dimensions.volume_cm3
        vol_diff = round(current_vol - new_vol, 2)
        vol_reduction_pct = round((vol_diff / current_vol) * 100.0, 1) if current_vol > 0 else 0.0

        if fits:
            new_gross_weight = product_net_weight_kg + option.tare_weight_kg
            new_weight = compute_weight_breakdown(new_gross_weight, option.outer_dimensions, rule)
            weight_saved = max(0.0, round(current_weight.billing_slab_kg - new_weight.billing_slab_kg, 3))
            freight_savings = round(weight_saved * per_kg_freight_estimate_inr, 2)

            results.append(
                PackagingComparisonResult(
                    box_id=option.box_id,
                    box_name=option.box_name,
                    package_type=option.package_type,
                    is_feasible=True,
                    rejection_reason=None,
                    original_volume_cm3=current_vol,
                    new_volume_cm3=new_vol,
                    volume_difference_cm3=vol_diff,
                    volume_reduction_pct=vol_reduction_pct,
                    original_billable_slab_kg=current_weight.billing_slab_kg,
                    new_billable_slab_kg=new_weight.billing_slab_kg,
                    weight_saved_kg=weight_saved,
                    estimated_freight_savings_inr=freight_savings,
                )
            )
        else:
            results.append(
                PackagingComparisonResult(
                    box_id=option.box_id,
                    box_name=option.box_name,
                    package_type=option.package_type,
                    is_feasible=False,
                    rejection_reason=reason,
                    original_volume_cm3=current_vol,
                    new_volume_cm3=new_vol,
                    volume_difference_cm3=vol_diff,
                    volume_reduction_pct=vol_reduction_pct,
                    original_billable_slab_kg=current_weight.billing_slab_kg,
                    new_billable_slab_kg=current_weight.billing_slab_kg,
                    weight_saved_kg=0.0,
                    estimated_freight_savings_inr=0.0,
                )
            )

    # Sort feasible options by highest volume reduction first
    results.sort(key=lambda x: (not x.is_feasible, -x.estimated_freight_savings_inr, -x.volume_reduction_pct))
    return results
