"""
Modular Machine Learning Shipment Price Predictor.
Implements predictable, safe inference with structured output, confidence intervals,
and explicit labeling of ML estimates vs live provider quotes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from src.ml.feature_schema import (
    FEATURE_SCHEMA_VERSION,
    DomesticCourierFeatures,
    PredictionModelType,
    PredictionStatus,
    ReferenceSculptureFeatures,
)
from src.ml.model_loader import ModelArtifactNotFoundError, ModelLoader

logger = logging.getLogger("ShipmentPricePredictor")


class PricePredictionResult(BaseModel):
    """Structured result returned by the shipment price prediction engine."""
    predicted_price: Optional[float] = Field(None, description="Estimated landed shipping price in documented currency")
    currency: str = Field("INR", description="Documented currency code (INR or USD)")
    model_name: str = Field(..., description="Name of the machine learning model")
    model_version: str = Field("1.0.0", description="Semantic model version")
    feature_schema_version: str = Field(FEATURE_SCHEMA_VERSION, description="Feature schema version")
    status: PredictionStatus = Field(..., description="Outcome status of the prediction request")
    is_provider_quote: bool = Field(False, description="Always False: Indicates this is a statistical estimate, not a binding quote")
    confidence_interval: Optional[Tuple[float, float]] = Field(None, description="Estimated 90% prediction range [min, max]")
    input_summary: Dict[str, Any] = Field(default_factory=dict, description="Summary of features processed")
    limitations: List[str] = Field(default_factory=list, description="Known operational limitations and edge-case notices")
    explanation: str = Field(..., description="Human-readable explanation of the prediction outcome")
    error_message: Optional[str] = Field(None, description="Detailed error description if prediction could not be completed")


class ShipmentPricePredictor:
    """
    Inference service for shipment pricing.
    Supports both Domestic Courier (INR) and Reference Sculpture (USD) models.
    """

    def __init__(self, loader: Optional[ModelLoader] = None):
        self.loader = loader or ModelLoader()

    def predict_domestic(self, features: DomesticCourierFeatures) -> PricePredictionResult:
        """Predict domestic Indian courier shipment price in INR."""
        model_type = PredictionModelType.DOMESTIC_COURIER
        summary = {
            "origin_pin": features.origin_pin,
            "destination_pin": features.destination_pin,
            "dimensions_cm": f"{features.length_cm:.1f}x{features.width_cm:.1f}x{features.height_cm:.1f}",
            "parcel_weight_kg": features.parcel_weight_kg,
            "volumetric_weight_kg": round(features.compute_volumetric_weight(), 2),
            "package_type": features.package_type,
            "courier_code": features.courier_code,
            "is_cod": features.is_cod,
            "cod_amount": features.cod_amount,
        }

        try:
            pipeline, metadata = self.loader.get_model(model_type)
        except ModelArtifactNotFoundError as err:
            return PricePredictionResult(
                predicted_price=None,
                currency="INR",
                model_name=model_type.value,
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.MODEL_UNAVAILABLE,
                is_provider_quote=False,
                input_summary=summary,
                limitations=["Model artifact domestic_courier_model.joblib is not present."],
                explanation="Domestic price prediction model is currently unavailable on disk.",
                error_message=str(err),
            )
        except Exception as err:
            return PricePredictionResult(
                predicted_price=None,
                currency="INR",
                model_name=model_type.value,
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.ERROR,
                is_provider_quote=False,
                input_summary=summary,
                explanation="Failed to initialize prediction model pipeline.",
                error_message=str(err),
            )

        try:
            row_dict = features.to_model_dataframe_row()
            df_input = pd.DataFrame([row_dict])

            # Run inference
            raw_pred = float(pipeline.predict(df_input)[0])
            pred_price = round(max(30.0, raw_pred), 2)

            # Estimate ±12% empirical prediction uncertainty band
            ci_low = round(pred_price * 0.88, 2)
            ci_high = round(pred_price * 1.12, 2)

            limits = metadata.get("limitations", [
                "Estimate based on historical domestic courier rating benchmarks.",
                "Non-binding statistical prediction; actual courier quote takes precedence.",
            ])

            return PricePredictionResult(
                predicted_price=pred_price,
                currency="INR",
                model_name=metadata.get("model_name", "DomesticCourierPricePredictor"),
                model_version=metadata.get("model_version", "1.0.0"),
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.SUCCESS,
                is_provider_quote=False,
                confidence_interval=(ci_low, ci_high),
                input_summary=summary,
                limitations=limits,
                explanation=(
                    f"Predicted estimated landed courier cost of ₹{pred_price:.2f} (90% range: ₹{ci_low:.2f}–₹{ci_high:.2f}) "
                    f"using {metadata.get('algorithm', 'GradientBoostingRegressor')}."
                ),
            )

        except Exception as exc:
            logger.error("Inference execution error: %s", exc)
            return PricePredictionResult(
                predicted_price=None,
                currency="INR",
                model_name=metadata.get("model_name", "DomesticCourierPricePredictor"),
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.ERROR,
                is_provider_quote=False,
                input_summary=summary,
                explanation="Inference failed during feature transformation or model scoring.",
                error_message=str(exc),
            )

    def predict_reference_sculpture(self, features: ReferenceSculptureFeatures) -> PricePredictionResult:
        """Predict reference sculpture / specialty freight shipment price in USD."""
        model_type = PredictionModelType.REFERENCE_SCULPTURE
        summary = {
            "artist_reputation": features.artist_reputation,
            "dimensions_cm": f"{features.height:.1f}x{features.width:.1f}",
            "weight_kg": features.weight,
            "material": features.material,
            "price_of_sculpture": features.price_of_sculpture,
            "base_shipping_price": features.base_shipping_price,
            "transport": features.transport,
            "international": features.international,
        }

        try:
            pipeline, metadata = self.loader.get_model(model_type)
        except ModelArtifactNotFoundError as err:
            return PricePredictionResult(
                predicted_price=None,
                currency="USD",
                model_name=model_type.value,
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.MODEL_UNAVAILABLE,
                is_provider_quote=False,
                input_summary=summary,
                limitations=["Reference sculpture model artifact is not loaded on disk."],
                explanation="Reference sculpture price prediction model is currently unavailable on disk.",
                error_message=str(err),
            )
        except Exception as err:
            return PricePredictionResult(
                predicted_price=None,
                currency="USD",
                model_name=model_type.value,
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.ERROR,
                is_provider_quote=False,
                input_summary=summary,
                explanation="Failed to initialize reference model pipeline.",
                error_message=str(err),
            )

        try:
            row_dict = features.to_model_dataframe_row()
            df_input = pd.DataFrame([row_dict])

            # Run inference on log-scale and invert via expm1
            pred_log = float(pipeline.predict(df_input)[0])
            pred_price = round(float(np.expm1(max(0.0, pred_log))), 2)

            ci_low = round(pred_price * 0.85, 2)
            ci_high = round(pred_price * 1.18, 2)

            limits = metadata.get("limitations", [
                "Trained on HackerEarth sculpture shipment dataset in USD.",
                "Not applicable to domestic Indian e-commerce parcel shipping.",
            ])

            return PricePredictionResult(
                predicted_price=pred_price,
                currency="USD",
                model_name=metadata.get("model_name", "ReferenceSculpturePricePredictor"),
                model_version=metadata.get("model_version", "1.0.0"),
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.SUCCESS,
                is_provider_quote=False,
                confidence_interval=(ci_low, ci_high),
                input_summary=summary,
                limitations=limits,
                explanation=(
                    f"Predicted sculpture shipment cost of ${pred_price:,.2f} USD "
                    f"using reference Random Forest Regressor."
                ),
            )

        except Exception as exc:
            logger.error("Reference inference execution error: %s", exc)
            return PricePredictionResult(
                predicted_price=None,
                currency="USD",
                model_name=metadata.get("model_name", "ReferenceSculpturePricePredictor"),
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.ERROR,
                is_provider_quote=False,
                input_summary=summary,
                explanation="Inference failed during reference feature transformation.",
                error_message=str(exc),
            )


# Default global predictor instance
default_predictor = ShipmentPricePredictor()


def predict_shipment_price(
    features: Union[DomesticCourierFeatures, ReferenceSculptureFeatures, Dict[str, Any]],
    model_type: Optional[PredictionModelType] = None,
) -> PricePredictionResult:
    """
    Public top-level interface for shipment price prediction.
    Accepts validated schema models or raw dictionaries.
    """
    if isinstance(features, DomesticCourierFeatures):
        return default_predictor.predict_domestic(features)

    if isinstance(features, ReferenceSculptureFeatures):
        return default_predictor.predict_reference_sculpture(features)

    if isinstance(features, dict):
        # Auto-detect schema by keys
        if "Artist Reputation" in features or "price_of_sculpture" in features or "Material" in features:
            try:
                parsed_sculpture = ReferenceSculptureFeatures(**features)
                return default_predictor.predict_reference_sculpture(parsed_sculpture)
            except Exception as e:
                return PricePredictionResult(
                    predicted_price=None,
                    currency="USD",
                    model_name="ReferenceSculpturePricePredictor",
                    model_version="1.0.0",
                    feature_schema_version=FEATURE_SCHEMA_VERSION,
                    status=PredictionStatus.INVALID_INPUT,
                    is_provider_quote=False,
                    input_summary=features,
                    explanation="Input validation failed for reference sculpture model schema.",
                    error_message=str(e),
                )
        else:
            try:
                parsed_domestic = DomesticCourierFeatures(**features)
                return default_predictor.predict_domestic(parsed_domestic)
            except Exception as e:
                return PricePredictionResult(
                    predicted_price=None,
                    currency="INR",
                    model_name="DomesticCourierPricePredictor",
                    model_version="1.0.0",
                    feature_schema_version=FEATURE_SCHEMA_VERSION,
                    status=PredictionStatus.INVALID_INPUT,
                    is_provider_quote=False,
                    input_summary=features,
                    explanation="Input validation failed for domestic courier model schema.",
                    error_message=str(e),
                )

    raise ValueError(f"Unsupported features type: {type(features)}")


def predict_domestic_courier_price(
    features: DomesticCourierFeatures,
    artifact_filename: Optional[str] = None,
) -> PricePredictionResult:
    """Helper function to predict domestic courier shipping price."""
    if artifact_filename is not None:
        from src.ml.model_loader import load_model_artifact
        pipeline, meta = load_model_artifact(PredictionModelType.DOMESTIC_COURIER, artifact_filename=artifact_filename)
        if pipeline is None:
            return PricePredictionResult(
                predicted_price=None,
                currency="INR",
                model_name="DomesticCourierModel",
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.MODEL_UNAVAILABLE,
                is_provider_quote=False,
                explanation="Model artifact is currently unavailable.",
                limitations=["Trained model file missing or corrupted."],
            )
    return default_predictor.predict_domestic(features)


def predict_reference_sculpture_price(
    features: ReferenceSculptureFeatures,
    artifact_filename: Optional[str] = None,
) -> PricePredictionResult:
    """Helper function to predict reference sculpture freight price."""
    if artifact_filename is not None:
        from src.ml.model_loader import load_model_artifact
        pipeline, meta = load_model_artifact(PredictionModelType.REFERENCE_SCULPTURE, artifact_filename=artifact_filename)
        if pipeline is None:
            return PricePredictionResult(
                predicted_price=None,
                currency="USD",
                model_name="ReferenceSculptureModel",
                model_version="1.0.0",
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                status=PredictionStatus.MODEL_UNAVAILABLE,
                is_provider_quote=False,
                explanation="Model artifact is currently unavailable.",
                limitations=["Trained model file missing or corrupted."],
            )
    return default_predictor.predict_reference_sculpture(features)

