"""
Tests for Adaptive Shipping Intelligence Agent Correctness Improvements:
1. Interactive Alternative Courier Recommendations & Approval Invalidation
2. Verified Indian PIN Code Validation (4-tier classification & address consistency)
3. Manual Sandbox Entry Mode Complete Removal
4. Courier Rates Page Ordering (Recommended Courier -> Why Recommended -> ML Benchmark)
"""

import pytest
from fastapi.testclient import TestClient

from src.server import app
from src.domain.pincode_database import (
    evaluate_pin_code_full,
    lookup_verified_pin,
    validate_indian_pin_format,
    check_address_pin_consistency,
    PinValidationStatus,
)
from src.domain.advisor import find_alternative_recommendations
from src.domain.models import (
    Address,
    Dimensions,
    OrderInput,
    PackageType,
    CourierQuote,
    CostBreakdown,
)
from src.providers.registry import (
    get_provider_status,
    set_active_provider_mode,
    get_active_provider_mode,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_quotes():
    """Create a set of realistic courier quotes for testing alternative recommendations."""
    return [
        CourierQuote(
            quote_id="Q-AIR-EXPRESS",
            courier_code="DEMO_AIR_EXPRESS",
            courier_name="Blue Dart Express (Simulated)",
            service_type="Air Express Priority",
            estimated_delivery_days=1,
            is_serviceable=True,
            is_eligible=True,
            input_hash="hash-air",
            cost_breakdown=CostBreakdown(
                base_freight=200.0,
                fuel_surcharge=24.0,
                fuel_surcharge_rate_pct=12.0,
                cod_fee=0.0,
                handling_fee=15.0,
                subtotal_before_tax=239.0,
                tax_amount=43.02,
                tax_rate_pct=18.0,
                total_cost=282.02,
            ),
        ),
        CourierQuote(
            quote_id="Q-SURFACE-STD",
            courier_code="DEMO_SURFACE_STD",
            courier_name="Delhivery Surface (Simulated)",
            service_type="Surface Ground Economy",
            estimated_delivery_days=3,
            is_serviceable=True,
            is_eligible=True,
            input_hash="hash-surface",
            cost_breakdown=CostBreakdown(
                base_freight=95.0,
                fuel_surcharge=9.5,
                fuel_surcharge_rate_pct=10.0,
                cod_fee=0.0,
                handling_fee=10.0,
                subtotal_before_tax=114.5,
                tax_amount=20.61,
                tax_rate_pct=18.0,
                total_cost=135.11,
            ),
        ),
        CourierQuote(
            quote_id="Q-HEAVY-CARGO",
            courier_code="DEMO_ECONOMY_CARGO",
            courier_name="DTDC Heavy Cargo (Simulated)",
            service_type="Heavy Commercial Freight",
            estimated_delivery_days=4,
            is_serviceable=True,
            is_eligible=True,
            input_hash="hash-heavy",
            cost_breakdown=CostBreakdown(
                base_freight=120.0,
                fuel_surcharge=9.6,
                fuel_surcharge_rate_pct=8.0,
                cod_fee=0.0,
                handling_fee=25.0,
                subtotal_before_tax=154.6,
                tax_amount=27.83,
                tax_rate_pct=18.0,
                total_cost=182.43,
            ),
        ),
        CourierQuote(
            quote_id="Q-UNSERVICEABLE-CARRIER",
            courier_code="DEMO_SAME_DAY",
            courier_name="XpressBees Same-Day (Simulated)",
            service_type="Same-Day Metro Delivery",
            estimated_delivery_days=1,
            is_serviceable=False,
            is_eligible=False,
            unserviceable_reason="Destination PIN is outside same-day metro delivery radius.",
            input_hash="hash-unserv",
            cost_breakdown=CostBreakdown(
                base_freight=350.0,
                fuel_surcharge=35.0,
                fuel_surcharge_rate_pct=10.0,
                cod_fee=0.0,
                handling_fee=20.0,
                subtotal_before_tax=405.0,
                tax_amount=72.9,
                tax_rate_pct=18.0,
                total_cost=477.9,
            ),
        ),
    ]


# =============================================================================
# Requirement 2: Indian PIN Code Validation Tests
# =============================================================================

class TestIndianPinCodeValidation:
    """Rigorous verification of Indian PIN codes using postal database."""

    def test_valid_six_digit_pin_present_in_verified_database(self):
        """Standard valid Indian PIN codes must evaluate to VALID_PIN_SERVICEABLE."""
        for pin in ["110001", "110020", "400001", "560001", "600001", "700001"]:
            eval_res = evaluate_pin_code_full(pin)
            assert eval_res.is_valid_format is True
            assert eval_res.is_recognized_in_database is True
            assert eval_res.status == PinValidationStatus.VALID_PIN_SERVICEABLE
            assert eval_res.postal_info is not None

    def test_five_digit_pin_rejected_as_invalid_format(self):
        """Short PIN codes (e.g. 5 digits) must be classified as INVALID_FORMAT."""
        eval_res = evaluate_pin_code_full("11000")
        assert eval_res.is_valid_format is False
        assert eval_res.status == PinValidationStatus.INVALID_FORMAT
        assert "6 digits" in eval_res.message

    def test_seven_digit_pin_rejected_as_invalid_format(self):
        """Overlength PIN codes (e.g. 7 digits) must be classified as INVALID_FORMAT."""
        eval_res = evaluate_pin_code_full("1100010")
        assert eval_res.is_valid_format is False
        assert eval_res.status == PinValidationStatus.INVALID_FORMAT
        assert "6 digits" in eval_res.message

    def test_letters_and_special_characters_rejected_as_invalid_format(self):
        """Non-numeric characters, letters, or symbols must be classified as INVALID_FORMAT."""
        for bad_pin in ["11000A", "11-001", "110 01", "ABCDEF", "110@01"]:
            eval_res = evaluate_pin_code_full(bad_pin)
            assert eval_res.is_valid_format is False
            assert eval_res.status == PinValidationStatus.INVALID_FORMAT

    def test_pin_starting_with_zero_rejected(self):
        """Indian postal codes never start with digit 0."""
        eval_res = evaluate_pin_code_full("011001")
        assert eval_res.is_valid_format is False
        assert eval_res.status == PinValidationStatus.INVALID_FORMAT
        assert "never begin with 0" in eval_res.message

    def test_unknown_or_nonexistent_six_digit_pin(self):
        """
        6-digit values that belong to unallocated Indian postal circles
        must be classified as PIN_NOT_FOUND (not invalid format, not unserviceable).
        """
        for unknown_pin in ["100000", "291001", "350001", "540001", "860001"]:
            eval_res = evaluate_pin_code_full(unknown_pin)
            assert eval_res.is_valid_format is True
            assert eval_res.is_recognized_in_database is False
            assert eval_res.status == PinValidationStatus.PIN_NOT_FOUND
            assert "verified" in eval_res.message.lower()

    def test_valid_indian_pin_with_conflicting_address(self):
        """
        Address text must not override PIN, and conflicts between entered state
        and registered PIN postal circle must be flagged.
        """
        # PIN 110001 belongs to Delhi. Specifying Maharashtra is a conflict.
        eval_res = evaluate_pin_code_full("110001", state="Maharashtra")
        assert eval_res.is_valid_format is True
        assert eval_res.address_conflict is True
        assert "Address conflict detected" in eval_res.conflict_message
        assert "Delhi" in eval_res.conflict_message

    def test_valid_pin_with_no_courier_serviceability(self):
        """
        Valid PIN in Army Postal Service (Circle 9) or restricted area
        must be classified as VALID_PIN_UNSERVICEABLE, distinguishing it from invalid PIN.
        """
        eval_res = evaluate_pin_code_full("900001")
        assert eval_res.is_valid_format is True
        assert eval_res.is_recognized_in_database is True
        assert eval_res.status == PinValidationStatus.VALID_PIN_UNSERVICEABLE
        assert "Army Postal Service" in eval_res.message

    def test_backend_direct_bypass_rejected_with_422(self, client):
        """Submitting an invalid or nonexistent PIN directly to /api/rates must be rejected with HTTP 422."""
        payload = {
            "sender_name": "Test Hub",
            "sender_phone": "9876543210",
            "sender_pin": "12345",  # Invalid 5-digit PIN
            "recipient_name": "Recipient",
            "recipient_phone": "9123456780",
            "recipient_pin": "400001",
            "length_cm": 20.0,
            "width_cm": 15.0,
            "height_cm": 10.0,
            "parcel_weight_kg": 1.5,
        }
        res = client.post("/api/rates", json=payload)
        assert res.status_code == 422
        assert any("PIN" in str(e) for e in res.json().get("detail", {}).get("errors", []))

    def test_backend_unknown_pin_rejected_with_422(self, client):
        """Submitting an unallocated 6-digit PIN directly to /api/rates must be rejected with HTTP 422."""
        payload = {
            "sender_name": "Test Hub",
            "sender_phone": "9876543210",
            "sender_pin": "291001",  # Unallocated circle 29
            "recipient_name": "Recipient",
            "recipient_phone": "9123456780",
            "recipient_pin": "400001",
            "length_cm": 20.0,
            "width_cm": 15.0,
            "height_cm": 10.0,
            "parcel_weight_kg": 1.5,
        }
        res = client.post("/api/rates", json=payload)
        assert res.status_code == 422


# =============================================================================
# Requirement 1: Alternative Recommendation & Approval Invalidation Tests
# =============================================================================

class TestAlternativeRecommendations:
    """Verification of preference-based re-ranking, alternatives, and approval state invalidation."""

    def test_alternative_recommendations_lowest_cost(self, sample_quotes):
        """Lowest shipping cost preference must recommend the cheapest eligible carrier."""
        res = find_alternative_recommendations(
            sample_quotes,
            current_winner_id="Q-AIR-EXPRESS",
            preference_mode="lowest_cost",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.courier_code == "DEMO_SURFACE_STD"
        assert res.selected_recommendation.total_cost == 135.11
        assert "Lowest Shipping Cost" in res.preference_applied

    def test_alternative_recommendations_fastest(self, sample_quotes):
        """Fastest delivery preference must recommend the carrier with shortest SLA."""
        res = find_alternative_recommendations(
            sample_quotes,
            current_winner_id="Q-SURFACE-STD",
            preference_mode="fastest",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.courier_code == "DEMO_AIR_EXPRESS"
        assert res.selected_recommendation.estimated_delivery_days == 1
        assert "Fastest Delivery" in res.preference_applied

    def test_alternative_recommendations_preferred_carrier(self, sample_quotes):
        """Selecting a preferred carrier must recommend that carrier if eligible."""
        res = find_alternative_recommendations(
            sample_quotes,
            current_winner_id="Q-SURFACE-STD",
            preference_mode="preferred_courier",
            preferred_courier="DTDC",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.courier_code == "DEMO_ECONOMY_CARGO"
        assert "DTDC" in res.selected_recommendation.courier_name

    def test_alternative_recommendations_unserviceable_preferred_carrier_not_selected(self, sample_quotes):
        """An unserviceable carrier must never be recommended even if requested by user."""
        res = find_alternative_recommendations(
            sample_quotes,
            current_winner_id="Q-SURFACE-STD",
            preference_mode="preferred_courier",
            preferred_courier="XpressBees",  # DEMO_SAME_DAY is unserviceable
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.is_serviceable is True
        assert res.selected_recommendation.courier_code != "DEMO_SAME_DAY"
        assert "cannot be recommended because it is unserviceable" in res.decision_explanation

    def test_alternative_recommendations_no_eligible_alternatives(self):
        """When only one carrier is eligible, engine explains no alternatives exist."""
        single_quote = [
            CourierQuote(
                quote_id="Q-ONLY",
                courier_code="DEMO_AIR_EXPRESS",
                courier_name="Blue Dart Express (Simulated)",
                service_type="Air Express Priority",
                estimated_delivery_days=2,
                is_serviceable=True,
                is_eligible=True,
                input_hash="hash-only",
                cost_breakdown=CostBreakdown(
                    base_freight=200.0,
                    fuel_surcharge=20.0,
                    fuel_surcharge_rate_pct=10.0,
                    cod_fee=0.0,
                    handling_fee=0.0,
                    subtotal_before_tax=220.0,
                    tax_amount=39.6,
                    tax_rate_pct=18.0,
                    total_cost=259.6,
                ),
            )
        ]
        res = find_alternative_recommendations(
            single_quote,
            current_winner_id="Q-ONLY",
            preference_mode="next_best",
        )
        assert res.selected_recommendation is not None
        assert "No other eligible couriers available" in res.decision_explanation

    def test_api_alternative_recommendation_and_approval_invalidation(self, client):
        """
        POST /api/recommendations/alternative must re-rank quotes and signal
        that downstream operator approval is invalidated.
        """
        # First calculate rates to create an order in session cache
        rate_res = client.post(
            "/api/rates",
            json={
                "sender_name": "Hub",
                "sender_phone": "9876543210",
                "sender_pin": "110001",
                "recipient_name": "Recipient",
                "recipient_phone": "9123456780",
                "recipient_pin": "400001",
                "length_cm": 25.0,
                "width_cm": 20.0,
                "height_cm": 15.0,
                "parcel_weight_kg": 2.0,
            },
        )
        assert rate_res.status_code == 200
        order_ref = rate_res.json()["order"]["order_reference"]
        current_winner = rate_res.json()["selected_quote_id"]

        # Request alternative recommendation under fastest delivery
        alt_res = client.post(
            "/api/recommendations/alternative",
            json={
                "order_reference": order_ref,
                "current_recommended_quote_id": current_winner,
                "preference_mode": "fastest",
                "exclude_current": True,
            },
        )
        assert alt_res.status_code == 200
        data = alt_res.json()
        assert data["status"] == "SUCCESS"
        assert "alternative_result" in data
        assert data["approval_invalidated"] is True


# =============================================================================
# Requirement 3: Manual Sandbox Entry Removal Tests
# =============================================================================

class TestManualSandboxEntryRemoved:
    """Confirm Manual Sandbox Entry mode is completely removed from the application."""

    def test_provider_status_available_modes_excludes_manual_sandbox(self):
        """Active modes must only include demo and local_sandbox."""
        status = get_provider_status()
        assert "manual_sandbox" not in status["available_modes"]
        assert "demo" in status["available_modes"]
        assert "local_sandbox" in status["available_modes"]

    def test_set_active_provider_mode_rejects_manual_sandbox(self):
        """Attempting to select manual_sandbox must raise ValueError."""
        with pytest.raises(ValueError):
            set_active_provider_mode("manual_sandbox")

    def test_manual_sandbox_quote_endpoint_returns_410_gone(self, client):
        """POST /api/manual-sandbox/quote must return 410 Gone."""
        res = client.post("/api/manual-sandbox/quote", json={"order_reference": "TEST"})
        assert res.status_code == 410
        assert "removed" in res.json()["detail"].lower()

    def test_manual_sandbox_dashboard_export_endpoint_returns_410_gone(self, client):
        """POST /api/manual-sandbox/dashboard-export must return 410 Gone."""
        res = client.post("/api/manual-sandbox/dashboard-export", json={"order_reference": "TEST"})
        assert res.status_code == 410
        assert "removed" in res.json()["detail"].lower()


# =============================================================================
# Requirement 4: Courier Rates Page Ordering Tests
# =============================================================================

class TestCourierRatesPageOrdering:
    """Verify Courier Rates page display order: Recommended Courier -> Why Recommended -> ML Benchmark."""

    def test_html_courier_rates_page_ordering(self):
        """
        Verify the DOM structure order in static/index.html:
        1. Recommended Courier card (advisorCard / recommended-courier-card)
        2. Why This Courier Is Recommended card (whyRecommendedCard)
        3. Machine Learning Price Benchmark card (mlPredictionCard)
        """
        with open("static/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        idx_rec = html.find('id="advisorCard"')
        idx_why = html.find('id="whyRecommendedCard"')
        idx_ml = html.find('id="mlPredictionCard"')

        assert idx_rec != -1, "Recommended Courier card (#advisorCard) not found in index.html"
        assert idx_why != -1, "Why Recommended card (#whyRecommendedCard) not found in index.html"
        assert idx_ml != -1, "ML Prediction card (#mlPredictionCard) not found in index.html"

        # Strict ordering assertion: A < B < C
        assert idx_rec < idx_why, "Recommended Courier card must precede Why Recommended card"
        assert idx_why < idx_ml, "Why Recommended card must precede ML Benchmark card"

    def test_manual_sandbox_modals_removed_from_html(self):
        """Verify modalManualSandbox and modalDashboardExport are absent from static/index.html."""
        with open("static/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        assert 'id="modalManualSandbox"' not in html
        assert 'id="modalDashboardExport"' not in html
        assert 'value="manual_sandbox"' not in html
