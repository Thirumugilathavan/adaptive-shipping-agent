"""
API Schemas for Shipment Price Prediction Endpoints.
Ensures validated requests, type safety, and clean serializable responses.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.ml.feature_schema import PredictionStatus


class PricePredictionApiRequest(BaseModel):
    """Payload for POST /api/v1/predictions/shipment-price."""
    # Domestic routing & measurements
    origin_pin: str = Field("110001", description="6-digit Indian origin PIN code")
    destination_pin: str = Field("400001", description="6-digit Indian destination PIN code")
    length_cm: float = Field(..., gt=0.0, description="Parcel length in cm")
    width_cm: float = Field(..., gt=0.0, description="Parcel width in cm")
    height_cm: float = Field(..., gt=0.0, description="Parcel height in cm")
    parcel_weight_kg: float = Field(..., gt=0.0, description="Parcel scale weight in kg")
    package_type: str = Field("box", description="Packaging type: box, flyer, wooden_crate, custom")
    courier_code: str = Field("DEMO_AIR_EXPRESS", description="Courier carrier code")
    is_cod: bool = Field(False, description="COD flag")
    cod_amount: float = Field(0.0, ge=0.0, description="COD collection amount")

    # Optional model selector: "domestic_courier" (default) or "reference_sculpture"
    model_type: str = Field("domestic_courier", description="Target model: domestic_courier or reference_sculpture")

    # Optional sculpture features if testing reference model
    reference_features: Optional[Dict[str, Any]] = Field(None, description="HackerEarth sculpture features if testing reference model")

    # Optional live/demo quote for automated comparison
    quote_cost: Optional[float] = Field(None, description="Current provider quote cost to compare against ML estimate")
    quote_courier_name: Optional[str] = Field(None, description="Courier name for comparison")


class QuoteComparisonDto(BaseModel):
    courier_code: str
    courier_name: str
    provider_quote_cost: float
    model_predicted_price: float
    currency: str
    difference_amount: float
    percentage_difference: float
    verdict: str
    analysis: str


class PricePredictionApiResponse(BaseModel):
    """Response returned by POST /api/v1/predictions/shipment-price."""
    predicted_price: Optional[float]
    currency: str
    model_name: str
    model_version: str
    feature_schema_version: str
    status: PredictionStatus
    is_provider_quote: bool = False
    confidence_interval: Optional[Tuple[float, float]] = None
    input_summary: Dict[str, Any] = Field(default_factory=dict)
    limitations: List[str] = Field(default_factory=list)
    explanation: str
    quote_comparison: Optional[QuoteComparisonDto] = None
    agent_advice: str
    recommended_action: str
    error_message: Optional[str] = None
