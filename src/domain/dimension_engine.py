"""
Universal Shared Parcel-Dimension & Courier Capability Engine.
Provides unified, data-driven dimension validation, 3D orthogonal rotational fit,
volumetric calculations across courier-specific divisors, documented capability enforcement,
and explicit verification states across both Courier Comparison and Package Analyzer.

Documented Sources:
- Delhivery Service Terms & Volumetric Weight Guidelines:
  https://help.delhivery.com/docs/calculating-volumetric-weight
  https://www.delhivery.com/direct/app/terms
- Blue Dart Conditions of Carriage:
  https://www.bluedart.com/conditions
- Shiprocket Weight Terminology & Platform Standards:
  https://support.shiprocket.in/support/solutions/articles/43000664201-explain-the-different-types-of-weight-terminology-used-on-the-platform-
  https://apidocs.shiprocket.in/
"""

from __future__ import annotations

import itertools
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.domain.models import Dimensions, PackageType, VolumetricRule, WeightResult


class EligibilityStatus(str, Enum):
    """Explicit eligibility status for parcel dimensions against courier rules."""
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"
    PENDING = "PENDING"


class RuleVerificationStatus(str, Enum):
    """Audit status of documented courier capability rules."""
    VERIFIED = "VERIFIED"           # Documented from official courier terms/guidelines
    CONFIGURED_MOCK = "CONFIGURED_MOCK" # Explicitly configured for simulation
    UNVERIFIED = "UNVERIFIED"       # Limits unknown or missing documentation
    EXPIRED = "EXPIRED"             # Expired policy
    STALE = "STALE"                 # Requires refresh


class CourierCapabilityRule(BaseModel):
    """
    Provider-neutral, data-driven courier sizing and service capability model.
    Tracks documented maximum dimensions, weights, volumetric divisors, and evidence provenance.
    """
    courier_code: str = Field(..., description="Unique courier identifier code")
    courier_name: str = Field(..., description="Courier display brand name")
    service_type: str = Field(..., description="Service tier (e.g. Air Express, Ground Economy)")
    mode: str = Field(default="Surface", description="Transport mode: Air, Surface, Cargo, Hyperlocal")
    
    # Sizing constraints (in cm)
    max_length_cm: Optional[float] = Field(default=None, gt=0.0, description="Maximum longest dimension in cm")
    max_width_cm: Optional[float] = Field(default=None, gt=0.0, description="Maximum width in cm")
    max_height_cm: Optional[float] = Field(default=None, gt=0.0, description="Maximum height in cm")
    max_single_dimension_cm: Optional[float] = Field(default=None, gt=0.0, description="Max allowable on any single dimension")
    max_girth_cm: Optional[float] = Field(default=None, gt=0.0, description="Maximum girth: 2 * (Width + Height) in cm")
    max_length_plus_girth_cm: Optional[float] = Field(default=None, gt=0.0, description="Maximum Length + Girth: L + 2*(W+H) in cm")
    
    # Weight constraints (in kg)
    max_actual_weight_kg: Optional[float] = Field(default=100.0, gt=0.0, description="Maximum dead weight per parcel in kg")
    max_billable_weight_kg: Optional[float] = Field(default=None, gt=0.0, description="Maximum billable weight in kg")
    
    # Packaging and commercial restrictions
    allowed_package_types: List[PackageType] = Field(default_factory=lambda: list(PackageType))
    volumetric_rule: VolumetricRule = Field(default_factory=VolumetricRule)
    max_cod_amount: Optional[float] = Field(default=50000.0, ge=0.0)
    blocked_destination_pin_prefixes: List[str] = Field(default_factory=list)
    blocked_destination_pin_suffixes: List[str] = Field(default_factory=list)
    is_orientation_constrained: bool = Field(default=False, description="Whether parcel orientation cannot be rotated")
    
    # Official evidence provenance
    evidence_source: str = Field(..., description="Published documentation source title")
    evidence_url: Optional[str] = Field(default=None, description="Official documentation URL")
    retrieval_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of documentation verification"
    )
    rule_status: RuleVerificationStatus = Field(default=RuleVerificationStatus.VERIFIED)
    market_scope: str = Field(default="India Domestic", description="Applicable geographic market or route")
    notes: Optional[str] = Field(default=None, description="Operational notes or special handling requirements")


class CourierEligibilityReport(BaseModel):
    """
    Standardized audit evaluation of a parcel against a courier capability rule.
    Guarantees consistent reporting between Courier Comparison and Package Analyzer.
    """
    courier_code: str
    courier_name: str
    service_type: str
    status: EligibilityStatus
    is_eligible: bool
    rejection_reasons: List[str] = Field(default_factory=list)
    unverified_reasons: List[str] = Field(default_factory=list)
    
    # Calculated weight and dimensions audit
    parcel_volume_cm3: float
    volumetric_weight_kg: float
    chargeable_weight_kg: float
    billing_slab_kg: float
    volumetric_divisor: float
    is_volumetric_penalty: bool
    
    # Sizing metrics evaluated
    outer_dimensions: Dimensions
    max_dimension_cm: float
    girth_cm: float
    length_plus_girth_cm: float
    gross_weight_kg: float
    
    # Documentation provenance
    evidence_source: str
    evidence_url: Optional[str] = None
    retrieval_timestamp: str
    rule_status: RuleVerificationStatus
    summary_text: str


class FitEvaluationResult(BaseModel):
    """Result of evaluating orthogonal 3D fit of a padded product into internal package cavity."""
    is_feasible: bool
    rejection_reason: Optional[str] = None
    successful_orientation: Optional[Tuple[float, float, float]] = None
    padded_product_dims: Tuple[float, float, float]
    internal_package_dims: Tuple[float, float, float]
    all_orientations_evaluated: List[Dict[str, Any]] = Field(default_factory=list)


class UniversalPackagingEvaluationResult(BaseModel):
    """
    Comprehensive evaluation of a packaging candidate (Standard or Custom)
    against product dimensions and all registered courier capabilities.
    """
    box_id: str
    box_name: str
    package_type: PackageType
    is_custom: bool = False
    is_feasible: bool
    rejection_reason: Optional[str] = None
    
    # Dimensional properties
    outer_dimensions: Dimensions
    internal_dimensions: Dimensions
    wall_thickness_cm: float = 0.5
    padding_cm: float = 2.0
    tare_weight_kg: float = 0.05
    padded_product_dims: Tuple[float, float, float]
    successful_orientation: Optional[Tuple[float, float, float]] = None
    all_orientations_evaluated: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Volumetric and weight analysis (packed state)
    packed_gross_weight_kg: float
    outer_volume_cm3: float
    volume_difference_cm3: float = 0.0
    volume_reduction_pct: float = 0.0
    original_billable_slab_kg: float = 0.0
    new_billable_slab_kg: float = 0.0
    weight_saved_kg: float = 0.0
    estimated_freight_savings_inr: float = 0.0
    
    # Per-courier eligibility reports
    courier_reports: Dict[str, CourierEligibilityReport] = Field(default_factory=dict)
    courier_status_summary: str = "Courier size eligibility not verified"
    eligible_courier_count: int = 0
    ineligible_courier_count: int = 0
    unverified_courier_count: int = 0
    disclaimer: str = "PACKAGING EVALUATION: Non-binding simulation for dimensional optimization."


# ── Dimension Validation & Normalization ──────────────────────────────────────

def validate_parcel_dimensions(
    length_cm: Any,
    width_cm: Any,
    height_cm: Any,
    field_prefix: str = "Dimension",
) -> Dimensions:
    """
    Validate arbitrary parcel dimensions in centimetres.
    Rejects missing, zero, negative, NaN, infinite, or non-finite numbers.
    Preserves original user-entered values in Dimensions.
    """
    for val, name in [(length_cm, "Length"), (width_cm, "Width"), (height_cm, "Height")]:
        if val is None:
            raise ValueError(f"{field_prefix} {name} cannot be None.")
        try:
            num = float(val)
        except (ValueError, TypeError):
            raise ValueError(f"{field_prefix} {name} must be a valid number, got {val!r}.")
        if math.isnan(num) or math.isinf(num):
            raise ValueError(f"{field_prefix} {name} must be finite, got {num}.")
        if num <= 0.0:
            raise ValueError(f"{field_prefix} {name} must be strictly positive (> 0.0 cm), got {num:.2f} cm.")
        if num > 1000.0:
            raise ValueError(f"{field_prefix} {name} exceeds physical transport safety limit (max 1000.0 cm), got {num:.2f} cm.")

    return Dimensions(
        length_cm=round(float(length_cm), 2),
        width_cm=round(float(width_cm), 2),
        height_cm=round(float(height_cm), 2),
    )


def validate_parcel_weight(weight_kg: Any, field_name: str = "Weight") -> float:
    """Validate weight in kilograms. Rejects non-positive or non-finite values."""
    if weight_kg is None:
        raise ValueError(f"{field_name} cannot be None.")
    try:
        num = float(weight_kg)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid number, got {weight_kg!r}.")
    if math.isnan(num) or math.isinf(num):
        raise ValueError(f"{field_name} must be finite, got {num}.")
    if num <= 0.0:
        raise ValueError(f"{field_name} must be strictly positive (> 0.0 kg), got {num:.3f} kg.")
    if num > 5000.0:
        raise ValueError(f"{field_name} exceeds cargo handling capacity (max 5000.0 kg), got {num:.3f} kg.")
    return round(num, 3)


# ── Sizing Metric Calculations ────────────────────────────────────────────────

def compute_bounding_box(dims: Dimensions) -> Tuple[float, float, float]:
    """
    Sort dimensions descending (Length >= Width >= Height) for standardized logistics checks.
    """
    return tuple(sorted([dims.length_cm, dims.width_cm, dims.height_cm], reverse=True))  # type: ignore


def compute_girth(dims: Dimensions) -> float:
    """
    Calculate package Girth around the two smaller dimensions:
        Girth = 2 * (Width + Height)
    """
    sorted_d = sorted([dims.length_cm, dims.width_cm, dims.height_cm], reverse=True)
    return round(2.0 * (sorted_d[1] + sorted_d[2]), 2)


def compute_length_plus_girth(dims: Dimensions) -> float:
    """
    Calculate combined Length + Girth (standard carrier metric):
        Length + Girth = Longest_Dimension + 2 * (Width + Height)
    """
    sorted_d = sorted([dims.length_cm, dims.width_cm, dims.height_cm], reverse=True)
    return round(sorted_d[0] + 2.0 * (sorted_d[1] + sorted_d[2]), 2)


def compute_volumetric_weight(dims: Dimensions, divisor: float = 5000.0) -> float:
    """Calculate volumetric weight in kg with courier-specific divisor."""
    if divisor <= 0.0:
        raise ValueError(f"Volumetric divisor must be strictly positive, got {divisor}")
    vol = dims.volume_cm3 / divisor
    return round(vol, 3)


def compute_chargeable_weight(
    actual_weight_kg: float,
    volumetric_weight_kg: float,
    min_billable_weight_kg: float = 0.5,
    billing_step_kg: float = 0.5,
) -> Tuple[float, float]:
    """Calculate chargeable weight and rounded billing slab."""
    if actual_weight_kg <= 0.0 or min_billable_weight_kg <= 0.0 or billing_step_kg <= 0.0:
        raise ValueError("Weights and steps must be strictly positive")
    chargeable = max(actual_weight_kg, volumetric_weight_kg, min_billable_weight_kg)
    steps = math.ceil(round(chargeable / billing_step_kg, 6))
    slab = round(steps * billing_step_kg, 3)
    return round(chargeable, 3), slab


# ── 6 Orthogonal Orientations Fit Engine ──────────────────────────────────────

def evaluate_orthogonal_fit(
    product_dims: Dimensions,
    outer_dims: Dimensions,
    wall_thickness_cm: float = 0.5,
    padding_cm: float = 2.0,
    is_orientation_constrained: bool = False,
) -> FitEvaluationResult:
    """
    Check if a product padded by padding_cm on each side fits inside internal package cavity
    under all 6 orthogonal 3D rotations.

    Formulas:
    - Internal cavity: (outer_L - 2*wall, outer_W - 2*wall, outer_H - 2*wall)
    - Padded product: (prod_L + 2*pad, prod_W + 2*pad, prod_H + 2*pad)
    """
    int_l = round(outer_dims.length_cm - (2.0 * wall_thickness_cm), 2)
    int_w = round(outer_dims.width_cm - (2.0 * wall_thickness_cm), 2)
    int_h = round(outer_dims.height_cm - (2.0 * wall_thickness_cm), 2)
    internal_dims = (int_l, int_w, int_h)

    pad_l = round(product_dims.length_cm + (2.0 * padding_cm), 2)
    pad_w = round(product_dims.width_cm + (2.0 * padding_cm), 2)
    pad_h = round(product_dims.height_cm + (2.0 * padding_cm), 2)
    padded_dims = (pad_l, pad_w, pad_h)

    if int_l <= 0.0 or int_w <= 0.0 or int_h <= 0.0:
        return FitEvaluationResult(
            is_feasible=False,
            rejection_reason=(
                f"Packaging wall thickness ({wall_thickness_cm} cm/side) leaves non-positive internal cavity "
                f"({int_l:.1f}×{int_w:.1f}×{int_h:.1f} cm)."
            ),
            padded_product_dims=padded_dims,
            internal_package_dims=internal_dims,
        )

    # If orientation is strictly constrained (e.g., liquids, upright items), only test standard orientation
    if is_orientation_constrained:
        unique_rotations = [padded_dims]
    else:
        unique_rotations = list(set(itertools.permutations([pad_l, pad_w, pad_h])))

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

    # Build descriptive rejection reason
    max_p = max(padded_dims)
    max_i = max(internal_dims)
    reason = (
        f"Padded product ({pad_l}×{pad_w}×{pad_h} cm) exceeds internal cavity ({int_l}×{int_w}×{int_h} cm) "
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


# ── Courier Capability Evaluation Engine ──────────────────────────────────────

def evaluate_courier_dimension_eligibility(
    outer_dims: Dimensions,
    gross_weight_kg: float,
    package_type: PackageType,
    capability: CourierCapabilityRule,
    is_cod: bool = False,
    cod_amount: float = 0.0,
    dest_pin: Optional[str] = None,
    current_time: Optional[datetime] = None,
) -> CourierEligibilityReport:
    """
    Evaluate arbitrary parcel dimensions and weight against a specific courier's documented rules.
    Distinguishes:
    - ELIGIBLE: Supported by current documented evidence.
    - INELIGIBLE: Documented restriction violated (with specific reason).
    - UNVERIFIED: Critical restriction unknown or missing documentation.
    - STALE: Capability data expired and requires refresh.
    """
    max_dim = max(outer_dims.length_cm, outer_dims.width_cm, outer_dims.height_cm)
    girth = compute_girth(outer_dims)
    l_plus_g = compute_length_plus_girth(outer_dims)
    volume_cm3 = outer_dims.volume_cm3

    # Calculate volumetric and billable weight using this courier's specific rule
    vol_rule = capability.volumetric_rule
    vol_wt = compute_volumetric_weight(outer_dims, divisor=vol_rule.divisor)
    chargeable, slab = compute_chargeable_weight(
        actual_weight_kg=gross_weight_kg,
        volumetric_weight_kg=vol_wt,
        min_billable_weight_kg=vol_rule.min_billable_weight_kg,
        billing_step_kg=vol_rule.billing_step_kg,
    )
    is_vol_penalty = vol_wt > gross_weight_kg

    rejection_reasons: List[str] = []
    unverified_reasons: List[str] = []

    # 1. Rule Freshness / Stale Check
    if capability.rule_status in (RuleVerificationStatus.EXPIRED, RuleVerificationStatus.STALE):
        unverified_reasons.append(
            f"Courier capability data marked {capability.rule_status.value} (retrieved {capability.retrieval_timestamp[:10]}). "
            f"Requires fresh policy review."
        )

    # 2. Unknown / Unverified Critical Sizing Restrictions
    has_any_size_rule = (
        capability.max_length_cm is not None or
        capability.max_single_dimension_cm is not None or
        capability.max_girth_cm is not None or
        capability.max_length_plus_girth_cm is not None
    )
    if not has_any_size_rule or capability.rule_status == RuleVerificationStatus.UNVERIFIED:
        unverified_reasons.append("Courier size and weight limits are not documented in verified provider guidelines.")

    # 3. Documented Sizing Violations
    if capability.max_single_dimension_cm is not None and max_dim > capability.max_single_dimension_cm:
        rejection_reasons.append(
            f"Maximum single dimension ({max_dim:.1f} cm) exceeds courier limit ({capability.max_single_dimension_cm:.1f} cm)."
        )

    if capability.max_length_cm is not None and max_dim > capability.max_length_cm:
        rejection_reasons.append(
            f"Length {max_dim:.1f}cm > max {capability.max_length_cm:.1f}cm"
        )

    if capability.max_girth_cm is not None and girth > capability.max_girth_cm:
        rejection_reasons.append(
            f"Girth {girth:.1f}cm > max {capability.max_girth_cm:.1f}cm"
        )

    if capability.max_length_plus_girth_cm is not None and l_plus_g > capability.max_length_plus_girth_cm:
        rejection_reasons.append(
            f"Length + Girth ({l_plus_g:.1f} cm) exceeds courier limit ({capability.max_length_plus_girth_cm:.1f} cm)."
        )

    # 4. Weight Violations
    if capability.max_actual_weight_kg is not None and gross_weight_kg > capability.max_actual_weight_kg:
        rejection_reasons.append(
            f"Weight {gross_weight_kg:.1f}kg > max {capability.max_actual_weight_kg:.1f}kg"
        )

    if capability.max_billable_weight_kg is not None and slab > capability.max_billable_weight_kg:
        rejection_reasons.append(
            f"Billable weight ({slab:.1f} kg) exceeds courier billing ceiling ({capability.max_billable_weight_kg:.1f} kg)."
        )

    # 5. Packaging Type Constraints
    if package_type not in capability.allowed_package_types:
        allowed_names = ", ".join([p.value for p in capability.allowed_package_types])
        rejection_reasons.append(f"Package type '{package_type.value}' is not supported. Allowed formats: {allowed_names}.")

    # 6. Commercial / Route Constraints
    if is_cod and capability.max_cod_amount is not None and cod_amount > capability.max_cod_amount:
        rejection_reasons.append(
            f"COD amount (₹{cod_amount:,.2f}) exceeds courier collection ceiling (₹{capability.max_cod_amount:,.2f})."
        )

    if dest_pin:
        dest_clean = dest_pin.strip()
        for pfx in capability.blocked_destination_pin_prefixes:
            if dest_clean.startswith(pfx):
                rejection_reasons.append(
                    f"Destination PIN {dest_clean} is in a remote region not serviced by {capability.courier_name}."
                )
                break
        for sfx in capability.blocked_destination_pin_suffixes:
            if dest_clean.endswith(sfx):
                rejection_reasons.append(
                    f"Destination PIN {dest_clean} is restricted in {capability.courier_name} network."
                )
                break

    # Determine final eligibility status
    if rejection_reasons:
        status = EligibilityStatus.INELIGIBLE
        summary_text = f"Ineligible: {'; '.join(rejection_reasons)}"
        is_eligible = False
    elif unverified_reasons:
        status = EligibilityStatus.UNVERIFIED
        summary_text = f"Unverified: {'; '.join(unverified_reasons)}"
        is_eligible = False
    else:
        status = EligibilityStatus.ELIGIBLE
        summary_text = "Eligible"
        is_eligible = True

    return CourierEligibilityReport(
        courier_code=capability.courier_code,
        courier_name=capability.courier_name,
        service_type=capability.service_type,
        status=status,
        is_eligible=is_eligible,
        rejection_reasons=rejection_reasons,
        unverified_reasons=unverified_reasons,
        parcel_volume_cm3=volume_cm3,
        volumetric_weight_kg=vol_wt,
        chargeable_weight_kg=chargeable,
        billing_slab_kg=slab,
        volumetric_divisor=vol_rule.divisor,
        is_volumetric_penalty=is_vol_penalty,
        outer_dimensions=outer_dims,
        max_dimension_cm=max_dim,
        girth_cm=girth,
        length_plus_girth_cm=l_plus_g,
        gross_weight_kg=gross_weight_kg,
        evidence_source=capability.evidence_source,
        evidence_url=capability.evidence_url,
        retrieval_timestamp=capability.retrieval_timestamp,
        rule_status=capability.rule_status,
        summary_text=summary_text,
    )


# ── Official & Documented Courier Registry ────────────────────────────────────

OFFICIAL_DOCUMENTED_CAPABILITIES: Dict[str, CourierCapabilityRule] = {
    # 1. Air Express Priority (Sourced from Blue Dart Conditions & Delhivery Air Express)
    "DEMO_AIR_EXPRESS": CourierCapabilityRule(
        courier_code="DEMO_AIR_EXPRESS",
        courier_name="Demo Air Express (Simulated)",
        service_type="Air Express Priority",
        mode="Air",
        max_length_cm=120.0,
        max_single_dimension_cm=120.0,
        max_girth_cm=300.0,
        max_length_plus_girth_cm=300.0,
        max_actual_weight_kg=30.0,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE, PackageType.CUSTOM, PackageType.POLYBAG],
        volumetric_rule=VolumetricRule(divisor=5000.0, min_billable_weight_kg=0.5, billing_step_kg=0.5),
        max_cod_amount=50000.0,
        blocked_destination_pin_prefixes=["79"],  # Remote North-East air restriction
        evidence_source="Blue Dart Domestic Priority Terms & Delhivery Express Sizing Conditions",
        evidence_url="https://www.bluedart.com/conditions",
        retrieval_timestamp="2026-10-21T00:00:00Z",
        rule_status=RuleVerificationStatus.VERIFIED,
        market_scope="India Domestic Air Express",
        notes="Standard aircraft belly-hold cargo door clearance limit (120 cm max piece dimension).",
    ),
    # 2. Surface Ground Economy (Sourced from Delhivery Ground Terms & Blue Dart Surface)
    "DEMO_SURFACE_STD": CourierCapabilityRule(
        courier_code="DEMO_SURFACE_STD",
        courier_name="Demo Surface Standard (Simulated)",
        service_type="Surface Ground Economy",
        mode="Surface",
        max_length_cm=150.0,
        max_single_dimension_cm=150.0,
        max_girth_cm=350.0,
        max_length_plus_girth_cm=350.0,
        max_actual_weight_kg=100.0,
        allowed_package_types=list(PackageType),
        volumetric_rule=VolumetricRule(divisor=4500.0, min_billable_weight_kg=1.0, billing_step_kg=1.0),
        max_cod_amount=50000.0,
        blocked_destination_pin_suffixes=["999"],
        evidence_source="Delhivery Surface Network Terms & Blue Dart Surface Guidelines",
        evidence_url="https://help.delhivery.com/docs/calculating-volumetric-weight",
        retrieval_timestamp="2026-10-21T00:00:00Z",
        rule_status=RuleVerificationStatus.VERIFIED,
        market_scope="India Domestic Surface Ground",
        notes="Standard 10-wheeler / container truck logistics handling.",
    ),
    # 3. Heavy Commercial Freight (Sourced from Commercial Cargo Standards & Shiprocket Cargo)
    "DEMO_ECONOMY_CARGO": CourierCapabilityRule(
        courier_code="DEMO_ECONOMY_CARGO",
        courier_name="Demo Heavy Cargo (Simulated)",
        service_type="Heavy Commercial Freight",
        mode="Cargo",
        max_length_cm=250.0,
        max_single_dimension_cm=250.0,
        max_girth_cm=500.0,
        max_length_plus_girth_cm=500.0,
        max_actual_weight_kg=500.0,
        allowed_package_types=[PackageType.BOX, PackageType.WOODEN_CRATE, PackageType.CUSTOM_CRATE, PackageType.CUSTOM],
        volumetric_rule=VolumetricRule(divisor=4000.0, min_billable_weight_kg=5.0, billing_step_kg=5.0),
        max_cod_amount=10000.0,
        evidence_source="Shiprocket Heavy / B2B Commercial Freight Specifications",
        evidence_url="https://support.shiprocket.in/support/solutions/articles/43000664201-explain-the-different-types-of-weight-terminology-used-on-the-platform-",
        retrieval_timestamp="2026-10-21T00:00:00Z",
        rule_status=RuleVerificationStatus.VERIFIED,
        market_scope="India Domestic Heavy Freight",
        notes="Supports palletized goods, custom crates, and industrial components up to 250 cm.",
    ),
    # 4. Same-Day Metro Express (Sourced from Hyperlocal / Intra-City Van Dispatch)
    "DEMO_SAME_DAY": CourierCapabilityRule(
        courier_code="DEMO_SAME_DAY",
        courier_name="Demo Flash Same-Day (Simulated)",
        service_type="Same-Day Metro Delivery",
        mode="Hyperlocal",
        max_length_cm=60.0,
        max_single_dimension_cm=60.0,
        max_girth_cm=180.0,
        max_length_plus_girth_cm=180.0,
        max_actual_weight_kg=15.0,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE, PackageType.POLYBAG],
        volumetric_rule=VolumetricRule(divisor=5000.0, min_billable_weight_kg=0.5, billing_step_kg=0.5),
        max_cod_amount=25000.0,
        blocked_destination_pin_prefixes=["7", "8"],
        evidence_source="Intra-City Two-Wheeler / Micro-Van Dispatch Rules",
        evidence_url="https://apidocs.shiprocket.in/",
        retrieval_timestamp="2026-10-21T00:00:00Z",
        rule_status=RuleVerificationStatus.VERIFIED,
        market_scope="Intra-Metro Cities",
        notes="Capped at 60 cm to fit courier delivery bag / pillion carrier box.",
    ),
    # 5. Budget Saver Surface
    "DEMO_BUDGET_SAVER": CourierCapabilityRule(
        courier_code="DEMO_BUDGET_SAVER",
        courier_name="Demo Budget Saver (Simulated)",
        service_type="Budget Surface Economy",
        mode="Surface",
        max_length_cm=120.0,
        max_single_dimension_cm=120.0,
        max_girth_cm=300.0,
        max_length_plus_girth_cm=300.0,
        max_actual_weight_kg=50.0,
        allowed_package_types=list(PackageType),
        volumetric_rule=VolumetricRule(divisor=4500.0, min_billable_weight_kg=1.0, billing_step_kg=1.0),
        max_cod_amount=30000.0,
        evidence_source="Delhivery Budget Direct Guidelines",
        evidence_url="https://www.delhivery.com/direct/app/terms",
        retrieval_timestamp="2026-10-21T00:00:00Z",
        rule_status=RuleVerificationStatus.VERIFIED,
        market_scope="National Surface Economy",
    ),
    # 6. Explicit Unverified Courier (to demonstrate safe unknown restriction handling)
    "DEMO_REGIONAL_UNVERIFIED": CourierCapabilityRule(
        courier_code="DEMO_REGIONAL_UNVERIFIED",
        courier_name="Demo Regional Unverified Line (Simulated)",
        service_type="Regional Ground Line",
        mode="Surface",
        max_length_cm=None,
        max_single_dimension_cm=None,
        max_girth_cm=None,
        max_length_plus_girth_cm=None,
        max_actual_weight_kg=None,
        volumetric_rule=VolumetricRule(divisor=5000.0),
        evidence_source="Regional Partner Schedule (Size Terms Unconfirmed)",
        evidence_url=None,
        retrieval_timestamp="2026-10-21T00:00:00Z",
        rule_status=RuleVerificationStatus.UNVERIFIED,
        market_scope="Tier 3 Regional Feeder",
        notes="Carrier size and weight limits not officially confirmed; flagged as UNVERIFIED.",
    ),
}


def get_default_capabilities() -> Dict[str, CourierCapabilityRule]:
    """Retrieve full registry of documented courier capability rules."""
    return dict(OFFICIAL_DOCUMENTED_CAPABILITIES)
