"""
Tests for INR Price Formatting, Alternative Recommendations Workflow, and Left Sidebar Navigation.

Verifies:
1. INR currency formatting rules (integers, floats, strings, negative, non-finite, missing).
2. End-to-end Alternative Recommendation engine supporting all 5 preferences.
3. Backend validation for preference_mode on POST /api/recommendations/alternative.
4. Approval invalidation when alternative recommendations change the active winner.
5. HTML structure and accessibility of the persistent Left Navigation Sidebar.
6. Preservation of the 4 nested subsections under Rate Shipping Dispatch.
"""

import pytest
import math
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup

from src.server import app, _ACTIVE_QUOTES_CACHE, _ACTIVE_ORDERS_CACHE
from src.ui.helpers import format_inr, fmt_inr
from src.domain.advisor import (
    find_alternative_recommendations,
    AlternativeCandidate,
    AlternativeRecommendationResult,
)
from src.domain.models import CourierQuote, CostBreakdown


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_test_quotes():
    """Create a diverse set of courier quotes for alternative preference evaluation."""
    return [
        CourierQuote(
            quote_id="Q-BD-AIR",
            courier_code="DEMO_AIR_EXPRESS",
            courier_name="Blue Dart Express (Simulated)",
            service_type="Air Express Priority",
            estimated_delivery_days=1,
            is_serviceable=True,
            is_eligible=True,
            input_hash="hash-bd",
            cost_breakdown=CostBreakdown(
                base_freight=210.0,
                fuel_surcharge=25.2,
                fuel_surcharge_rate_pct=12.0,
                cod_fee=0.0,
                handling_fee=15.0,
                subtotal_before_tax=250.2,
                tax_amount=45.04,
                tax_rate_pct=18.0,
                total_cost=295.24,
            ),
        ),
        CourierQuote(
            quote_id="Q-DLV-SURF",
            courier_code="DEMO_SURFACE_STD",
            courier_name="Delhivery Surface (Simulated)",
            service_type="Surface Ground Economy",
            estimated_delivery_days=4,
            is_serviceable=True,
            is_eligible=True,
            input_hash="hash-dlv",
            cost_breakdown=CostBreakdown(
                base_freight=80.0,
                fuel_surcharge=8.0,
                fuel_surcharge_rate_pct=10.0,
                cod_fee=0.0,
                handling_fee=10.0,
                subtotal_before_tax=98.0,
                tax_amount=17.64,
                tax_rate_pct=18.0,
                total_cost=115.64,
            ),
        ),
        CourierQuote(
            quote_id="Q-DTDC-CARGO",
            courier_code="DEMO_ECONOMY_CARGO",
            courier_name="DTDC Heavy Cargo (Simulated)",
            service_type="Heavy Commercial Freight",
            estimated_delivery_days=3,
            is_serviceable=True,
            is_eligible=True,
            input_hash="hash-dtdc",
            cost_breakdown=CostBreakdown(
                base_freight=130.0,
                fuel_surcharge=10.4,
                fuel_surcharge_rate_pct=8.0,
                cod_fee=0.0,
                handling_fee=20.0,
                subtotal_before_tax=160.4,
                tax_amount=28.87,
                tax_rate_pct=18.0,
                total_cost=189.27,
            ),
        ),
        CourierQuote(
            quote_id="Q-IP-PARCEL",
            courier_code="DEMO_BUDGET_SAVER",
            courier_name="India Post Parcel (Simulated)",
            service_type="National Registered Parcel",
            estimated_delivery_days=5,
            is_serviceable=True,
            is_eligible=True,
            input_hash="hash-ip",
            cost_breakdown=CostBreakdown(
                base_freight=90.0,
                fuel_surcharge=0.0,
                fuel_surcharge_rate_pct=0.0,
                cod_fee=0.0,
                handling_fee=5.0,
                subtotal_before_tax=95.0,
                tax_amount=17.1,
                tax_rate_pct=18.0,
                total_cost=112.10,
            ),
        ),
        CourierQuote(
            quote_id="Q-XPRESS-UNSVC",
            courier_code="DEMO_SAME_DAY",
            courier_name="XpressBees Same-Day (Simulated)",
            service_type="Same-Day Metro Delivery",
            estimated_delivery_days=1,
            is_serviceable=False,
            is_eligible=False,
            unserviceable_reason="Destination PIN is outside same-day metro delivery radius.",
            input_hash="hash-xb",
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
# PART 1: INR Price Formatting Tests
# =============================================================================

class TestInrPriceFormatting:
    def test_format_inr_integers_and_floats(self):
        """Standard valid numbers must be formatted with the rupee symbol and 2 decimal precision."""
        assert format_inr(124) == "₹124.00"
        assert format_inr(124.0) == "₹124.00"
        assert format_inr(124.5) == "₹124.50"
        assert format_inr(2548.75) == "₹2,548.75"

    def test_format_inr_valid_numeric_strings(self):
        """Numeric strings must be safely converted and formatted."""
        assert format_inr("124") == "₹124.00"
        assert format_inr("124.50") == "₹124.50"
        assert format_inr(" 99.99 ") == "₹99.99"

    def test_format_inr_missing_malformed_negative_nonfinite(self):
        """Invalid, negative, missing or non-finite numbers must yield fallback, never fabricated ₹0."""
        assert format_inr(None) == "Unavailable"
        assert format_inr("") == "Unavailable"
        assert format_inr("not-a-number") == "Unavailable"
        assert format_inr(-10.5) == "Unavailable"
        assert format_inr(float("nan")) == "Unavailable"
        assert format_inr(float("inf")) == "Unavailable"
        assert format_inr(float("-inf")) == "Unavailable"

    def test_quote_cost_breakdown_contains_inr_currency(self, sample_test_quotes):
        """Domain models must specify currency as INR."""
        for q in sample_test_quotes:
            assert q.cost_breakdown.currency == "INR"

    def test_alternative_candidates_include_cost_breakdown(self, sample_test_quotes):
        """Alternative candidates must attach cost_breakdown and currency."""
        res = find_alternative_recommendations(
            sample_test_quotes,
            current_winner_id="Q-BD-AIR",
            preference_mode="lowest_cost",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.currency == "INR"
        assert res.selected_recommendation.cost_breakdown is not None
        assert res.selected_recommendation.cost_breakdown.total_cost == 112.10


# =============================================================================
# PART 2: Alternative Recommendations & Apply Preference Tests
# =============================================================================

class TestAlternativeRecommendationsWorkflow:
    def test_preference_lowest_cost(self, sample_test_quotes):
        """Lowest shipping cost preference picks India Post (₹112.10)."""
        res = find_alternative_recommendations(
            sample_test_quotes,
            current_winner_id="Q-BD-AIR",
            preference_mode="lowest_cost",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.courier_code == "DEMO_BUDGET_SAVER"
        assert res.selected_recommendation.total_cost == 112.10
        assert "₹" in res.decision_explanation

    def test_preference_fastest_delivery(self, sample_test_quotes):
        """Fastest delivery preference picks Blue Dart (1 day transit)."""
        res = find_alternative_recommendations(
            sample_test_quotes,
            current_winner_id="Q-DLV-SURF",
            preference_mode="fastest",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.courier_code == "DEMO_AIR_EXPRESS"
        assert res.selected_recommendation.estimated_delivery_days == 1

    def test_preference_balanced(self, sample_test_quotes):
        """Balanced cost & speed uses composite 60/40 weighting."""
        res = find_alternative_recommendations(
            sample_test_quotes,
            current_winner_id="Q-BD-AIR",
            preference_mode="balanced",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.is_serviceable is True

    def test_preference_preferred_courier(self, sample_test_quotes):
        """Preferred courier mode prioritizes requested carrier if eligible."""
        res = find_alternative_recommendations(
            sample_test_quotes,
            current_winner_id="Q-BD-AIR",
            preference_mode="preferred_courier",
            preferred_courier="DTDC",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.courier_code == "DEMO_ECONOMY_CARGO"
        assert "DTDC" in res.selected_recommendation.courier_name

    def test_preference_preferred_courier_unserviceable_fallback(self, sample_test_quotes):
        """Unserviceable carrier must not be recommended; explains reason and picks best alternative."""
        res = find_alternative_recommendations(
            sample_test_quotes,
            current_winner_id="Q-BD-AIR",
            preference_mode="preferred_courier",
            preferred_courier="XpressBees",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.courier_code != "DEMO_SAME_DAY"
        assert "cannot be recommended because it is unserviceable" in res.decision_explanation

    def test_preference_next_best_alternative(self, sample_test_quotes):
        """Next best alternative excludes current winner."""
        res = find_alternative_recommendations(
            sample_test_quotes,
            current_winner_id="Q-IP-PARCEL",
            preference_mode="next_best",
        )
        assert res.selected_recommendation is not None
        assert res.selected_recommendation.quote_id != "Q-IP-PARCEL"

    def test_backend_direct_request_invalid_mode_rejected_with_422(self, client):
        """Direct API request with invalid preference_mode must be rejected with HTTP 422."""
        res = client.post(
            "/api/recommendations/alternative",
            json={
                "order_reference": "TEST-INVALID-MODE",
                "preference_mode": "invalid_mode_bypass",
            },
        )
        assert res.status_code == 422
        assert "Invalid preference_mode" in res.text

    def test_api_recommendation_change_signals_approval_invalidation(self, client):
        """When an alternative recommendation is applied, approval_invalidated must be True."""
        # 1. Seed rate shopping order
        rate_res = client.post(
            "/api/rates",
            json={
                "sender_name": "Source Hub",
                "sender_pin": "110001",
                "recipient_name": "Dest Hub",
                "recipient_pin": "400001",
                "length_cm": 25.0,
                "width_cm": 20.0,
                "height_cm": 15.0,
                "parcel_weight_kg": 2.0,
                "package_type": "box",
                "ranking_mode": "lowest_cost",
                "provider_mode": "demo",
            },
        )
        assert rate_res.status_code == 200
        order_ref = rate_res.json()["order"]["order_reference"]
        first_winner_id = rate_res.json()["selected_quote_id"]

        # 2. Request alternative under fastest delivery
        alt_res = client.post(
            "/api/recommendations/alternative",
            json={
                "order_reference": order_ref,
                "current_recommended_quote_id": first_winner_id,
                "preference_mode": "fastest",
                "exclude_current": True,
            },
        )
        assert alt_res.status_code == 200
        data = alt_res.json()
        assert data["status"] == "SUCCESS"
        assert data["approval_invalidated"] is True
        assert "Renewed operator approval is required" in data["notice"]


# =============================================================================
# PART 3: Left Navigation Sidebar Structure & Tests
# =============================================================================

class TestLeftNavigationSidebar:
    def test_sidebar_contains_all_four_primary_navigation_items(self, client):
        """Index HTML must include all four primary navigation items with exact labels."""
        res = client.get("/")
        assert res.status_code == 200
        soup = BeautifulSoup(res.text, "html.parser")

        sidebar = soup.find("aside", id="appSidebar")
        assert sidebar is not None, "appSidebar element not found"

        # Check the 4 primary items
        rates_nav = sidebar.find(id="navItemRates")
        assert rates_nav is not None
        assert "Rate Shipping Dispatch" in rates_nav.text

        eval_nav = sidebar.find(id="navItemEvaluator")
        assert eval_nav is not None
        assert "Official Test Cases" in eval_nav.text

        pkg_nav = sidebar.find(id="navItemPackaging")
        assert pkg_nav is not None
        assert "Packaging Optimizer" in pkg_nav.text

        audit_nav = sidebar.find(id="navItemAudit")
        assert audit_nav is not None
        assert "Security Audit Logs" in audit_nav.text

    def test_sidebar_contains_four_nested_subsections_expanded_by_default(self, client):
        """Rate Shipping Dispatch must be expanded by default and list its 4 exact child subsections."""
        res = client.get("/")
        assert res.status_code == 200
        soup = BeautifulSoup(res.text, "html.parser")

        rates_group = soup.find(id="sidebarGroupRates")
        assert rates_group is not None
        assert "expanded" in rates_group.get("class", [])

        subsections = soup.find(id="ratesSubsections")
        assert subsections is not None

        # Verify exact 4 child subsections
        step1 = subsections.find(id="navSubStep1")
        assert step1 is not None
        assert "Shipment Details" in step1.text

        step2 = subsections.find(id="navSubStep2")
        assert step2 is not None
        assert "Carrier Rates" in step2.text

        step3 = subsections.find(id="navSubStep3")
        assert step3 is not None
        assert "Approval Gate" in step3.text

        step4 = subsections.find(id="navSubStep4")
        assert step4 is not None
        assert "Shipping Label" in step4.text

    def test_mobile_toggle_and_overlay_present(self, client):
        """Responsive drawer elements (toggle button, overlay, close button) must be present."""
        res = client.get("/")
        assert res.status_code == 200
        soup = BeautifulSoup(res.text, "html.parser")

        assert soup.find(id="sidebarToggle") is not None
        assert soup.find(id="sidebarCloseBtn") is not None
        assert soup.find(id="sidebarOverlay") is not None

    def test_explore_other_recommendations_has_all_five_preference_chips(self, client):
        """Alternative recommendation panel must provide chips for all 5 preferences."""
        res = client.get("/")
        assert res.status_code == 200
        soup = BeautifulSoup(res.text, "html.parser")

        panel = soup.find(id="altRecommendationsPanel")
        assert panel is not None

        chips = panel.find_all(class_="btn-pref-chip")
        chip_prefs = [c.get("data-pref") for c in chips]
        assert "lowest_cost" in chip_prefs
        assert "fastest" in chip_prefs
        assert "balanced" in chip_prefs
        assert "preferred_courier" in chip_prefs
        assert "next_best" in chip_prefs

        assert panel.find(id="btnApplyAltPreference") is not None
        assert panel.find(id="selPreferredCourier") is not None

    def test_api_quotes_hydration_from_request_body(self, client, sample_test_quotes):
        """When cache or DB has no quotes, payload quotes must be hydrated and evaluated."""
        quote_dicts = [q.model_dump() for q in sample_test_quotes]
        res = client.post(
            "/api/recommendations/alternative",
            json={
                "order_reference": "ORDER-STATELESS-COLD-START",
                "current_recommended_quote_id": "Q-BD-AIR",
                "preference_mode": "lowest_cost",
                "quotes": quote_dicts,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SUCCESS"
        assert data["selected_quote_id"] == "Q-IP-PARCEL"
        assert data["alternative_result"]["selected_recommendation"]["courier_name"] == "India Post Parcel (Simulated)"

    def test_persistent_layout_css_rules_in_style_css(self):
        """Verify CSS contains the locked layout, sticky header, and independent scroll containers."""
        with open("static/css/style.css", "r", encoding="utf-8") as f:
            css = f.read()

        # HTML/Body locked
        assert "overflow: hidden" in css
        # App Header pinned
        assert ".app-header" in css
        assert "z-index: 100" in css
        # App layout flex container
        assert ".app-layout" in css
        # Sidebar locked height and independent scroll
        assert ".app-sidebar" in css
        assert "overflow-y: auto" in css
        # Main container independent scroll
        assert ".main-container" in css
        assert "height: 100%" in css
        # Mobile drawer stacking
        assert "z-index: 950" in css
        assert "z-index: 900" in css

