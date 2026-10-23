"""
Feature Schemas and Input Validation for Shipment Price Prediction Models.
Supports both the adapted Indian Domestic Courier model and the reference Sculpture model.
Enforces strict input validation, preventing silent defaults or invalid inputs.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


FEATURE_SCHEMA_VERSION = "1.0.0"


class PackageTypeEnum(str, Enum):
    BOX = "box"
    FLYER = "flyer"
    WOODEN_CRATE = "wooden_crate"
    CUSTOM = "custom"


class PredictionModelType(str, Enum):
    DOMESTIC_COURIER = "domestic_courier"
    REFERENCE_SCULPTURE = "reference_sculpture"


class PredictionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    INVALID_INPUT = "INVALID_INPUT"
    ERROR = "ERROR"


# -----------------------------------------------------------------------------
# Domestic Indian Courier Features
# -----------------------------------------------------------------------------

class DomesticCourierFeatures(BaseModel):
    """
    Features required by the Domestic Indian Courier Price Predictor.
    Matches Indian logistics routing, dimensional weight rules, and courier types.
    """
    origin_pin: str = Field(..., description="6-digit Indian origin postal PIN code")
    destination_pin: str = Field(..., description="6-digit Indian destination postal PIN code")
    length_cm: float = Field(..., gt=0.0, le=500.0, description="Parcel outer length in cm")
    width_cm: float = Field(..., gt=0.0, le=500.0, description="Parcel outer width in cm")
    height_cm: float = Field(..., gt=0.0, le=500.0, description="Parcel outer height in cm")
    parcel_weight_kg: float = Field(..., gt=0.0, le=1000.0, description="Actual scale weight in kg")
    package_type: str = Field(default="box", description="Packaging type: box, flyer, wooden_crate, custom")
    courier_code: str = Field(default="DEMO_AIR_EXPRESS", description="Courier carrier code")
    is_cod: bool = Field(default=False, description="Cash on Delivery flag")
    cod_amount: float = Field(default=0.0, ge=0.0, description="COD collection amount in INR")

    @field_validator("origin_pin", "destination_pin")
    @classmethod
    def validate_pin(cls, v: str, info) -> str:
        cleaned = str(v).strip()
        if not re.fullmatch(r"^[1-9]\d{5}$", cleaned):
            raise ValueError(f"{info.field_name} must be a valid 6-digit Indian postal PIN code (got '{v}')")
        return cleaned

    @field_validator("package_type")
    @classmethod
    def normalize_package_type(cls, v: str) -> str:
        cleaned = str(v).strip().lower()
        valid = {"box", "flyer", "wooden_crate", "custom", "crate"}
        if cleaned not in valid:
            raise ValueError(f"Unsupported package_type '{v}'. Must be one of: {valid}")
        return "wooden_crate" if cleaned == "crate" else cleaned

    def compute_volumetric_weight(self, divisor: float = 5000.0) -> float:
        """Standard courier volumetric weight in kg."""
        return (self.length_cm * self.width_cm * self.height_cm) / divisor

    def to_model_dataframe_row(self) -> Dict[str, Any]:
        """Convert validated domain input to exact feature dictionary expected by model pipeline."""
        vol_weight = self.compute_volumetric_weight()
        metro_prefixes = {"11", "40", "56", "60", "70", "50"}
        o_metro = self.origin_pin[:2] in metro_prefixes
        d_metro = self.destination_pin[:2] in metro_prefixes
        is_metro = int(o_metro and d_metro)
        is_remote = int(self.destination_pin.startswith("79") or self.origin_pin.startswith("79"))

        return {
            "length_cm": float(self.length_cm),
            "width_cm": float(self.width_cm),
            "height_cm": float(self.height_cm),
            "parcel_weight_kg": float(self.parcel_weight_kg),
            "volumetric_weight_kg": round(float(vol_weight), 2),
            "cod_amount": float(self.cod_amount) if self.is_cod else 0.0,
            "package_type": self.package_type,
            "courier_code": self.courier_code.upper(),
            "is_cod": 1 if self.is_cod else 0,
            "is_metro_route": is_metro,
            "is_remote_route": is_remote,
        }


# -----------------------------------------------------------------------------
# Reference Sculpture Model Features (Rahul-Shelke-1 Reference Repo)
# -----------------------------------------------------------------------------

class ReferenceSculptureFeatures(BaseModel):
    """
    Features required by the reference sculpture / artwork price prediction model.
    Priced in USD. Matches HackerEarth Art Shipment dataset.
    """
    artist_reputation: float = Field(default=0.5, ge=0.0, le=1.0, alias="Artist Reputation")
    height: float = Field(..., gt=0.0, le=1000.0, alias="Height")
    width: float = Field(..., gt=0.0, le=1000.0, alias="Width")
    weight: float = Field(..., gt=0.0, le=1000000.0, alias="Weight")
    material: str = Field(default="Bronze", alias="Material")
    price_of_sculpture: float = Field(..., ge=0.0, alias="Price Of Sculpture")
    base_shipping_price: float = Field(..., ge=0.0, alias="Base Shipping Price")
    international: str = Field(default="No", alias="International")
    express_shipment: str = Field(default="No", alias="Express Shipment")
    installation_included: str = Field(default="No", alias="Installation Included")
    transport: str = Field(default="Roadways", alias="Transport")
    fragile: str = Field(default="No", alias="Fragile")
    customer_information: str = Field(default="Working Class", alias="Customer Information")
    remote_location: str = Field(default="No", alias="Remote Location")

    model_config = ConfigDict(populate_by_name=True)

    def to_model_dataframe_row(self) -> Dict[str, Any]:
        """Convert to exact column names expected by reference model ColumnTransformer."""
        return {
            "Artist Reputation": float(self.artist_reputation),
            "Height": float(self.height),
            "Width": float(self.width),
            "Weight": float(self.weight),
            "Price Of Sculpture": float(self.price_of_sculpture),
            "Base Shipping Price": float(self.base_shipping_price),
            "Material": str(self.material),
            "International": "Yes" if str(self.international).lower() in ("yes", "true", "1") else "No",
            "Express Shipment": "Yes" if str(self.express_shipment).lower() in ("yes", "true", "1") else "No",
            "Installation Included": "Yes" if str(self.installation_included).lower() in ("yes", "true", "1") else "No",
            "Transport": str(self.transport),
            "Fragile": "Yes" if str(self.fragile).lower() in ("yes", "true", "1") else "No",
            "Customer Information": str(self.customer_information),
            "Remote Location": "Yes" if str(self.remote_location).lower() in ("yes", "true", "1") else "No",
        }
