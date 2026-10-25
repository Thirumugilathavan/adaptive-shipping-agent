"""
Domain models and data schemas for the Adaptive Shipping Intelligence Agent.
Provides strongly-typed, validated Pydantic models for addresses, parcels,
orders, weight breakdowns, costs, quotes, and state tracking.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Optional
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
    country: str = Field(default="India", max_length=50, description="Country name")
    pin_code: str = Field(..., description="Postal PIN or ZIP code")

    @field_validator("pin_code")
    @classmethod
    def validate_pin_code(cls, v: str, info) -> str:
        cleaned = v.strip()
        country = (info.data.get("country") if hasattr(info, "data") else "India") or "India"
        c = str(country).strip().upper()
        if c in ("INDIA", "IN"):
            from src.domain.pincode_database import validate_indian_pin_format, lookup_verified_pin
            is_fmt, err_fmt = validate_indian_pin_format(cleaned)
            if not is_fmt:
                raise ValueError(err_fmt)
            is_found, _, err_found = lookup_verified_pin(cleaned)
            if not is_found:
                raise ValueError(err_found)
        elif c in ("US", "USA", "UNITED STATES"):
            if not re.fullmatch(r"^\d{5}$", cleaned):
                raise ValueError(f"US ZIP code must be 5 digits (e.g. 78756), got '{cleaned}'")
        else:
            if not re.fullmatch(r"^[A-Za-z0-9\s\-]{3,10}$", cleaned):
                raise ValueError(f"Postal code must be valid, got '{cleaned}'")
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

    @field_validator("length_cm", "width_cm", "height_cm", mode="before")
    @classmethod
    def validate_dimension_scalar(cls, v: Any, info) -> float:
        field_label = info.field_name.replace("_cm", "")
        if v is None:
            raise ValueError(f"Please enter the parcel {field_label}.")
        try:
            val = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"Parcel {field_label} must be a valid number.")
        if math.isnan(val) or math.isinf(val):
            raise ValueError(f"Parcel {field_label} must be a finite number.")
        if val <= 0.0:
            raise ValueError(f"Parcel {field_label} must be greater than zero (greater than 0).")
        if val > 500.0:
            raise ValueError(f"Parcel {field_label} exceeds allowed maximum (500.0 cm).")
        return val

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

    @field_validator("parcel_weight_kg", mode="before")
    @classmethod
    def validate_weight_scalar(cls, v: Any) -> float:
        if v is None:
            raise ValueError("Please enter the actual parcel weight.")
        try:
            val = float(v)
        except (TypeError, ValueError):
            raise ValueError("Actual parcel weight must be a valid number.")
        if math.isnan(val) or math.isinf(val):
            raise ValueError("Actual parcel weight must be a finite number.")
        if val <= 0.0:
            raise ValueError("Actual parcel weight must be greater than zero (greater than 0).")
        if val > 1000.0:
            raise ValueError("Actual parcel weight exceeds allowed maximum (1000.0 kg).")
        return val
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


class FieldProvenanceRecord(BaseModel):
    """
    Field-level provenance record stored beside an extracted value.
    Ensures complete traceability from aggregator sandbox/mock/dashboard to recommendation.
    """
    field_name: str = Field(..., description="Name of the extracted field, e.g. total_cost, base_freight, estimated_delivery_days")
    extracted_value: Any = Field(..., description="Extracted domain value")
    raw_value: Optional[Any] = Field(default=None, description="Raw value as represented in the source payload or input")
    source_type: str = Field(default="LOCAL_AGGREGATOR_MOCK", description="Source classification: LOCAL_AGGREGATOR_MOCK, MANUAL_DASHBOARD_ENTRY, LOCAL_DEMO, AGGREGATOR_SANDBOX")
    source_identifier: str = Field(..., description="Unique identifier of the source engine, operator dashboard entry, or mock instance")
    source_url_or_endpoint: Optional[str] = Field(default=None, description="Source URL or mock endpoint, e.g. mock://courier-aggregator/v1/rates")
    retrieval_timestamp: str = Field(..., description="ISO 8601 UTC timestamp of extraction")
    retrieval_method: str = Field(default="MOCK_AGGREGATOR_INTERFACE", description="Method: MOCK_AGGREGATOR_INTERFACE, MANUAL_DASHBOARD_TRANSCRIPTION, DASHBOARD_EXPORT_PARSER, DEMO_DETERMINISTIC_ENGINE")
    quote_id: str = Field(..., description="Associated quote ID")
    request_or_shipment_id: Optional[str] = Field(default=None, description="Associated order reference or request ID")
    raw_response_snapshot: Optional[dict[str, Any]] = Field(default=None, description="Sanitized payload snapshot scrubbed of sensitive data")
    validation_status: str = Field(default="VALIDATED", description="VALIDATED, MANUAL_UNVERIFIED, MOCK_VERIFIED, MISSING_PROVENANCE, STALE")
    transformation_applied: Optional[str] = Field(default=None, description="Data transformation applied, e.g. float_inr_rounding, iso_days_int")


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
    source_type: str = Field(default="DEMO_ADAPTER", description="DEMO_ADAPTER, LOCAL_AGGREGATOR_MOCK, MANUAL_SANDBOX, etc.")
    source_id: str = Field(default="demo-v1", description="Identifier of the quoting engine or API")
    retrieval_method: str = Field(
        default="DEMO_DETERMINISTIC_ENGINE",
        description="Mechanism used: MOCK_AGGREGATOR_INTERFACE, MANUAL_DASHBOARD_TRANSCRIPTION, DASHBOARD_EXPORT_PARSER, DEMO_DETERMINISTIC_ENGINE"
    )
    raw_response_snapshot: Optional[dict[str, Any]] = Field(
        default=None,
        description="Sanitized, secret-scrubbed raw payload snapshot from aggregator sandbox/mock or manual dashboard form"
    )
    field_provenance: dict[str, FieldProvenanceRecord] = Field(
        default_factory=dict,
        description="Field-level provenance mapping: field_name -> FieldProvenanceRecord"
    )
    input_hash: str = Field(..., description="Deterministic hash of the inputs used for this quote")
    decision_explanation: str = Field(default="", description="Clear explanation of cost & SLA factors")
    is_demo_data: bool = Field(default=True, description="Explicit flag: Demo/synthetic data disclosure")
    eligibility_status: str = Field(
        default="ELIGIBLE",
        description="Explicit eligibility: ELIGIBLE, INELIGIBLE, UNVERIFIED, STALE, PENDING"
    )
    evidence_source: Optional[str] = Field(default=None, description="Official published documentation title")
    evidence_url: Optional[str] = Field(default=None, description="Official documentation URL or mock endpoint")
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

    def record_field_provenance(
        self,
        field_name: str,
        extracted_value: Any,
        raw_value: Optional[Any] = None,
        source_type: Optional[str] = None,
        source_identifier: Optional[str] = None,
        source_url_or_endpoint: Optional[str] = None,
        retrieval_timestamp: Optional[str] = None,
        retrieval_method: Optional[str] = None,
        request_or_shipment_id: Optional[str] = None,
        raw_response_snapshot: Optional[dict[str, Any]] = None,
        validation_status: Optional[str] = None,
        transformation_applied: Optional[str] = None,
    ) -> FieldProvenanceRecord:
        record = FieldProvenanceRecord(
            field_name=field_name,
            extracted_value=extracted_value,
            raw_value=raw_value if raw_value is not None else extracted_value,
            source_type=source_type or self.source_type,
            source_identifier=source_identifier or self.source_id,
            source_url_or_endpoint=source_url_or_endpoint or self.evidence_url,
            retrieval_timestamp=retrieval_timestamp or self.retrieval_timestamp,
            retrieval_method=retrieval_method or getattr(self, "retrieval_method", "MOCK_AGGREGATOR_INTERFACE"),
            quote_id=self.quote_id,
            request_or_shipment_id=request_or_shipment_id,
            raw_response_snapshot=raw_response_snapshot or self.raw_response_snapshot,
            validation_status=validation_status or ("MOCK_VERIFIED" if self.is_demo_data else "VALIDATED"),
            transformation_applied=transformation_applied,
        )
        self.field_provenance[field_name] = record
        return record

    def populate_standard_field_provenance(self, order_reference: Optional[str] = None) -> None:
        """Populate field-level provenance for all core extracted fields on this quote."""
        ts = self.retrieval_timestamp
        st = self.source_type
        sid = self.source_id
        url = self.evidence_url
        method = getattr(self, "retrieval_method", "MOCK_AGGREGATOR_INTERFACE")
        val_status = "MANUAL_UNVERIFIED" if st == "MANUAL_SANDBOX" else ("MOCK_VERIFIED" if "MOCK" in st or "SANDBOX" in st else "DEMO_VERIFIED")

        if self.cost_breakdown:
            self.record_field_provenance("total_cost", self.cost_breakdown.total_cost, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="landed_cost_aggregate_inr")
            self.record_field_provenance("base_freight", self.cost_breakdown.base_freight, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="weight_slab_pricing_inr")
            self.record_field_provenance("fuel_surcharge", self.cost_breakdown.fuel_surcharge, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="percentage_surcharge_inr")
            self.record_field_provenance("tax_amount", self.cost_breakdown.tax_amount, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="gst_statutory_tax_inr")
            self.record_field_provenance("cod_fee", self.cost_breakdown.cod_fee, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="cod_handling_surcharge_inr")

        if self.estimated_delivery_days is not None:
            self.record_field_provenance("estimated_delivery_days", self.estimated_delivery_days, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="sla_business_days_int")

        self.record_field_provenance("is_serviceable", self.is_serviceable, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="pincode_route_serviceability_check")

        if self.weight_result:
            self.record_field_provenance("billing_slab_kg", self.weight_result.billing_slab_kg, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="volumetric_vs_dead_chargeable_slab")

        self.record_field_provenance("courier_name", self.courier_name, source_type=st, source_identifier=sid, source_url_or_endpoint=url, retrieval_timestamp=ts, retrieval_method=method, request_or_shipment_id=order_reference, validation_status=val_status, transformation_applied="courier_brand_mapping")
