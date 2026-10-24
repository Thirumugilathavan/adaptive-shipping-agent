"""
Comprehensive automated tests for PIN serviceability validation,
transparent recommendation explanations, and tool orchestration safeguards.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.domain.agent_tools import (
    execute_validate_inputs_tool,
    execute_check_pin_serviceability_tool,
    execute_calculate_billable_weight_tool,
    execute_rank_and_explain_recommendations_tool,
    dispatch_tool,
)
from src.domain.advisor import build_advisor_recommendation, UserShippingPreferences
from src.domain.models import (
    Address,
    Dimensions,
    OrderInput,
    PackageType,
    CourierQuote,
    CostBreakdown,
    WeightResult,
)
from src.domain.ranking import rank_couriers, RankingMode, RankingPreferences
from src.providers.demo_adapter import DemoShippingAdapter, ALL_DEMO_COURIERS
from src.server import app, _ACTIVE_QUOTES_CACHE, _ACTIVE_ORDERS_CACHE


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def sample_serviceable_order():
    return OrderInput(
        order_reference="ORD-TEST-SVC-01",
        sender=Address(
            name="Alpha Sender",
            street_line1="100 Logistics Park",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560001",
            phone="9876543210",
        ),
        recipient=Address(
            name="Beta Receiver",
            street_line1="200 Delivery Rd",
            city="Mumbai",
            state="Maharashtra",
            pin_code="400001",
            phone="9123456780",
        ),
        dimensions=Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0),
        parcel_weight_kg=1.5,
        package_type=PackageType.BOX,
        is_cod=False,
        cod_amount=0.0,
    )


@pytest.fixture
def remote_pin_order():
    return OrderInput(
        order_reference="ORD-TEST-REMOTE-01",
        sender=Address(
            name="Alpha Sender",
            street_line1="100 Logistics Park",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560001",
            phone="9876543210",
        ),
        recipient=Address(
            name="Remote Receiver",
            street_line1="Hilltop Post",
            city="Itanagar",
            state="Arunachal Pradesh",
            pin_code="791111",
            phone="9123456780",
        ),
        dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        parcel_weight_kg=2.0,
        package_type=PackageType.BOX,
        is_cod=False,
        cod_amount=0.0,
    )


# -----------------------------------------------------------------------------
# 1. PIN Format & Serviceability Classification Tool Tests
# -----------------------------------------------------------------------------

def test_pin_serviceability_tool_valid_format():
    """Verify tool classifies standard 6-digit Indian PINs correctly."""
    res = execute_check_pin_serviceability_tool({
        "origin_pin": "560001",
        "destination_pin": "400001",
    })
    assert res["status"] == "SUCCESS"
    assert res["classification"] == "SERVICEABLE"
    assert res["is_serviceable"] is True
    assert len(res["serviceable_couriers"]) > 0
    assert "No live carrier network was queried" in res["disclaimer"]


def test_pin_serviceability_tool_malformed_pins():
    """Verify malformed PIN codes are flagged with INVALID_FORMAT without crashing."""
    malformed_cases = ["5600", "56000A", "012345", "", "5600012", "ABCDEF"]
    for pin in malformed_cases:
        res = execute_check_pin_serviceability_tool({
            "origin_pin": "560001",
            "destination_pin": pin,
        })
        assert res["classification"] == "INVALID_FORMAT", f"Expected INVALID_FORMAT for {pin}"
        assert res["is_serviceable"] is False


def test_pin_serviceability_tool_military_aps_pin():
    """Verify circle 9 (Army Postal Service) PIN is classified as UNSERVICEABLE_NETWORK."""
    res = execute_check_pin_serviceability_tool({
        "origin_pin": "560001",
        "destination_pin": "962111",
    })
    assert res["classification"] in ("UNSERVICEABLE_NETWORK", "CARRIER_UNSERVICEABLE")
    assert res["is_serviceable"] is False
    assert "Army Postal Service" in res["message"] or "Circle 9" in res["message"]


def test_pin_serviceability_tool_remote_prefix():
    """Verify remote prefix '79' is detected and flagged."""
    res = execute_check_pin_serviceability_tool({
        "origin_pin": "560001",
        "destination_pin": "791111",
        "courier_codes": ["DEMO_AIR_EXPRESS"],
    })
    assert "DEMO_AIR_EXPRESS" in res["unserviceable_couriers"]
    assert res["is_serviceable"] is False
    assert any("remote" in r.lower() for r in res["unserviceable_reasons"].values())


# -----------------------------------------------------------------------------
# 2. Ranking Serviceability Gating Tests
# -----------------------------------------------------------------------------

def test_remote_destination_excludes_air_express_before_ranking(remote_pin_order):
    """
    Ensure unserviceable couriers are excluded BEFORE ranking.
    Air Express cannot service prefix 79; Surface Standard can.
    Winner must be Surface Standard; Air Express must be in ineligible list.
    """
    adapter = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
    quotes = adapter.get_rates(remote_pin_order)

    air_q = next(q for q in quotes if q.courier_code == "DEMO_AIR_EXPRESS")
    assert air_q.is_serviceable is False
    assert air_q.cost_breakdown is None

    ranking = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))
    assert ranking.selected_winner is not None
    assert ranking.selected_winner.quote.courier_code == "DEMO_SURFACE_STD"

    # Air Express must be in ineligible candidates with explicit reason
    ineligible_codes = [c.courier_code for c in ranking.ineligible_candidates]
    assert "DEMO_AIR_EXPRESS" in ineligible_codes
    assert "DEMO_AIR_EXPRESS" in ranking.loss_reasons
    assert any("remote" in r.lower() for r in ranking.loss_reasons["DEMO_AIR_EXPRESS"])


def test_cheapest_unserviceable_courier_never_wins():
    """
    Even if an unserviceable courier has an artificially lower cost,
    it must NEVER be selected as the winner or recommended pick.
    """
    q_unserviceable_cheap = CourierQuote(
        quote_id="QUOTE-CHEAP-UNSVC",
        courier_code="UNSVC_CHEAP",
        courier_name="Unserviceable Ultra Cheap",
        service_type="Air Express",
        is_serviceable=False,
        is_eligible=False,
        input_hash="hash-unsvc-cheap",
        weight_result=WeightResult(
            actual_weight_kg=1.0,
            volumetric_weight_kg=1.0,
            chargeable_weight_kg=1.0,
            billing_slab_kg=0.5,
            divisor_used=5000.0,
            is_volumetric_penalty=False,
        ),
        cost_breakdown=CostBreakdown(
            base_freight=1.0,
            fuel_surcharge=0.1,
            fuel_surcharge_rate_pct=10.0,
            handling_fee=0.0,
            cod_fee=0.0,
            subtotal_before_tax=1.1,
            tax_amount=0.2,
            tax_rate_pct=18.0,
            total_cost=1.3,
        ),
        estimated_delivery_days=1,
    )
    q_serviceable = CourierQuote(
        quote_id="QUOTE-SVC-STANDARD",
        courier_code="DEMO_SURFACE_STD",
        courier_name="Demo Surface Standard",
        service_type="Surface Standard",
        is_serviceable=True,
        is_eligible=True,
        input_hash="hash-svc-standard",
        weight_result=WeightResult(
            actual_weight_kg=1.0,
            volumetric_weight_kg=1.0,
            chargeable_weight_kg=1.0,
            billing_slab_kg=0.5,
            divisor_used=4500.0,
            is_volumetric_penalty=False,
        ),
        cost_breakdown=CostBreakdown(
            base_freight=50.0,
            fuel_surcharge=4.0,
            fuel_surcharge_rate_pct=8.0,
            handling_fee=0.0,
            cod_fee=0.0,
            subtotal_before_tax=54.0,
            tax_amount=9.72,
            tax_rate_pct=18.0,
            total_cost=63.72,
        ),
        estimated_delivery_days=4,
    )

    ranking = rank_couriers([q_unserviceable_cheap, q_serviceable], RankingPreferences(mode=RankingMode.LOWEST_COST))
    assert ranking.selected_winner is not None
    assert ranking.selected_winner.quote.quote_id == "QUOTE-SVC-STANDARD"
    assert ranking.selected_winner.quote.courier_code != "UNSVC_CHEAP"


def test_all_couriers_unserviceable_produces_no_winner_and_no_fallback():
    """
    When all couriers are unserviceable, ranking must return selected_winner=None,
    and advisor must return primary_recommendation=None without inventing a fallback.
    """
    q_unserviceable = CourierQuote(
        quote_id="QUOTE-UNSVC-ONLY",
        courier_code="UNSVC_ONLY",
        courier_name="Unserviceable Carrier",
        service_type="Surface Standard",
        is_serviceable=False,
        is_eligible=False,
        input_hash="hash-unsvc-only",
        cost_breakdown=None,
    )

    ranking = rank_couriers([q_unserviceable], RankingPreferences(mode=RankingMode.LOWEST_COST))
    assert ranking.selected_winner is None
    assert ranking.total_eligible == 0
    assert ranking.total_ineligible == 1
    assert len(ranking.comparison_table) == 1
    assert ranking.comparison_table[0]["outcome_status"] == "EXCLUDED"

    advisor = build_advisor_recommendation([q_unserviceable], UserShippingPreferences())
    assert advisor.has_serviceable_couriers is False
    assert advisor.primary_recommendation is None
    assert advisor.best_compromise is None


# -----------------------------------------------------------------------------
# 3. Transparent Recommendation Explanations & Comparison Table Tests
# -----------------------------------------------------------------------------

def test_transparent_recommendation_explanations(sample_serviceable_order):
    """
    Verify complete transparency in rankings:
    - Winner reasons populated
    - Trade-off analysis populated
    - Loss reasons populated for all non-winning couriers
    - Comparison table has all evaluated couriers
    """
    adapter = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
    quotes = adapter.get_rates(sample_serviceable_order)
    ranking = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))

    assert ranking.selected_winner is not None
    assert len(ranking.winner_reasons) > 0
    assert ranking.trade_off_analysis != ""
    assert len(ranking.loss_reasons) == len(quotes)

    # Check comparison table format
    assert len(ranking.comparison_table) == len(quotes)
    winner_row = next(r for r in ranking.comparison_table if r["is_winner"])
    assert winner_row["outcome_status"] == "WINNER"
    assert winner_row["total_cost"] is not None
    assert winner_row["base_freight"] is not None
    assert winner_row["tax_amount"] is not None


# -----------------------------------------------------------------------------
# 4. Dispatch Gate Serviceability Protection Endpoint Tests
# -----------------------------------------------------------------------------

def test_dispatch_endpoint_blocks_unserviceable_quote(test_client, remote_pin_order):
    """
    Attempting to dispatch an unserviceable quote via POST /api/dispatch
    must be blocked with HTTP 400 Bad Request.
    """
    adapter = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
    quotes = adapter.get_rates(remote_pin_order)

    # Populate cache
    _ACTIVE_ORDERS_CACHE[remote_pin_order.order_reference] = remote_pin_order
    _ACTIVE_QUOTES_CACHE[remote_pin_order.order_reference] = quotes

    air_q = next(q for q in quotes if q.courier_code == "DEMO_AIR_EXPRESS")
    assert air_q.is_serviceable is False

    payload = {
        "order_reference": remote_pin_order.order_reference,
        "quote_id": air_q.quote_id,
        "courier_code": air_q.courier_code,
        "approved_by": "Test Operator",
        "total_cost": 0.0,
        "provider_mode": "demo",
    }

    res = test_client.post("/api/dispatch", json=payload)
    assert res.status_code == 400
    assert "unserviceable or ineligible" in res.json()["detail"].lower()


# -----------------------------------------------------------------------------
# 5. Modular Agent Tools Orchestration Tests
# -----------------------------------------------------------------------------

def test_agent_tools_dispatch_orchestration(sample_serviceable_order):
    """
    Test direct invocation of modular agent tools:
    - validate_inputs
    - check_pin_serviceability
    - calculate_billable_weight
    - rank_and_explain_recommendations
    """
    # Tool 1: validate_shipment_inputs
    v_res = dispatch_tool("validate_shipment_inputs", {
        "sender_pin": sample_serviceable_order.sender.pin_code,
        "recipient_pin": sample_serviceable_order.recipient.pin_code,
        "length_cm": sample_serviceable_order.dimensions.length_cm,
        "width_cm": sample_serviceable_order.dimensions.width_cm,
        "height_cm": sample_serviceable_order.dimensions.height_cm,
        "weight_kg": sample_serviceable_order.parcel_weight_kg,
    })
    assert v_res["status"] == "SUCCESS"
    assert v_res["is_valid"] is True

    # Tool 2: check_pin_serviceability
    s_res = dispatch_tool("check_pin_serviceability", {
        "origin_pin": sample_serviceable_order.sender.pin_code,
        "destination_pin": sample_serviceable_order.recipient.pin_code,
    })
    assert s_res["status"] == "SUCCESS"
    assert s_res["classification"] == "SERVICEABLE"

    # Tool 3: calculate_billable_weights
    w_res = dispatch_tool("calculate_billable_weights", {
        "actual_weight_kg": sample_serviceable_order.parcel_weight_kg,
        "length_cm": sample_serviceable_order.dimensions.length_cm,
        "width_cm": sample_serviceable_order.dimensions.width_cm,
        "height_cm": sample_serviceable_order.dimensions.height_cm,
    })
    assert w_res["status"] == "SUCCESS"
    assert "divisor_breakdowns" in w_res
    assert w_res["chargeable_weight_estimated"] >= sample_serviceable_order.parcel_weight_kg
