"""
Agent Tools Definition & Execution Layer.
Exposes machine learning price prediction, parcel evaluation, and market benchmarking
as standardized, structured tools for agentic decision workflows.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from src.ml.feature_schema import (
    DomesticCourierFeatures,
    PredictionModelType,
    PredictionStatus,
    ReferenceSculptureFeatures,
)
from src.ml.price_predictor import PricePredictionResult, predict_shipment_price
from src.services.price_prediction_service import (
    PricePredictionServiceResponse,
    ShipmentPricePredictionService,
)

logger = logging.getLogger("AgentTools")


# Tool Definition Schema for LLM / Agent Orchestrators
PREDICT_SHIPMENT_PRICE_TOOL_DEFINITION = {
    "name": "predict_shipment_price",
    "description": (
        "Estimate domestic Indian shipping price or reference sculpture freight cost using trained "
        "machine learning regression models. Produces a non-binding statistical benchmark price "
        "with 90% confidence intervals. Does NOT override or replace binding carrier provider quotes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "origin_pin": {
                "type": "string",
                "description": "6-digit Indian origin postal code (e.g., '110001')",
            },
            "destination_pin": {
                "type": "string",
                "description": "6-digit Indian destination postal code (e.g., '400001')",
            },
            "length_cm": {
                "type": "number",
                "description": "Parcel length in centimetres",
            },
            "width_cm": {
                "type": "number",
                "description": "Parcel width in centimetres",
            },
            "height_cm": {
                "type": "number",
                "description": "Parcel height in centimetres",
            },
            "parcel_weight_kg": {
                "type": "number",
                "description": "Actual physical parcel weight in kilograms",
            },
            "package_type": {
                "type": "string",
                "enum": ["box", "flyer", "wooden_crate", "custom"],
                "description": "Type of outer container packaging",
                "default": "box",
            },
            "courier_code": {
                "type": "string",
                "description": "Target carrier code (e.g., 'DEMO_AIR_EXPRESS', 'DEMO_SURFACE_STANDARD')",
                "default": "DEMO_AIR_EXPRESS",
            },
            "is_cod": {
                "type": "boolean",
                "description": "Whether Cash On Delivery is required",
                "default": False,
            },
            "cod_amount": {
                "type": "number",
                "description": "COD cash value to collect in INR",
                "default": 0.0,
            },
            "model_type": {
                "type": "string",
                "enum": ["domestic_courier", "reference_sculpture"],
                "description": "ML model to evaluate (domestic_courier for Indian parcel INR, reference_sculpture for sculpture freight USD)",
                "default": "domestic_courier",
            },
            "quote_cost": {
                "type": "number",
                "description": "Optional active carrier quote cost to compare against the statistical baseline",
            },
            "quote_courier_name": {
                "type": "string",
                "description": "Optional carrier name corresponding to the active quote",
            },
        },
        "required": ["origin_pin", "destination_pin", "length_cm", "width_cm", "height_cm", "parcel_weight_kg"],
    },
}


class PredictShipmentPriceToolInput(BaseModel):
    """Structured input parameters for predict_shipment_price tool."""
    origin_pin: str = Field(..., description="6-digit Indian origin postal code")
    destination_pin: str = Field(..., description="6-digit Indian destination postal code")
    length_cm: float = Field(..., gt=0.0)
    width_cm: float = Field(..., gt=0.0)
    height_cm: float = Field(..., gt=0.0)
    parcel_weight_kg: float = Field(..., gt=0.0)
    package_type: str = Field("box")
    courier_code: str = Field("DEMO_AIR_EXPRESS")
    is_cod: bool = Field(False)
    cod_amount: float = Field(0.0, ge=0.0)
    model_type: str = Field("domestic_courier")
    quote_cost: Optional[float] = None
    quote_courier_name: Optional[str] = None
    reference_features: Optional[Dict[str, Any]] = None


def execute_predict_shipment_price_tool(
    inputs: Dict[str, Any] | PredictShipmentPriceToolInput,
) -> Dict[str, Any]:
    """
    Execute predict_shipment_price tool with strict parameter validation,
    fallback handling, and clear separation of statistical prediction from binding quotes.
    """
    if isinstance(inputs, dict):
        validated = PredictShipmentPriceToolInput(**inputs)
    else:
        validated = inputs

    service = ShipmentPricePredictionService()

    if validated.model_type == "reference_sculpture":
        # Reference sculpture freight in USD
        ref_dict = validated.reference_features or {
            "Artist Reputation": 0.5,
            "Height": validated.height_cm,
            "Width": validated.width_cm,
            "Weight": validated.parcel_weight_kg * 1000.0,
            "Material": "Brass",
            "Price Of Sculpture": 500.0,
            "Base Shipping Price": 20.0,
            "International": "No",
            "Express Shipment": "Yes" if "EXPRESS" in validated.courier_code.upper() else "No",
            "Installation Included": "No",
            "Transport": "Airways",
            "Fragile": "Yes",
            "Customer Information": "Working Class",
            "Remote": "No",
            "Scheduled Date": "2026-10-23",
            "Delivery Date": "2026-10-26",
        }
        ref_feat = ReferenceSculptureFeatures(**ref_dict)
        pred = predict_shipment_price(ref_feat)
        return {
            "tool_name": "predict_shipment_price",
            "model_type": "reference_sculpture",
            "status": pred.status.value,
            "predicted_price": pred.predicted_price,
            "currency": pred.currency,
            "confidence_interval": pred.confidence_interval,
            "model_version": pred.model_version,
            "is_provider_quote": False,
            "explanation": pred.explanation,
            "limitations": pred.limitations,
            "advice": "Reference model predicts specialty sculpture freight in USD. Not representative of Indian domestic parcel rates.",
        }

    # Domestic courier parcel in INR
    domestic_feat = DomesticCourierFeatures(
        origin_pin=validated.origin_pin,
        destination_pin=validated.destination_pin,
        length_cm=validated.length_cm,
        width_cm=validated.width_cm,
        height_cm=validated.height_cm,
        parcel_weight_kg=validated.parcel_weight_kg,
        package_type=validated.package_type,
        courier_code=validated.courier_code,
        is_cod=validated.is_cod,
        cod_amount=validated.cod_amount,
    )

    active_quote = None
    if validated.quote_cost is not None:
        active_quote = {
            "cost": validated.quote_cost,
            "courier_code": validated.courier_code,
            "courier_name": validated.quote_courier_name or validated.courier_code,
        }

    resp: PricePredictionServiceResponse = service.predict_and_compare(domestic_feat, active_quote=active_quote)

    return {
        "tool_name": "predict_shipment_price",
        "model_type": "domestic_courier",
        "status": resp.prediction.status.value,
        "predicted_price": resp.prediction.predicted_price,
        "currency": resp.prediction.currency,
        "confidence_interval": resp.prediction.confidence_interval,
        "model_version": resp.prediction.model_version,
        "feature_schema_version": resp.prediction.feature_schema_version,
        "is_provider_quote": False,
        "input_summary": resp.prediction.input_summary,
        "explanation": resp.prediction.explanation,
        "limitations": resp.prediction.limitations,
        "quote_comparison": resp.quote_comparison.model_dump() if resp.quote_comparison else None,
        "agent_advice": resp.agent_advice,
        "recommended_action": resp.recommended_action,
    }
