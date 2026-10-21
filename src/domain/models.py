"""
Domain models and data schemas for the Adaptive Shipping Intelligence Agent.
Provides strongly-typed, validated Pydantic models for addresses, parcels,
orders, weight breakdowns, costs, quotes, and state tracking.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class PackageType(str, Enum):
    """Standardized package types."""
    BOX = "box"
    FLYER = "flyer"
    ENVELOPE = "envelope"
    WOODEN_CRATE = "wooden_crate"
    CUSTOM = "custom"
    POLYBAG = "polybag"
    TUBE = "tube"
    CUSTOM_CRATE = "custom_crate"


def parse_package_type(val: str | PackageType) -> PackageType:
    """Safely map user-facing packaging type string to PackageType enum."""
    if isinstance(val, PackageType):
        return val
    cleaned = str(val).strip().lower().replace(" ", "_").replace("-", "_")
    mapping = {
        "box": PackageType.BOX,
        "polybag": PackageType.POLYBAG,
        "flyer": PackageType.FLYER,
        "envelope": PackageType.ENVELOPE,
        "tube": PackageType.TUBE,
        "wooden_crate": PackageType.WOODEN_CRATE,
        "custom_crate": PackageType.CUSTOM_CRATE,
        "crate": PackageType.CUSTOM_CRATE,
        "custom": PackageType.CUSTOM,
    }
    return mapping.get(cleaned, PackageType.CUSTOM)


class ShipmentStatus(str, Enum):
    """
    Explicit lifecycle states for a shipment.
    Transitions are enforced by the Finite State Machine.
    """
    DRAFT = "DRAFT"
    QUOTED = "QUOTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    DISPATCHING = "DISPATCHING"
    CREATED = "CREATED"
    CREATION_UNCERTAIN = "CREATION_UNCERTAIN"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Address(BaseModel):
    """Physical address model with postal PIN code validation."""
    name: str = Field(..., min_length=2, max_length=100, description="Contact name or company")
    phone: str = Field(..., min_length=10, max_length=15, description="Contact phone number")
    street_line1: str = Field(..., min_length=3, max_length=150, description="Street address line 1")
    street_line2: Optional[str] = Field(default=None, max_length=150, description="Street address line 2")
    city: str = Field(..., min_length=2, max_length=50, description="City name")
    state: str = Field(..., min_length=2, max_length=50, description="State or province")
    pin_code: str = Field(..., description="6-digit postal PIN code")
    country: str = Field(default="India", max_length=50, description="Country name")

    @field_validator("pin_code")
    @classmethod
    def validate_pin_code(cls, v: str) -> str:
        cleaned = v.strip()
        if not re.fullmatch(r"^\d{6}$", cleaned):
            raise ValueError(f"PIN code must be exactly 6 digits, got '{cleaned}'")
        return cleaned

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        cleaned = re.sub(r"[\s\-\(\)\+]", "", v.strip())
        if not re.fullmatch(r"^\d{10,15}$", cleaned):
            raise ValueError(f"Phone number must contain between 10 and 15 digits, got '{v}'")
        return cleaned


class Dimensions(BaseModel):
    """Parcel outer dimensions in centimeters."""
    length_cm: float = Field(..., gt=0.0, le=500.0, description="Length in centimeters")
    width_cm: float = Field(..., gt=0.0, le=500.0, description="Width in centimeters")
    height_cm: float = Field(..., gt=0.0, le=500.0, description="Height in centimeters")

    @property
    def volume_cm3(self) -> float:
        """Calculate volumetric volume in cubic centimeters."""
        return round(self.length_cm * self.width_cm * self.height_cm, 3)

    @property
    def bounding_box_tuple(self) -> tuple[float, float, float]:
        """Dimensions sorted descending for rotational fit comparisons."""
        return tuple(sorted([self.length_cm, self.width_cm, self.height_cm], reverse=True))  # type: ignore


class OrderInput(BaseModel):
    """
    Complete order and parcel input collected from the user or e-commerce source.
    """
    order_reference: str = Field(..., min_length=3, max_length=64, description="Unique merchant order reference")
    sender: Address
    recipient: Address
    parcel_weight_kg: float = Field(..., gt=0.0, le=1000.0, description="Actual physical gross weight in kg")
    dimensions: Dimensions
    package_type: PackageType = Field(default=PackageType.BOX, description="Packaging format")
    is_cod: bool = Field(default=False, description="Whether shipment is Cash on Delivery")
    cod_amount: float = Field(default=0.0, ge=0.0, description="Cash amount to collect on delivery")
    declared_value: float = Field(default=0.0, ge=0.0, description="Declared goods value in INR")
    product_description: str = Field(default="General Merchandise", max_length=200)

    @model_validator(mode="after")
    def validate_cod_requirements(self) -> OrderInput:
        if self.is_cod and self.cod_amount <= 0.0:
            raise ValueError("COD amount must be greater than 0.0 when COD status is active.")
        if not self.is_cod and self.cod_amount > 0.0:
            raise ValueError("COD amount must be 0.0 for prepaid shipments.")
        return self


class VolumetricRule(BaseModel):
    """
    Rule specification for volumetric weight calculations.
    Note: 5000 is a configurable demonstration default; providers use varying divisors.
    """
    divisor: float = Field(default=5000.0, gt=0.0, description="Volumetric divisor (cm3/kg)")
    min_billable_weight_kg: float = Field(default=0.5, gt=0.0, description="Minimum billable weight in kg")
    billing_step_kg: float = Field(default=0.5, gt=0.0, description="Weight increment step in kg")


class WeightResult(BaseModel):
    """Detailed weight audit breakdown."""
    actual_weight_kg: float
    volumetric_weight_kg: float
    chargeable_weight_kg: float
    billing_slab_kg: float
    divisor_used: float
    is_volumetric_penalty: bool


class CostBreakdown(BaseModel):
    """Complete transparent landed cost calculation."""
    base_freight: float
    fuel_surcharge: float
    fuel_surcharge_rate_pct: float
    cod_fee: float
    handling_fee: float
    subtotal_before_tax: float
    tax_amount: float
    tax_rate_pct: float
    total_cost: float
    currency: str = "INR"


class CourierQuote(BaseModel):
    """
    Standardized courier quote returned by an adapter or documented API.
    Carries full audit traceability metadata, serviceability, and missing evidence flags.
    """
    quote_id: str = Field(..., description="Unique quote identifier")
    courier_code: str = Field(..., description="Courier identifier code")
    courier_name: str = Field(..., description="Courier brand name")
    service_type: str = Field(..., description="Service tier (e.g., Express Air, Surface Standard)")
    estimated_delivery_days: Optional[int] = Field(default=None, ge=1, description="Estimated SLA delivery transit days")
    is_serviceable: bool = Field(default=True, description="Serviceability between origin and destination")
    is_eligible: bool = Field(default=True, description="Eligible for booking (serviceable and constraints met)")
    unserviceable_reason: Optional[str] = Field(default=None, description="Detailed explanation if unserviceable")
    weight_result: Optional[WeightResult] = Field(default=None, description="Chargeable weight calculation")
    cost_breakdown: Optional[CostBreakdown] = Field(default=None, description="Transparent fee breakdown; None if unserviceable")
    retrieval_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of quote retrieval"
    )
    source_type: str = Field(default="DEMO_ADAPTER", description="DEMO_ADAPTER or VERIFIED_API")
    source_id: str = Field(default="demo-v1", description="Identifier of the quoting engine or API")
    input_hash: str = Field(..., description="Deterministic hash of the inputs used for this quote")
    decision_explanation: str = Field(default="", description="Clear explanation of cost & SLA factors")
    is_demo_data: bool = Field(default=True, description="Explicit flag: Demo/synthetic data disclosure")
    eligibility_status: str = Field(
        default="ELIGIBLE",
        description="Explicit eligibility: ELIGIBLE, INELIGIBLE, UNVERIFIED, STALE, PENDING"
    )
    evidence_source: Optional[str] = Field(default=None, description="Official published documentation title")
    evidence_url: Optional[str] = Field(default=None, description="Official documentation URL")
    eligibility_reasons: list[str] = Field(default_factory=list, description="Documented restriction rejection reasons")
    unverified_reasons: list[str] = Field(default_factory=list, description="Reasons for unverified status")
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="List of unverified or missing data points (e.g. unverified API endpoint)"
    )
    disclaimer: str = Field(
        default="DEMO DATA: Simulated quote for demonstration purposes only. Not a live carrier rate.",
        description="Prominent user disclosure"
    )
