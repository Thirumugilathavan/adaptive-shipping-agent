"""
Automated test suite for Model 2 (CatBoost Courier Intelligence Engine)
Covers:
1. Model 2 artifact existence and integrity
2. Correct feature names, order, types, and PIN mapping (origin_pin/dest_pin -> pickup_pin/delivery_pin)
3. PIN lookup: positive synthetic matches and missing-PIN UNKNOWN behavior
4. Prediction output types and graceful error handling
5. CatBoost unavailable / missing model file behavior
6. Model 1 remains available, functional, and unaffected
7. Model predictions never override serviceability decisions or binding carrier quotes
8. Tool orchestration integration (predict_model2_intelligence & agent tools)
9. FastAPI REST endpoints for Model 2
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.domain.model2_adapter import (
    Model2Adapter,
    MODEL2_DIR,
    MODEL2_FEATURE_ORDER,
    MODEL2_KNOWN_COURIERS,
    RATE_MODEL_PATH,
    DELIVERY_MODEL_PATH,
    PIN_LOOKUP_PATH,
)
from src.domain.agent_tools import dispatch_tool
from src.server import app


@pytest.fixture
def test_client():
    return TestClient(app)


# -----------------------------------------------------------------------------
# 1. Artifact Existence & Metadata Integrity
# -----------------------------------------------------------------------------

def test_model2_artifacts_exist_in_project():
    """Verify that required Model 2 artifacts were copied into the project models directory."""
    assert MODEL2_DIR.is_dir(), f"Expected directory: {MODEL2_DIR}"
    assert RATE_MODEL_PATH.is_file(), f"Missing rate model: {RATE_MODEL_PATH}"
    assert DELIVERY_MODEL_PATH.is_file(), f"Missing delivery model: {DELIVERY_MODEL_PATH}"
    assert PIN_LOOKUP_PATH.is_file(), f"Missing PIN lookup: {PIN_LOOKUP_PATH}"

    # Verify metadata files
    rate_meta = MODEL2_DIR / "rate_model_metadata.json"
    delivery_meta = MODEL2_DIR / "delivery_model_metadata.json"
    assert rate_meta.is_file()
    assert delivery_meta.is_file()

    with open(rate_meta, "r", encoding="utf-8") as f:
        rm = json.load(f)
    assert rm["target"] == "cost_inr"
    assert rm["features"] == MODEL2_FEATURE_ORDER

    with open(delivery_meta, "r", encoding="utf-8") as f:
        dm = json.load(f)
    assert dm["target"] == "delivery_days"
    assert dm["features"] == MODEL2_FEATURE_ORDER


# -----------------------------------------------------------------------------
# 2. Input Feature Normalization & Mismatch Resolution
# -----------------------------------------------------------------------------

def test_feature_normalization_resolves_origin_dest_mismatch():
    """
    Ensure input normalization resolves origin_pin/dest_pin into
    pickup_pin/delivery_pin matching the exact CatBoost schema.
    """
    adapter = Model2Adapter()

    # Case A: using origin_pin and dest_pin
    raw_a = {
        "origin_pin": "560001",
        "dest_pin": "400001",
        "weight_kg": 2.5,
        "length_cm": 25.0,
        "width_cm": 20.0,
        "height_cm": 15.0,
        "courier_code": "DEMO_AIR_EXPRESS",
    }
    norm_a = adapter.normalize_inputs(raw_a)
    assert norm_a.pickup_pin == "560001"
    assert norm_a.delivery_pin == "400001"
    assert norm_a.weight_kg == 2.5
    assert norm_a.courier_name == "BlueDart"

    # Case B: using sender_pin and recipient_pin
    raw_b = {
        "sender_pin": "110001",
        "recipient_pin": "700001",
        "parcel_weight_kg": 1.2,
        "length_cm": 10.0,
        "width_cm": 10.0,
        "height_cm": 10.0,
        "courier_name": "CourierX",
    }
    norm_b = adapter.normalize_inputs(raw_b)
    assert norm_b.pickup_pin == "110001"
    assert norm_b.delivery_pin == "700001"
    assert norm_b.weight_kg == 1.2
    assert norm_b.courier_name == "CourierX"


# -----------------------------------------------------------------------------
# 3. PIN Lookup Serviceability Evidence Tests
# -----------------------------------------------------------------------------

def test_historical_pin_lookup_positive_match():
    """Verify that PINs present in historical lookup return SERVICEABLE_CONFIRMED."""
    adapter = Model2Adapter()
    # BlueDart includes 446092 and 445998 in synthetic dataset
    res = adapter.check_historical_pin_coverage("446092", "445998", "BlueDart")
    assert res.status == "SERVICEABLE_CONFIRMED"
    assert res.has_historical_coverage is True
    assert res.evidence_count == 2
    assert "synthetic historical" in res.disclaimer


def test_historical_pin_lookup_missing_pin_returns_unknown():
    """
    A missing PIN must return UNKNOWN, NOT confirmed unserviceable,
    because synthetic historical data does not record negative coverage.
    """
    adapter = Model2Adapter()
    res = adapter.check_historical_pin_coverage("999999", "111111", "BlueDart")
    assert res.status == "UNKNOWN"
    assert res.has_historical_coverage is False
    assert res.evidence_count == 0
    assert "synthetic historical" in res.disclaimer


# -----------------------------------------------------------------------------
# 4. Graceful Error Handling When CatBoost is Unavailable
# -----------------------------------------------------------------------------

def test_graceful_handling_when_catboost_missing():
    """
    When CatBoost is not installed, methods must return controlled error responses
    without throwing unhandled exceptions.
    """
    adapter = Model2Adapter()
    with patch.object(adapter, "is_catboost_available", return_value=False):
        price_res = adapter.predict_price({"origin_pin": "560001", "dest_pin": "400001", "weight_kg": 2.0})
        assert price_res.status == "FRAMEWORK_MISSING"
        assert price_res.predicted_price_inr is None
        assert "not installed" in price_res.error_message

        sla_res = adapter.predict_delivery_days({"origin_pin": "560001", "dest_pin": "400001", "weight_kg": 2.0})
        assert sla_res.status == "FRAMEWORK_MISSING"
        assert sla_res.predicted_days is None
        assert "not installed" in sla_res.error_message


def test_missing_model_file_handling():
    """When a model file does not exist, adapter returns MODEL_UNAVAILABLE gracefully."""
    fake_dir = Path("E:/nonexistent_models_dir")
    adapter = Model2Adapter(models_dir=fake_dir)
    with patch.object(adapter, "is_catboost_available", return_value=True):
        price_res = adapter.predict_price({"origin_pin": "560001", "dest_pin": "400001", "weight_kg": 2.0})
        assert price_res.status == "MODEL_UNAVAILABLE"


# -----------------------------------------------------------------------------
# 5. Simulated Live CatBoost Inference Pipeline
# -----------------------------------------------------------------------------

def test_inference_pipeline_with_mocked_catboost():
    """Verify inference pipeline execution, feature ordering, and predictions with mocked CatBoost."""
    adapter = Model2Adapter()

    mock_model = MagicMock()
    mock_model.predict.return_value = [185.50]

    with patch.object(adapter, "is_catboost_available", return_value=True):
        with patch.object(adapter, "_get_rate_model", return_value=mock_model):
            res = adapter.predict_price({
                "origin_pin": "560001",
                "dest_pin": "400001",
                "weight_kg": 2.0,
                "length_cm": 20.0,
                "width_cm": 15.0,
                "height_cm": 10.0,
                "courier_name": "BlueDart",
            })
            assert res.status == "SUCCESS"
            assert res.predicted_price_inr == 185.50
            assert res.is_provider_quote is False

            # Verify that model.predict was called with a DataFrame containing exact MODEL2_FEATURE_ORDER
            call_df = mock_model.predict.call_args[0][0]
            assert list(call_df.columns) == MODEL2_FEATURE_ORDER
            assert call_df["pickup_pin"].iloc[0] == "560001"
            assert call_df["delivery_pin"].iloc[0] == "400001"
            assert call_df["courier_name"].iloc[0] == "BlueDart"


# -----------------------------------------------------------------------------
# 6. Model 1 Independence & Preservation
# -----------------------------------------------------------------------------

def test_model1_remains_active_and_independent(test_client):
    """
    Ensure Model 1 (domestic courier & reference sculpture) remains functional
    and unchanged by the presence of Model 2.
    """
    payload = {
        "origin_pin": "560001",
        "destination_pin": "400001",
        "length_cm": 20.0,
        "width_cm": 15.0,
        "height_cm": 10.0,
        "parcel_weight_kg": 2.0,
        "package_type": "box",
        "courier_code": "DEMO_AIR_EXPRESS",
        "model_type": "domestic_courier",
    }
    res = test_client.post("/api/v1/predictions/shipment-price", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["currency"] == "INR"
    assert data["predicted_price"] is not None
    assert data["predicted_price"] > 0
    assert data["is_provider_quote"] is False


# -----------------------------------------------------------------------------
# 7. Agent Tool Orchestration for Model 2
# -----------------------------------------------------------------------------

def test_agent_tool_predict_model2_intelligence():
    """Verify invocation of Model 2 through the unified agent tool registry."""
    inputs = {
        "origin_pin": "560001",
        "destination_pin": "400001",
        "parcel_weight_kg": 2.0,
        "length_cm": 25.0,
        "width_cm": 20.0,
        "height_cm": 15.0,
        "courier_code": "DEMO_AIR_EXPRESS",
    }
    tool_res = dispatch_tool("predict_model2_intelligence", inputs)
    assert tool_res["tool_name"] == "predict_model2_intelligence"
    assert "price_prediction" in tool_res
    assert "delivery_prediction" in tool_res
    assert "pin_coverage" in tool_res
    assert "disclaimer" in tool_res


# -----------------------------------------------------------------------------
# 8. REST Endpoint for Model 2 Prediction
# -----------------------------------------------------------------------------

def test_model2_rest_endpoint(test_client):
    """Verify POST /api/v1/model2/predict endpoint returns structured response."""
    payload = {
        "origin_pin": "560001",
        "destination_pin": "400001",
        "length_cm": 25.0,
        "width_cm": 20.0,
        "height_cm": 15.0,
        "parcel_weight_kg": 2.0,
        "package_type": "box",
        "courier_code": "DEMO_AIR_EXPRESS",
    }
    res = test_client.post("/api/v1/model2/predict", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "price_prediction" in data
    assert "delivery_prediction" in data
    assert "pin_coverage" in data
    assert data["disclaimer"] != ""
