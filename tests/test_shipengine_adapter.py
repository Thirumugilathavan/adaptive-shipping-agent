"""
Comprehensive unit and integration tests for ShipEngineAdapter.
Validates sandbox authentication, HTTP request payload modeling, rate normalization,
label purchase, tracking, voiding, and strict secret protection.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch
import httpx
import pytest

from src.domain.models import (
    Address,
    CostBreakdown,
    CourierQuote,
    Dimensions,
    OrderInput,
    PackageType,
)
from src.persistence.state_machine import ApprovalRecord
from src.providers.base import ShipmentResult, TrackingResult
from src.providers.registry import get_provider
from src.providers.shipengine_adapter import (
    ShipEngineAdapter,
    ShipEngineConnectionStatus,
    get_shipengine_api_key,
    mask_api_key,
    validate_sandbox_key,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_order():
    return OrderInput(
        order_reference="ORD-TEST-SE-001",
        sender=Address(
            name="Origin Sender",
            phone="9876543210",
            street_line1="123 Warehouse Rd",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560103",
            country="IN",
        ),
        recipient=Address(
            name="Destination Receiver",
            phone="9123456780",
            street_line1="456 Consumer St",
            city="Austin",
            state="TX",
            pin_code="78756",
            country="US",
        ),
        parcel_weight_kg=2.5,
        dimensions=Dimensions(length_cm=30.0, width_cm=20.0, height_cm=15.0),
        package_type=PackageType.BOX,
        is_cod=False,
    )


@pytest.fixture
def mock_rate_response_data():
    return {
        "rate_response": {
            "rates": [
                {
                    "rate_id": "se-rate-123456",
                    "carrier_id": "se-carrier-ups",
                    "carrier_code": "ups",
                    "carrier_friendly_name": "UPS Ground",
                    "service_type": "UPS Ground®",
                    "shipping_amount": {"currency": "usd", "amount": 14.85},
                    "other_amount": {"currency": "usd", "amount": 1.15},
                    "delivery_days": 3,
                    "estimated_delivery_date": "2026-10-25T23:00:00Z",
                },
                {
                    "rate_id": "se-rate-789012",
                    "carrier_id": "se-carrier-fedex",
                    "carrier_code": "fedex",
                    "carrier_friendly_name": "FedEx Express",
                    "service_type": "FedEx 2Day®",
                    "shipping_amount": {"currency": "usd", "amount": 22.50},
                    "other_amount": {"currency": "usd", "amount": 0.0},
                    "delivery_days": 2,
                    "estimated_delivery_date": "2026-10-24T23:00:00Z",
                },
            ],
            "invalid_rates": [],
            "errors": [],
        }
    }


# ---------------------------------------------------------------------------
# Key Validation & Masking Tests
# ---------------------------------------------------------------------------

def test_validate_sandbox_key_missing_and_empty():
    ok, msg = validate_sandbox_key(None)
    assert not ok
    assert "not configured" in msg.lower()

    ok, msg = validate_sandbox_key("")
    assert not ok

    ok, msg = validate_sandbox_key("   ")
    assert not ok


def test_validate_sandbox_key_refuses_production_keys():
    # Production keys without TEST_ prefix MUST be rejected
    ok, msg = validate_sandbox_key("LIVE_abc123456789")
    assert not ok
    assert "production credentials rejected" in msg.lower()

    ok, msg = validate_sandbox_key("PROD_se_secret_key_12345")
    assert not ok
    assert "production credentials rejected" in msg.lower()

    ok, msg = validate_sandbox_key("sk_live_9988776655")
    assert not ok
    assert "production credentials rejected" in msg.lower()


def test_validate_sandbox_key_too_short():
    ok, msg = validate_sandbox_key("TEST_12")
    assert not ok
    assert "malformed or too short" in msg.lower()


def test_validate_sandbox_key_accepts_valid_test_key():
    ok, msg = validate_sandbox_key("TEST_2K7lGkM3FkE7jB0qA9w")
    assert ok
    assert "valid" in msg.lower()


def test_mask_api_key_redaction():
    assert mask_api_key(None) == "NOT_CONFIGURED"
    assert mask_api_key("") == "NOT_CONFIGURED"
    assert mask_api_key("12345") == "••••••••"
    masked = mask_api_key("TEST_2K7lGkM3FkE7jB0qA9w")
    assert masked.startswith("TEST_")
    assert masked.endswith("qA9w")
    assert "••••••••" in masked
    assert "2K7lGkM3" not in masked  # Secret inner bytes redacted


def test_get_shipengine_api_key_from_env(monkeypatch):
    monkeypatch.setenv("SHIPENGINE_API_KEY", "TEST_env_key_12345678")
    key = get_shipengine_api_key()
    assert key == "TEST_env_key_12345678"


# ---------------------------------------------------------------------------
# Registry Integration
# ---------------------------------------------------------------------------

def test_provider_registration_shipengine():
    adapter = get_provider("shipengine")
    assert isinstance(adapter, ShipEngineAdapter)
    assert adapter.provider_code == "SHIPENGINE_SANDBOX"
    assert adapter.provider_name == "ShipEngine Sandbox API"
    assert adapter.is_demo is False

    adapter_alias = get_provider("shipengine_sandbox")
    assert isinstance(adapter_alias, ShipEngineAdapter)


# ---------------------------------------------------------------------------
# Connection Testing
# ---------------------------------------------------------------------------

def test_connection_unconfigured():
    adapter = ShipEngineAdapter(api_key=None)
    res = adapter.test_connection()
    assert res["connected"] is False
    assert res["status"] == ShipEngineConnectionStatus.NOT_CONFIGURED.value
    assert res["masked_key"] == "NOT_CONFIGURED"


def test_connection_invalid_credentials_rejected():
    adapter = ShipEngineAdapter(api_key="LIVE_prod_key_should_fail")
    res = adapter.test_connection()
    assert res["connected"] is False
    assert res["status"] == ShipEngineConnectionStatus.INVALID_CREDENTIALS.value
    assert "production" in res["message"].lower()


@patch("httpx.Client.get")
def test_connection_mocked_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "carriers": [
            {"carrier_id": "se-1", "carrier_code": "stamps_com", "friendly_name": "USPS via Stamps.com"},
            {"carrier_id": "se-2", "carrier_code": "ups", "friendly_name": "UPS"},
        ]
    }
    mock_get.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    res = adapter.test_connection()

    assert res["connected"] is True
    assert res["status"] == ShipEngineConnectionStatus.CONNECTED.value
    assert len(res["carriers"]) == 2
    assert "USPS via Stamps.com" in res["message"]


@patch("httpx.Client.get")
def test_connection_mocked_auth_failure(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized: Invalid API key"
    mock_get.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_bad_key_123456")
    res = adapter.test_connection()

    assert res["connected"] is False
    assert res["status"] == ShipEngineConnectionStatus.AUTHENTICATION_FAILED.value
    assert res["status_code"] == 401


@patch("httpx.Client.get")
def test_connection_mocked_timeout(mock_get):
    mock_get.side_effect = httpx.TimeoutException("Read timed out")

    adapter = ShipEngineAdapter(api_key="TEST_key_timeout_123456")
    res = adapter.test_connection()

    assert res["connected"] is False
    assert res["status"] == ShipEngineConnectionStatus.NETWORK_UNAVAILABLE.value
    assert "timed out" in res["message"].lower()


# ---------------------------------------------------------------------------
# Rate Discovery (POST /v1/rates)
# ---------------------------------------------------------------------------

@patch("httpx.Client.post")
def test_get_rates_mocked_success(mock_post, sample_order, mock_rate_response_data):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_rate_response_data
    mock_post.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    quotes = adapter.get_rates(sample_order)

    assert len(quotes) == 2
    q1 = quotes[0]
    assert q1.quote_id == "SE-se-rate-123456"
    assert q1.courier_code == "ups"
    assert "UPS Ground" in q1.courier_name
    assert q1.estimated_delivery_days == 3
    assert q1.is_serviceable is True
    assert q1.is_eligible is True
    assert q1.source_type == "SHIPENGINE_SANDBOX"
    assert q1.is_demo_data is False
    assert q1.cost_breakdown is not None
    assert q1.cost_breakdown.total_cost == 16.0  # 14.85 + 1.15
    assert q1.cost_breakdown.currency == "USD"

    # Verify request payload
    called_args, called_kwargs = mock_post.call_args
    sent_json = called_kwargs["json"]
    pkg = sent_json["shipment"]["packages"][0]
    assert pkg["weight"]["value"] == 2.5
    assert pkg["weight"]["unit"] == "kilogram"
    assert pkg["dimensions"]["length"] == 30.0
    assert pkg["dimensions"]["unit"] == "centimeter"


@patch("httpx.Client.post")
def test_get_rates_unserviceable_reported_without_silent_fallback(mock_post, sample_order):
    # ShipEngine returns 0 rates and an error message explaining carrier route failure
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "rate_response": {
            "rates": [],
            "invalid_rates": [
                {
                    "carrier_code": "stamps_com",
                    "error_messages": ["Carrier does not support shipping to destination country."],
                }
            ],
            "errors": [
                {"message": "No active carrier accounts could service this origin/destination pair."}
            ],
        }
    }
    mock_post.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    quotes = adapter.get_rates(sample_order)

    assert len(quotes) == 1
    unsvc = quotes[0]
    assert unsvc.is_serviceable is False
    assert unsvc.is_eligible is False
    assert unsvc.cost_breakdown is None  # Must NOT fabricate cost or fall back to zero
    assert "No active carrier accounts" in unsvc.unserviceable_reason
    assert unsvc.source_type == "SHIPENGINE_SANDBOX"
    assert unsvc.is_demo_data is False


@patch("httpx.Client.post")
def test_get_rates_rate_limit_429(mock_post, sample_order):
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_post.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        adapter.get_rates(sample_order)


@patch("httpx.Client.post")
def test_get_rates_auth_401(mock_post, sample_order):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_post.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    with pytest.raises(PermissionError, match="authentication failed"):
        adapter.get_rates(sample_order)


# ---------------------------------------------------------------------------
# Label Purchase (POST /v1/labels/rates/{rate_id})
# ---------------------------------------------------------------------------

@patch("httpx.Client.post")
def test_create_shipment_mocked_success(mock_post, sample_order):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "label_id": "se-lbl-998877",
        "status": "completed",
        "shipment_id": "se-shp-112233",
        "tracking_number": "1Z9999999999999999",
        "label_download": {
            "pdf": "https://api.shipengine.com/v1/downloads/10/mock-label.pdf",
            "href": "https://api.shipengine.com/v1/downloads/10/mock-label.pdf",
        },
        "carrier_id": "se-carrier-ups",
        "service_code": "ups_ground",
    }
    mock_post.return_value = mock_resp

    quote = CourierQuote(
        quote_id="SE-rate-123456",
        courier_code="ups",
        courier_name="UPS Ground (ShipEngine Sandbox)",
        service_type="UPS Ground®",
        is_serviceable=True,
        is_eligible=True,
        eligibility_status="ELIGIBLE",
        source_type="SHIPENGINE_SANDBOX",
        input_hash="hash-1234",
        is_demo_data=False,
    )
    approval = ApprovalRecord(
        order_reference=sample_order.order_reference,
        quote_id=quote.quote_id,
        courier_code=quote.courier_code,
        approved_by="Shipping Officer",
        total_cost=16.0,
    )

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    shipment_res = adapter.create_shipment(
        order=sample_order,
        quote=quote,
        approval=approval,
        idempotency_key="IDEM-TEST-123",
    )

    assert isinstance(shipment_res, ShipmentResult)
    assert shipment_res.shipment_id == "se-lbl-998877"
    assert shipment_res.tracking_number == "1Z9999999999999999"
    assert shipment_res.label_status == "AVAILABLE_PDF"
    assert shipment_res.label_url_or_data == "https://api.shipengine.com/v1/downloads/10/mock-label.pdf"
    assert shipment_res.is_demo_data is False
    assert "SHIPENGINE SANDBOX" in shipment_res.disclaimer


# ---------------------------------------------------------------------------
# Tracking & Cancellation
# ---------------------------------------------------------------------------

@patch("httpx.Client.get")
def test_get_tracking_mocked_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "tracking_number": "1Z9999999999999999",
        "status_code": "IT",
        "status_description": "In Transit",
        "estimated_delivery_date_time": "2026-10-25T18:00:00Z",
        "events": [
            {
                "occurred_at": "2026-10-23T10:00:00Z",
                "status": "ACCEPTED",
                "description": "Package accepted at sandbox carrier facility",
                "city_locality": "Austin",
            }
        ],
    }
    mock_get.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    res = adapter.get_tracking("1Z9999999999999999", carrier_code="ups")

    assert isinstance(res, TrackingResult)
    assert res.tracking_number == "1Z9999999999999999"
    assert res.current_status == "In Transit"
    assert len(res.events) == 1
    assert res.events[0].status == "ACCEPTED"
    assert res.is_demo_data is False


@patch("httpx.Client.put")
def test_cancel_shipment_mocked_success(mock_put):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_put.return_value = mock_resp

    adapter = ShipEngineAdapter(api_key="TEST_mock_key_123456")
    ok = adapter.cancel_shipment("se-lbl-998877")
    assert ok is True


# ---------------------------------------------------------------------------
# Opt-in Real API Live Test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.getenv("RUN_REAL_SHIPENGINE_TEST"),
    reason="Skipping live ShipEngine API test unless RUN_REAL_SHIPENGINE_TEST is enabled.",
)
def test_live_shipengine_sandbox_api():
    key = os.getenv("SHIPENGINE_API_KEY")
    assert key and key.startswith("TEST_"), "Live test requires a valid TEST_ sandbox key in SHIPENGINE_API_KEY"
    adapter = ShipEngineAdapter(api_key=key)
    conn = adapter.test_connection()
    assert conn["connected"] is True, f"Failed connecting to live ShipEngine sandbox: {conn['message']}"
