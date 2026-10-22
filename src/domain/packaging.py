"""
Alternative Packaging Optimizer.
Evaluates 3D rotational fit against configurable packaging options,
quantifying volumetric weight reduction, dead space elimination, and potential cost savings.

IMPORTANT: All predefined box sizes are explicitly labelled as DEMO PACKAGING OPTIONS
for demonstration purposes only. They are not official courier standards.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import itertools
from pydantic import BaseModel, Field
from src.domain.models import Dimensions, PackageType, VolumetricRule, WeightResult
from src.domain.weight import compute_weight_breakdown
from src.domain.dimension_engine import (
    evaluate_orthogonal_fit,
    evaluate_courier_dimension_eligibility,
    CourierCapabilityRule,
    CourierEligibilityReport,
    EligibilityStatus,
    RuleVerificationStatus,
    OFFICIAL_DOCUMENTED_CAPABILITIES,
    compute_girth,
    compute_length_plus_girth,
)


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


class CustomPackaging(BaseModel):
    """
    User-defined custom-size packaging.
    """
    packaging_id: str
    name: str
    package_type: PackageType = PackageType.BOX
    outer_dimensions: Dimensions
    wall_thickness_cm: float = Field(default=0.5, ge=0.0)
    padding_cm: float = Field(default=2.0, ge=0.0)
    tare_weight_kg: float = Field(default=0.5, ge=0.0)
    internal_dimensions: Optional[Dimensions] = None
    created_at: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if self.internal_dimensions is None:
            self.internal_dimensions = Dimensions(
                length_cm=max(0.1, round(self.outer_dimensions.length_cm - (2 * self.wall_thickness_cm), 2)),
                width_cm=max(0.1, round(self.outer_dimensions.width_cm - (2 * self.wall_thickness_cm), 2)),
                height_cm=max(0.1, round(self.outer_dimensions.height_cm - (2 * self.wall_thickness_cm), 2)),
            )


class FitEvaluationResult(BaseModel):
    """Result of evaluating orthogonal 3D fit of a padded product into internal package dimensions."""
    is_feasible: bool
    rejection_reason: Optional[str] = None
    successful_orientation: Optional[Tuple[float, float, float]] = None
    padded_product_dims: Tuple[float, float, float]
    internal_package_dims: Tuple[float, float, float]
    all_orientations_evaluated: List[Dict[str, Any]] = Field(default_factory=list)


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
    is_custom: bool = False
    outer_dimensions: Optional[Dimensions] = None
    internal_dimensions: Optional[Dimensions] = None
    padded_product_dimensions: Optional[Tuple[float, float, float]] = None
    successful_orientation: Optional[Tuple[float, float, float]] = None
    courier_eligibility: Dict[str, str] = Field(default_factory=dict)
    courier_status_summary: str = "Courier size eligibility not verified"
    courier_reports: Dict[str, Any] = Field(default_factory=dict)
    tare_weight_kg: float = 0.05
    new_gross_weight_kg: float = 0.0
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


def evaluate_orthogonal_fit(
    product_dims: Dimensions,
    outer_dims: Dimensions,
    wall_thickness_cm: float = 0.5,
    padding_cm: float = 2.0,
) -> FitEvaluationResult:
    """
    Check if a product padded with padding_cm on each side fits inside internal package dimensions
    under any of the 6 orthogonal 3D rotations.

    Math:
    - Internal package dims: (outer_L - 2*wall, outer_W - 2*wall, outer_H - 2*wall)
    - Padded product dims: (prod_L + 2*pad, prod_W + 2*pad, prod_H + 2*pad)
    """
    int_l = round(outer_dims.length_cm - (2 * wall_thickness_cm), 2)
    int_w = round(outer_dims.width_cm - (2 * wall_thickness_cm), 2)
    int_h = round(outer_dims.height_cm - (2 * wall_thickness_cm), 2)
    internal_dims = (int_l, int_w, int_h)

    pad_l = round(product_dims.length_cm + (2 * padding_cm), 2)
    pad_w = round(product_dims.width_cm + (2 * padding_cm), 2)
    pad_h = round(product_dims.height_cm + (2 * padding_cm), 2)
    padded_dims = (pad_l, pad_w, pad_h)

    if int_l <= 0 or int_w <= 0 or int_h <= 0:
        return FitEvaluationResult(
            is_feasible=False,
            rejection_reason=f"Packaging wall thickness ({wall_thickness_cm} cm) exceeds outer dimensions, leaving non-positive internal cavity ({int_l}x{int_w}x{int_h} cm).",
            padded_product_dims=padded_dims,
            internal_package_dims=internal_dims,
        )

    # 6 orthogonal permutations of the padded product
    base_dims = [pad_l, pad_w, pad_h]
    unique_rotations = list(set(itertools.permutations(base_dims)))

    all_evals = []
    feasible_rotation = None

    for rot in unique_rotations:
        fits = (rot[0] <= int_l) and (rot[1] <= int_w) and (rot[2] <= int_h)
        all_evals.append({
            "orientation": rot,
            "fits": fits,
            "length_fit": rot[0] <= int_l,
            "width_fit": rot[1] <= int_w,
            "height_fit": rot[2] <= int_h,
        })
        if fits and feasible_rotation is None:
            feasible_rotation = rot

    if feasible_rotation is not None:
        return FitEvaluationResult(
            is_feasible=True,
            rejection_reason=None,
            successful_orientation=feasible_rotation,
            padded_product_dims=padded_dims,
            internal_package_dims=internal_dims,
            all_orientations_evaluated=all_evals,
        )

    # If infeasible, build clear descriptive explanation
    max_p = max(padded_dims)
    max_i = max(internal_dims)
    reason = (
        f"Padded product ({pad_l}x{pad_w}x{pad_h} cm) exceeds internal cavity ({int_l}x{int_w}x{int_h} cm) "
        f"across all 6 orthogonal rotations."
    )
    if max_p > max_i:
        reason += f" Largest padded dimension ({max_p:.1f} cm) exceeds maximum internal dimension ({max_i:.1f} cm)."

    return FitEvaluationResult(
        is_feasible=False,
        rejection_reason=reason,
        successful_orientation=None,
        padded_product_dims=padded_dims,
        internal_package_dims=internal_dims,
        all_orientations_evaluated=all_evals,
    )


def check_courier_package_eligibility(
    outer_dims: Dimensions,
    gross_weight_kg: float,
    package_type: PackageType,
    couriers: Optional[List[Any]] = None,
) -> Tuple[Dict[str, str], str]:
    """
    Check if a package's outer dimensions and gross weight satisfy courier network constraints.
    Returns (dict of {courier_code: status_text}, overall_summary_status).

    If couriers do not have documented max dimension limits, reports:
    "Courier size eligibility not verified"
    """
    eval_list = couriers
    if eval_list is None:
        eval_list = list(OFFICIAL_DOCUMENTED_CAPABILITIES.values())

    if not eval_list:
        return {}, "Courier size eligibility not verified"

    eligibility: Dict[str, str] = {}
    verified_count = 0
    eligible_count = 0

    max_dim = max(outer_dims.length_cm, outer_dims.width_cm, outer_dims.height_cm)
    sorted_dims = sorted([outer_dims.length_cm, outer_dims.width_cm, outer_dims.height_cm], reverse=True)
    girth = sorted_dims[0] + 2.0 * (sorted_dims[1] + sorted_dims[2])

    for c in eval_list:
        code = getattr(c, "courier_code", str(c))
        max_l = getattr(c, "max_length_cm", None)
        max_g = getattr(c, "max_girth_cm", None)
        max_w = getattr(c, "max_weight_kg", None)
        if max_w is None:
            max_w = getattr(c, "max_actual_weight_kg", 100.0)
        allowed_types = getattr(c, "allowed_package_types", list(PackageType))
        rule_status = getattr(c, "rule_status", None)

        if (max_l is None and max_g is None) or (rule_status is not None and getattr(rule_status, "value", str(rule_status)) == "UNVERIFIED"):
            eligibility[code] = "Courier size eligibility not verified"
            continue

        verified_count += 1
        issues = []
        if max_l is not None and max_dim > max_l:
            issues.append(f"Length {max_dim:.1f}cm > max {max_l:.1f}cm")
        if max_g is not None and girth > max_g:
            issues.append(f"Girth {girth:.1f}cm > max {max_g:.1f}cm")
        if max_w is not None and gross_weight_kg > max_w:
            issues.append(f"Weight {gross_weight_kg:.1f}kg > max {max_w:.1f}kg")
        if package_type not in allowed_types:
            issues.append(f"Type '{package_type.value}' unsupported")

        if issues:
            eligibility[code] = f"Ineligible: {'; '.join(issues)}"
        else:
            eligibility[code] = "Eligible"
            eligible_count += 1

    if verified_count == 0:
        overall_summary = "Courier size eligibility not verified"
    elif eligible_count == 0:
        overall_summary = "Ineligible across verified couriers"
    elif eligible_count == verified_count:
        overall_summary = f"Eligible on all {verified_count} verified couriers"
    else:
        overall_summary = f"Eligible on {eligible_count}/{verified_count} verified couriers"

    return eligibility, overall_summary


def check_3d_fit(
    product_dims: Dimensions,
    package_internal_dims: Dimensions,
    padding_cm: float = 0.5,
) -> tuple[bool, Optional[str]]:
    """
    Check if a product fits inside internal package dimensions under any orthogonal 3D rotation.
    Sorts dimensions descending (bounding box invariant test).
    Maintained for backward compatibility.
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
    custom_packages: list[CustomPackaging] | None = None,
    couriers: list[Any] | None = None,
) -> list[PackagingComparisonResult]:
    """
    Compare current packaging against available demo packaging options and user-defined custom packaging.
    Returns ranked alternatives highlighting potential dead space reduction, orientation fit,
    courier size eligibility, and cost savings.
    """
    rule = volumetric_rule or VolumetricRule()
    catalogue = packaging_catalogue or DEMO_PACKAGING_CATALOGUE
    custom_pkgs = custom_packages or []

    # Current benchmark
    current_weight = compute_weight_breakdown(product_net_weight_kg, current_outer_dimensions, rule)
    current_vol = current_outer_dimensions.volume_cm3
    results: list[PackagingComparisonResult] = []

    # 1. Evaluate demo packaging catalogue
    for option in catalogue:
        wall = 0.5  # standard demo packaging nominal wall
        pad = 0.5   # standard demo packaging nominal padding
        fit_res = evaluate_orthogonal_fit(
            product_dims=product_dimensions,
            outer_dims=option.outer_dimensions,
            wall_thickness_cm=wall,
            padding_cm=pad,
        )
        new_vol = option.outer_dimensions.volume_cm3
        vol_diff = round(current_vol - new_vol, 2)
        vol_reduction_pct = round((vol_diff / current_vol) * 100.0, 1) if current_vol > 0 else 0.0
        new_gross_weight = round(product_net_weight_kg + option.tare_weight_kg, 3)

        courier_el, courier_summary = check_courier_package_eligibility(
            outer_dims=option.outer_dimensions,
            gross_weight_kg=new_gross_weight,
            package_type=option.package_type,
            couriers=couriers,
        )

        if fit_res.is_feasible:
            new_weight = compute_weight_breakdown(new_gross_weight, option.outer_dimensions, rule)
            weight_saved = max(0.0, round(current_weight.billing_slab_kg - new_weight.billing_slab_kg, 3))
            freight_savings = round(weight_saved * per_kg_freight_estimate_inr, 2)

            demo_reports = {
                cap.courier_code: evaluate_courier_dimension_eligibility(
                    outer_dims=option.outer_dimensions,
                    gross_weight_kg=new_gross_weight,
                    package_type=option.package_type,
                    capability=cap,
                )
                for cap in OFFICIAL_DOCUMENTED_CAPABILITIES.values()
            }
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
                    is_custom=False,
                    outer_dimensions=option.outer_dimensions,
                    internal_dimensions=option.internal_dimensions,
                    padded_product_dimensions=fit_res.padded_product_dims,
                    successful_orientation=fit_res.successful_orientation,
                    courier_eligibility=courier_el,
                    courier_status_summary=courier_summary,
                    courier_reports=demo_reports,
                    tare_weight_kg=option.tare_weight_kg,
                    new_gross_weight_kg=new_gross_weight,
                )
            )
        else:
            results.append(
                PackagingComparisonResult(
                    box_id=option.box_id,
                    box_name=option.box_name,
                    package_type=option.package_type,
                    is_feasible=False,
                    rejection_reason=fit_res.rejection_reason,
                    original_volume_cm3=current_vol,
                    new_volume_cm3=new_vol,
                    volume_difference_cm3=vol_diff,
                    volume_reduction_pct=vol_reduction_pct,
                    original_billable_slab_kg=current_weight.billing_slab_kg,
                    new_billable_slab_kg=current_weight.billing_slab_kg,
                    weight_saved_kg=0.0,
                    estimated_freight_savings_inr=0.0,
                    is_custom=False,
                    outer_dimensions=option.outer_dimensions,
                    internal_dimensions=option.internal_dimensions,
                    padded_product_dimensions=fit_res.padded_product_dims,
                    successful_orientation=None,
                    courier_eligibility=courier_el,
                    courier_status_summary=courier_summary,
                    tare_weight_kg=option.tare_weight_kg,
                    new_gross_weight_kg=new_gross_weight,
                )
            )

    # 2. Evaluate custom packages
    for c_pkg in custom_pkgs:
        fit_res = evaluate_orthogonal_fit(
            product_dims=product_dimensions,
            outer_dims=c_pkg.outer_dimensions,
            wall_thickness_cm=c_pkg.wall_thickness_cm,
            padding_cm=c_pkg.padding_cm,
        )
        new_vol = c_pkg.outer_dimensions.volume_cm3
        vol_diff = round(current_vol - new_vol, 2)
        vol_reduction_pct = round((vol_diff / current_vol) * 100.0, 1) if current_vol > 0 else 0.0
        new_gross_weight = round(product_net_weight_kg + c_pkg.tare_weight_kg, 3)

        courier_el, courier_summary = check_courier_package_eligibility(
            outer_dims=c_pkg.outer_dimensions,
            gross_weight_kg=new_gross_weight,
            package_type=c_pkg.package_type,
            couriers=couriers,
        )

        if fit_res.is_feasible:
            new_weight = compute_weight_breakdown(new_gross_weight, c_pkg.outer_dimensions, rule)
            weight_saved = max(0.0, round(current_weight.billing_slab_kg - new_weight.billing_slab_kg, 3))
            freight_savings = round(weight_saved * per_kg_freight_estimate_inr, 2)

            cust_reports = {
                cap.courier_code: evaluate_courier_dimension_eligibility(
                    outer_dims=c_pkg.outer_dimensions,
                    gross_weight_kg=new_gross_weight,
                    package_type=c_pkg.package_type,
                    capability=cap,
                )
                for cap in OFFICIAL_DOCUMENTED_CAPABILITIES.values()
            }
            results.append(
                PackagingComparisonResult(
                    box_id=c_pkg.packaging_id,
                    box_name=c_pkg.name,
                    package_type=c_pkg.package_type,
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
                    is_custom=True,
                    outer_dimensions=c_pkg.outer_dimensions,
                    internal_dimensions=c_pkg.internal_dimensions,
                    padded_product_dimensions=fit_res.padded_product_dims,
                    successful_orientation=fit_res.successful_orientation,
                    courier_eligibility=courier_el,
                    courier_status_summary=courier_summary,
                    courier_reports=cust_reports,
                    tare_weight_kg=c_pkg.tare_weight_kg,
                    new_gross_weight_kg=new_gross_weight,
                )
            )
        else:
            results.append(
                PackagingComparisonResult(
                    box_id=c_pkg.packaging_id,
                    box_name=c_pkg.name,
                    package_type=c_pkg.package_type,
                    is_feasible=False,
                    rejection_reason=fit_res.rejection_reason,
                    original_volume_cm3=current_vol,
                    new_volume_cm3=new_vol,
                    volume_difference_cm3=vol_diff,
                    volume_reduction_pct=vol_reduction_pct,
                    original_billable_slab_kg=current_weight.billing_slab_kg,
                    new_billable_slab_kg=current_weight.billing_slab_kg,
                    weight_saved_kg=0.0,
                    estimated_freight_savings_inr=0.0,
                    is_custom=True,
                    outer_dimensions=c_pkg.outer_dimensions,
                    internal_dimensions=c_pkg.internal_dimensions,
                    padded_product_dimensions=fit_res.padded_product_dims,
                    successful_orientation=None,
                    courier_eligibility=courier_el,
                    courier_status_summary=courier_summary,
                    tare_weight_kg=c_pkg.tare_weight_kg,
                    new_gross_weight_kg=new_gross_weight,
                )
            )

    # Sort feasible options by highest volume reduction first
    results.sort(key=lambda x: (not x.is_feasible, -x.estimated_freight_savings_inr, -x.volume_reduction_pct))
    return results
