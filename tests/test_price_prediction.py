"""
Automated Test Suite for Shipment Price Prediction ML Module.
Covers:
  - Model artifact loading, caching, path security & fallback
  - Domestic courier price prediction (INR) & 90% confidence intervals
  - Reference sculpture price prediction (USD)
  - Pydantic schema validation & error rejection
  - Unseen/unknown categorical handling without crashes
  - Non-binding safety assertion (provider quote separation)
  - Quote comparison variance analysis (CONGRUENT, COMPETITIVE_DISCOUNT, PREMIUM_OVERRUN)
  - Agent tool execution (predict_shipment_price)
  - Workflow orchestrator trace integration (AgentDecisionEngine.run_predict_price)
  - REST API endpoint verification (POST /api/v1/predictions/shipment-price)
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from src.ml.feature_schema import (
    DomesticCourierFeatures,
    PredictionModelType,
    PredictionStatus,
    ReferenceSculptureFeatures,
)
from src.ml.model_loader import (
    clear_model_cache,
    get_model_metadata,
    load_model_artifact,
)
from src.ml.price_predictor import (
    PricePredictionResult,
    predict_domestic_courier_price,
    predict_reference_sculpture_price,
    predict_shipment_price,
)
from src.services.price_prediction_service import (
    PricePredictionServiceResponse,
    ShipmentPricePredictionService,
)
from src.domain.agent_tools import (
    PREDICT_SHIPMENT_PRICE_TOOL_DEFINITION,
    PredictShipmentPriceToolInput,
    execute_predict_shipment_price_tool,
)
from src.domain.models import Address, Dimensions, OrderInput, PackageType
from src.domain.workflow import AgentDecisionEngine, AgentDecisionTrace, DecisionTraceStatus
from src.server import app


# -----------------------------------------------------------------------------
# 1. Model Loading, Security & Fallback Tests
# -----------------------------------------------------------------------------

def test_model_loading_and_caching():
    """Verify models are loaded from disk and subsequent calls use the cache."""
    clear_model_cache()
    model, meta = load_model_artifact(PredictionModelType.DOMESTIC_COURIER)
    assert model is not None
    assert meta is not None
    assert meta.get("model_name") == "DomesticCourierPricePredictor"
    assert "GradientBoostingRegressor" in meta.get("algorithm")
    assert meta.get("currency") == "INR"

    # Second call should return cached object
    model2, _ = load_model_artifact(PredictionModelType.DOMESTIC_COURIER)
    assert model2 is model


def test_model_loading_security_sandbox():
    """Verify paths outside the trusted models directory cannot be loaded."""
    with pytest.raises(PermissionError):
        load_model_artifact(PredictionModelType.DOMESTIC_COURIER, artifact_filename="../sensitive_file.joblib")


def test_model_missing_fallback():
    """Verify graceful fallback when model artifact does not exist."""
    clear_model_cache()
    model, meta = load_model_artifact(PredictionModelType.DOMESTIC_COURIER, artifact_filename="non_existent_model.joblib")
    assert model is None
    assert meta.get("status") == "MISSING"

    # Inference should return MODEL_UNAVAILABLE gracefully
    feat = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="400001",
        length_cm=20.0,
        width_cm=15.0,
        height_cm=10.0,
        parcel_weight_kg=1.5,
    )
    res = predict_domestic_courier_price(feat, artifact_filename="non_existent_model.joblib")
    assert res.status == PredictionStatus.MODEL_UNAVAILABLE
    assert res.predicted_price is None
    assert "unavailable" in res.explanation.lower()


# -----------------------------------------------------------------------------
# 2. Domestic Courier Prediction (INR) Tests
# -----------------------------------------------------------------------------

def test_domestic_courier_prediction_success():
    """Verify domestic shipment prediction produces positive INR price with confidence intervals."""
    feat = DomesticCourierFeatures(
        origin_pin="110001",  # Delhi
        destination_pin="400001",  # Mumbai
        length_cm=25.0,
        width_cm=20.0,
        height_cm=15.0,
        parcel_weight_kg=2.0,
        package_type="box",
        courier_code="DEMO_AIR_EXPRESS",
        is_cod=False,
    )
    res = predict_shipment_price(feat)
    assert res.status == PredictionStatus.SUCCESS
    assert res.predicted_price is not None
    assert res.predicted_price > 0.0
    assert res.currency == "INR"
    assert res.is_provider_quote is False
    assert res.confidence_interval is not None
    lower, upper = res.confidence_interval
    assert lower < res.predicted_price < upper
    assert len(res.limitations) > 0


def test_domestic_courier_heavy_parcel():
    """Verify price scales logically with heavier weight."""
    feat_light = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="560001",
        length_cm=15.0,
        width_cm=10.0,
        height_cm=10.0,
        parcel_weight_kg=0.5,
    )
    feat_heavy = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="560001",
        length_cm=50.0,
        width_cm=40.0,
        height_cm=30.0,
        parcel_weight_kg=12.0,
    )
    res_light = predict_shipment_price(feat_light)
    res_heavy = predict_shipment_price(feat_heavy)

    assert res_light.predicted_price is not None
    assert res_heavy.predicted_price is not None
    assert res_heavy.predicted_price > res_light.predicted_price


# -----------------------------------------------------------------------------
# 3. Reference Sculpture Model (USD) Tests
# -----------------------------------------------------------------------------

def test_reference_sculpture_prediction_success():
    """Verify reference model predicts sculpture freight in USD."""
    ref_feat = ReferenceSculptureFeatures(
        artist_reputation=0.65,
        height=30.0,
        width=25.0,
        weight=5000.0,
        material="Bronze",
        price_of_sculpture=1200.0,
        base_shipping_price=45.0,
        international="Yes",
        express_shipment="No",
        installation_included="No",
        transport="Airways",
        fragile="Yes",
        customer_information="Working Class",
        remote_location="No",
    )
    res = predict_shipment_price(ref_feat)
    assert res.status == PredictionStatus.SUCCESS
    assert res.predicted_price is not None
    assert res.predicted_price > 0.0
    assert res.currency == "USD"
    assert res.is_provider_quote is False
    lower, upper = res.confidence_interval
    assert lower < res.predicted_price < upper


# -----------------------------------------------------------------------------
# 4. Schema Validation & Rejection Tests
# -----------------------------------------------------------------------------

def test_schema_rejects_invalid_pincode():
    """Verify 5-digit or alphabetic PIN codes are rejected."""
    with pytest.raises(ValidationError):
        DomesticCourierFeatures(
            origin_pin="11000",  # Only 5 digits
            destination_pin="400001",
            length_cm=20.0,
            width_cm=15.0,
            height_cm=10.0,
            parcel_weight_kg=1.0,
        )

    with pytest.raises(ValidationError):
        DomesticCourierFeatures(
            origin_pin="ABCDEF",  # Non-digit
            destination_pin="400001",
            length_cm=20.0,
            width_cm=15.0,
            height_cm=10.0,
            parcel_weight_kg=1.0,
        )


def test_schema_rejects_negative_measurements():
    """Verify negative dimensions and non-positive weight are rejected."""
    with pytest.raises(ValidationError):
        DomesticCourierFeatures(
            origin_pin="110001",
            destination_pin="400001",
            length_cm=-10.0,  # Negative
            width_cm=15.0,
            height_cm=10.0,
            parcel_weight_kg=1.0,
        )

    with pytest.raises(ValidationError):
        DomesticCourierFeatures(
            origin_pin="110001",
            destination_pin="400001",
            length_cm=20.0,
            width_cm=15.0,
            height_cm=10.0,
            parcel_weight_kg=0.0,  # Zero weight
        )


# -----------------------------------------------------------------------------
# 5. Unseen Categorical Handling Tests
# -----------------------------------------------------------------------------

def test_unseen_categorical_graceful_handling():
    """Verify unseen courier code does not crash model pipeline (handle_unknown='ignore')."""
    feat = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="400001",
        length_cm=20.0,
        width_cm=15.0,
        height_cm=10.0,
        parcel_weight_kg=1.0,
        package_type="box",
        courier_code="FUTURE_HYPERLOOP_EXPRESS",
    )
    res = predict_shipment_price(feat)
    assert res.status == PredictionStatus.SUCCESS
    assert res.predicted_price is not None
    assert res.predicted_price > 0.0


# -----------------------------------------------------------------------------
# 6. Non-Binding Safety & Provider Quote Authority
# -----------------------------------------------------------------------------

def test_non_binding_safety_guarantee():
    """Verify ML predictions never mutate or overwrite active carrier quotes."""
    active_quote = {
        "quote_id": "Q-TEST-001",
        "courier_code": "DEMO_AIR_EXPRESS",
        "courier_name": "Demo Air Express",
        "cost": 310.50,
    }
    feat = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="400001",
        length_cm=20.0,
        width_cm=15.0,
        height_cm=10.0,
        parcel_weight_kg=1.5,
    )
    service = ShipmentPricePredictionService()
    resp = service.predict_and_compare(feat, active_quote=active_quote)

    assert resp.prediction.is_provider_quote is False
    assert active_quote["cost"] == 310.50  # Original quote untouched
    assert resp.quote_comparison is not None
    assert resp.quote_comparison.provider_quote_cost == 310.50


# -----------------------------------------------------------------------------
# 7. Quote Comparison & Variance Verdict Tests
# -----------------------------------------------------------------------------

def test_quote_comparison_congruent():
    """Verify variance within 12% is classified as CONGRUENT."""
    feat = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="400001",
        length_cm=20.0,
        width_cm=15.0,
        height_cm=10.0,
        parcel_weight_kg=1.5,
    )
    pred_res = predict_shipment_price(feat)
    baseline = pred_res.predicted_price
    assert baseline is not None

    service = ShipmentPricePredictionService()
    # Quote with +5% variance
    resp = service.predict_and_compare(feat, active_quote={"cost": round(baseline * 1.05, 2), "courier_code": "DEMO_AIR"})
    assert resp.quote_comparison.verdict == "CONGRUENT"


def test_quote_comparison_discount():
    """Verify quote cheaper than ML benchmark by >12% is classified as COMPETITIVE_DISCOUNT."""
    feat = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="400001",
        length_cm=20.0,
        width_cm=15.0,
        height_cm=10.0,
        parcel_weight_kg=1.5,
    )
    pred_res = predict_shipment_price(feat)
    baseline = pred_res.predicted_price
    assert baseline is not None

    service = ShipmentPricePredictionService()
    resp = service.predict_and_compare(feat, active_quote={"cost": round(baseline * 0.70, 2), "courier_code": "DEMO_SURFACE"})
    assert resp.quote_comparison.verdict == "COMPETITIVE_DISCOUNT"


def test_quote_comparison_overrun():
    """Verify quote more expensive than ML benchmark by >12% is classified as PREMIUM_OVERRUN."""
    feat = DomesticCourierFeatures(
        origin_pin="110001",
        destination_pin="400001",
        length_cm=20.0,
        width_cm=15.0,
        height_cm=10.0,
        parcel_weight_kg=1.5,
    )
    pred_res = predict_shipment_price(feat)
    baseline = pred_res.predicted_price
    assert baseline is not None

    service = ShipmentPricePredictionService()
    resp = service.predict_and_compare(feat, active_quote={"cost": round(baseline * 1.50, 2), "courier_code": "EXPEDITED_JET"})
    assert resp.quote_comparison.verdict == "PREMIUM_OVERRUN"


# -----------------------------------------------------------------------------
# 8. Agent Tool Execution Tests
# -----------------------------------------------------------------------------

def test_agent_tool_definition_and_execution():
    """Verify tool definition schema and structured tool execution."""
    assert PREDICT_SHIPMENT_PRICE_TOOL_DEFINITION["name"] == "predict_shipment_price"
    assert "parameters" in PREDICT_SHIPMENT_PRICE_TOOL_DEFINITION

    inputs = {
        "origin_pin": "110001",
        "destination_pin": "400001",
        "length_cm": 22.0,
        "width_cm": 16.0,
        "height_cm": 12.0,
        "parcel_weight_kg": 1.8,
        "courier_code": "DEMO_AIR_EXPRESS",
        "quote_cost": 315.0,
        "quote_courier_name": "Demo Air Express",
    }
    tool_res = execute_predict_shipment_price_tool(inputs)
    assert tool_res["tool_name"] == "predict_shipment_price"
    assert tool_res["status"] == "SUCCESS"
    assert tool_res["predicted_price"] > 0
    assert tool_res["currency"] == "INR"
    assert tool_res["quote_comparison"] is not None


# -----------------------------------------------------------------------------
# 9. Workflow Orchestrator Integration Tests
# -----------------------------------------------------------------------------

def test_workflow_decision_engine_run_predict_price():
    """Verify AgentDecisionEngine records a decision trace step for price prediction."""
    engine = AgentDecisionEngine()
    trace = engine.create_trace("ORD-TEST-TRACE-001")

    order = OrderInput(
        order_reference="ORD-TEST-TRACE-001",
        sender=Address(name="Sender Inc", phone="9876543210", street_line1="1 Main St", city="Delhi", state="Delhi", pin_code="110001"),
        recipient=Address(name="Receiver Co", phone="9876543211", street_line1="2 Sea Rd", city="Mumbai", state="Maharashtra", pin_code="400001"),
        dimensions=Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0),
        parcel_weight_kg=1.5,
        package_type=PackageType.BOX,
    )

    res = engine.run_predict_price(order, trace)
    assert res is not None
    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.stage == "Stage B: Evaluate Alternatives"
    assert "ML Shipment Price Prediction" in step.title
    assert step.status == DecisionTraceStatus.VERIFIED
    assert "predicted_price" in step.evidence


# -----------------------------------------------------------------------------
# 10. FastAPI Endpoint Tests
# -----------------------------------------------------------------------------

def test_fastapi_prediction_endpoint_domestic():
    """Verify POST /api/v1/predictions/shipment-price returns 200 for domestic inputs."""
    client = TestClient(app)
    payload = {
        "origin_pin": "110001",
        "destination_pin": "400001",
        "length_cm": 20.0,
        "width_cm": 15.0,
        "height_cm": 10.0,
        "parcel_weight_kg": 1.5,
        "package_type": "box",
        "courier_code": "DEMO_AIR_EXPRESS",
        "quote_cost": 300.0,
    }
    resp = client.post("/api/v1/predictions/shipment-price", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["predicted_price"] > 0
    assert data["currency"] == "INR"
    assert data["quote_comparison"] is not None


def test_fastapi_prediction_endpoint_reference_model():
    """Verify POST /api/v1/predictions/shipment-price returns 200 for reference sculpture model."""
    client = TestClient(app)
    payload = {
        "origin_pin": "110001",
        "destination_pin": "400001",
        "length_cm": 30.0,
        "width_cm": 25.0,
        "height_cm": 20.0,
        "parcel_weight_kg": 5.0,
        "model_type": "reference_sculpture",
    }
    resp = client.post("/api/v1/predictions/shipment-price", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["predicted_price"] > 0
    assert data["currency"] == "USD"
    assert "ReferenceSculpture" in data["model_name"]


def test_fastapi_prediction_endpoint_invalid_input():
    """Verify 422 Unprocessable Entity on invalid inputs."""
    client = TestClient(app)
    payload = {
        "origin_pin": "110001",
        "destination_pin": "400001",
        "length_cm": -5.0,  # Negative length
        "width_cm": 15.0,
        "height_cm": 10.0,
        "parcel_weight_kg": 1.5,
    }
    resp = client.post("/api/v1/predictions/shipment-price", json=payload)
    assert resp.status_code == 422
