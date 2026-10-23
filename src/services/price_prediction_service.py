"""
High-Level Shipment Price Prediction & Quote Comparison Service.
Orchestrates ML price inference, compares predictions against live or simulated
carrier quotes, and generates actionable agent insights without overwriting quotes.
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

logger = logging.getLogger("ShipmentPricePredictionService")


class QuoteComparisonDetails(BaseModel):
    """Detailed variance analysis between an ML predicted price and a provider quote."""
    courier_code: str
    courier_name: str
    provider_quote_cost: float
    model_predicted_price: float
    currency: str = "INR"
    difference_amount: float = Field(..., description="Provider quote minus ML prediction")
    percentage_difference: float = Field(..., description="Percentage delta relative to prediction")
    verdict: str = Field(..., description="CONGRUENT, COMPETITIVE_DISCOUNT, or PREMIUM_OVERRUN")
    analysis: str = Field(..., description="Detailed explanation of variance drivers")


class PricePredictionServiceResponse(BaseModel):
    """Comprehensive service response returned to APIs and Agent tools."""
    prediction: PricePredictionResult
    quote_comparison: Optional[QuoteComparisonDetails] = None
    agent_advice: str
    recommended_action: str


class ShipmentPricePredictionService:
    """Service handling prediction and intelligent quote comparison."""

    def predict_and_compare(
        self,
        features: DomesticCourierFeatures,
        active_quote: Optional[Dict[str, Any]] = None,
    ) -> PricePredictionServiceResponse:
        """
        Execute ML prediction for domestic shipment and compare against active courier quote if provided.
        """
        pred_result = predict_shipment_price(features)

        if pred_result.status != PredictionStatus.SUCCESS or pred_result.predicted_price is None:
            return PricePredictionServiceResponse(
                prediction=pred_result,
                quote_comparison=None,
                agent_advice="Machine learning estimate could not be computed. Proceed with carrier quotes directly.",
                recommended_action="Use carrier rate lookup.",
            )

        comp = None
        advice = "Statistical baseline established. Compare with live courier rates before dispatch."
        rec_action = "Review live quotes."

        if active_quote and "cost" in active_quote:
            quote_cost = float(active_quote["cost"])
            courier_code = str(active_quote.get("courier_code", "UNKNOWN"))
            courier_name = str(active_quote.get("courier_name", courier_code))
            pred_cost = pred_result.predicted_price

            diff = round(quote_cost - pred_cost, 2)
            pct_diff = round((diff / pred_cost) * 100.0, 1) if pred_cost > 0 else 0.0

            if abs(pct_diff) <= 12.0:
                verdict = "CONGRUENT"
                analysis = (
                    f"Provider quote of ₹{quote_cost:.2f} closely matches the ML prediction "
                    f"of ₹{pred_cost:.2f} (variance: {pct_diff:+.1f}%). Pricing is within normal statistical distribution."
                )
                advice = "Quote is fair and aligned with market historical rates."
                rec_action = "Proceed to approval gate with confidence."
            elif diff < 0:
                verdict = "COMPETITIVE_DISCOUNT"
                analysis = (
                    f"Provider quote of ₹{quote_cost:.2f} is ₹{abs(diff):.2f} ({abs(pct_diff):.1f}%) "
                    f"cheaper than the historical ML benchmark of ₹{pred_cost:.2f}. Represents strong cost efficiency."
                )
                advice = "Selected courier offers a discounted rate below historical market expectations."
                rec_action = "Recommended for lowest-cost fulfillment."
            else:
                verdict = "PREMIUM_OVERRUN"
                analysis = (
                    f"Provider quote of ₹{quote_cost:.2f} is ₹{diff:.2f} (+{pct_diff:.1f}%) "
                    f"higher than the baseline estimate of ₹{pred_cost:.2f}. May be driven by expedited SLA or remote surcharges."
                )
                advice = "Quote carries a premium over the statistical baseline. Verify if faster SLA or special handling justifies the cost."
                rec_action = "Compare with alternative couriers or packaging optimizations."

            comp = QuoteComparisonDetails(
                courier_code=courier_code,
                courier_name=courier_name,
                provider_quote_cost=quote_cost,
                model_predicted_price=pred_cost,
                currency="INR",
                difference_amount=diff,
                percentage_difference=pct_diff,
                verdict=verdict,
                analysis=analysis,
            )

        return PricePredictionServiceResponse(
            prediction=pred_result,
            quote_comparison=comp,
            agent_advice=advice,
            recommended_action=rec_action,
        )


# Singleton instance
default_prediction_service = ShipmentPricePredictionService()
