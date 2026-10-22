"""
Tests for Phase 6 UI helpers and approval/dispatch gating.
These tests exercise pure helper functions only — no Streamlit runtime required.
All existing 58 domain tests are unchanged.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from src.domain.models import (
    Address,
    CostBreakdown,
    Dimensions,
    OrderInput,
    PackageType,
    ShipmentStatus,
    WeightResult,
)
from src.domain.freshness import QuoteFreshnessStatus
from src.persistence.state_machine import ApprovalRecord
from src.ui.helpers import (
    build_order_input_from_form,
    describe_ranking_result,
    fmt_inr,
    fmt_kg,
    fmt_age,
    format_audit_event,
    format_cost_breakdown,
    format_packaging_recommendation,
    format_weight_breakdown,
    freshness_badge,
    friendly_courier_status,
    quote_source_badge,
    shipment_status_colour,
    validate_order_reference,
    validate_phone,
    validate_pin_code,
    validate_positive_float,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

VALID_FORM_DATA = {
    "order_reference": "ORD-TEST-001",
    "sender_name": "Alpha Electronics",
    "sender_phone": "9876543210",
    "sender_street": "42 Industrial Area",
    "sender_street2": "",
    "sender_city": "New Delhi",
    "sender_state": "Delhi",
    "sender_pin": "110020",
    "recipient_name": "Vikram Patel",
    "recipient_phone": "9123456780",
    "recipient_street": "Flat 402, Green Acres",
    "recipient_street2": "",
    "recipient_city": "Bengaluru",
    "recipient_state": "Karnataka",
    "recipient_pin": "560034",
    "parcel_weight_kg": 1.8,
    "length_cm": 35.0,
    "width_cm": 25.0,
    "height_cm": 20.0,
    "package_type": "box",
    "is_cod": False,
    "cod_amount": 0.0,
    "declared_value": 2500.0,
    "product_description": "Headphones",
}


def make_dummy_quote(
    is_demo: bool = True,
    total_cost: float = 350.0,
    retrieval_timestamp: str | None = None,
    is_serviceable: bool = True,
):
    """Build a minimal CourierQuote for helper tests."""
    from src.domain.models import CourierQuote

    ts = retrieval_timestamp or datetime.now(timezone.utc).isoformat()
    cb = CostBreakdown(
        base_freight=200.0,
        fuel_surcharge=24.0,
        fuel_surcharge_rate_pct=12.0,
        cod_fee=0.0,
        handling_fee=15.0,
        subtotal_before_tax=239.0,
        tax_amount=43.02,
        tax_rate_pct=18.0,
        total_cost=total_cost,
    )
    wr = WeightResult(
        actual_weight_kg=1.8,
        volumetric_weight_kg=3.5,
        chargeable_weight_kg=3.5,
        billing_slab_kg=4.0,
        divisor_used=5000.0,
        is_volumetric_penalty=True,
    )
    return CourierQuote(
        quote_id="QUOTE-TEST-001",
        courier_code="DEMO_AIR_EXPRESS",
        courier_name="Demo Air Express (Simulated)",
        service_type="Air Express Priority",
        estimated_delivery_days=2,
        is_serviceable=is_serviceable,
        is_eligible=is_serviceable,
        weight_result=wr,
        cost_breakdown=cb,
        retrieval_timestamp=ts,
        source_type="DEMO_ADAPTER",
        input_hash="abc123",
        is_demo_data=is_demo,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PIN Code Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatePinCode:
    def test_valid_pin(self):
        ok, msg = validate_pin_code("560034")
        assert ok is True
        assert msg == ""

    def test_empty_pin(self):
        ok, msg = validate_pin_code("")
        assert ok is False
        assert "required" in msg

    def test_short_pin(self):
        ok, msg = validate_pin_code("1234")
        assert ok is False

    def test_non_numeric_pin(self):
        ok, msg = validate_pin_code("56003A")
        assert ok is False

    def test_seven_digit_pin(self):
        ok, msg = validate_pin_code("5600340")
        assert ok is False

    def test_valid_pin_with_whitespace(self):
        ok, msg = validate_pin_code("  110020  ")
        assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# Phone Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatePhone:
    def test_valid_10_digit(self):
        ok, msg = validate_phone("9876543210")
        assert ok is True

    def test_valid_with_spaces(self):
        ok, msg = validate_phone("98765 43210")
        assert ok is True

    def test_short_phone(self):
        ok, msg = validate_phone("98765")
        assert ok is False

    def test_empty_phone(self):
        ok, msg = validate_phone("")
        assert ok is False
        assert "required" in msg

    def test_valid_15_digit(self):
        ok, msg = validate_phone("123456789012345")
        assert ok is True

    def test_too_long_phone(self):
        ok, msg = validate_phone("1234567890123456")
        assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# Order Reference Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateOrderReference:
    def test_valid_reference(self):
        ok, msg = validate_order_reference("ORD-2026-001")
        assert ok is True

    def test_too_short(self):
        ok, msg = validate_order_reference("AB")
        assert ok is False

    def test_too_long(self):
        ok, msg = validate_order_reference("A" * 65)
        assert ok is False

    def test_exactly_minimum(self):
        ok, msg = validate_order_reference("ABC")
        assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# Positive Float Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatePositiveFloat:
    def test_valid(self):
        ok, msg = validate_positive_float(1.5, "Weight")
        assert ok is True

    def test_zero(self):
        ok, msg = validate_positive_float(0.0, "Weight")
        assert ok is False

    def test_negative(self):
        ok, msg = validate_positive_float(-1.0, "Weight")
        assert ok is False

    def test_exceeds_max(self):
        ok, msg = validate_positive_float(1001.0, "Weight", max_val=1000.0)
        assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# OrderInput construction from form data
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildOrderInputFromForm:
    def test_valid_prepaid_order(self):
        order, errors = build_order_input_from_form(VALID_FORM_DATA)
        assert errors == []
        assert order is not None
        assert order.order_reference == "ORD-TEST-001"
        assert order.is_cod is False
        assert order.cod_amount == 0.0

    def test_valid_cod_order(self):
        data = {**VALID_FORM_DATA, "is_cod": True, "cod_amount": 1500.0}
        order, errors = build_order_input_from_form(data)
        assert errors == []
        assert order is not None
        assert order.is_cod is True
        assert order.cod_amount == 1500.0

    def test_cod_missing_amount(self):
        """COD=True but cod_amount=0 → domain model validation error"""
        data = {**VALID_FORM_DATA, "is_cod": True, "cod_amount": 0.0}
        order, errors = build_order_input_from_form(data)
        assert order is None
        assert len(errors) > 0

    def test_prepaid_with_cod_amount(self):
        """COD=False but cod_amount > 0 → domain model validation error"""
        data = {**VALID_FORM_DATA, "is_cod": False, "cod_amount": 500.0}
        order, errors = build_order_input_from_form(data)
        assert order is None
        assert len(errors) > 0

    def test_invalid_sender_pin(self):
        data = {**VALID_FORM_DATA, "sender_pin": "12345"}
        order, errors = build_order_input_from_form(data)
        assert order is None
        assert any("PIN" in e or "pin" in e.lower() for e in errors)

    def test_invalid_recipient_pin(self):
        data = {**VALID_FORM_DATA, "recipient_pin": "ABCDEF"}
        order, errors = build_order_input_from_form(data)
        assert order is None

    def test_zero_weight_rejected(self):
        data = {**VALID_FORM_DATA, "parcel_weight_kg": 0.0}
        order, errors = build_order_input_from_form(data)
        assert order is None

    def test_invalid_dimensions(self):
        data = {**VALID_FORM_DATA, "length_cm": 0.0}
        order, errors = build_order_input_from_form(data)
        assert order is None

    def test_short_order_reference_rejected(self):
        data = {**VALID_FORM_DATA, "order_reference": "AB"}
        order, errors = build_order_input_from_form(data)
        assert order is None


# ─────────────────────────────────────────────────────────────────────────────
# Display Formatting Helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatters:
    def test_fmt_inr(self):
        assert fmt_inr(350.25) == "INR 350.25"
        assert fmt_inr(1000.0) == "INR 1,000.00"

    def test_fmt_kg(self):
        assert fmt_kg(1.8) == "1.800 kg"

    def test_fmt_age_seconds(self):
        result = fmt_age(45.3)
        assert "s ago" in result

    def test_fmt_age_minutes(self):
        result = fmt_age(125.0)
        assert "m ago" in result

    def test_fmt_age_hours(self):
        result = fmt_age(3700.0)
        assert "h ago" in result

    def test_fmt_age_negative(self):
        result = fmt_age(-1.0)
        assert result == "Unknown"

    def test_format_cost_breakdown_keys(self):
        cb = CostBreakdown(
            base_freight=200.0,
            fuel_surcharge=24.0,
            fuel_surcharge_rate_pct=12.0,
            cod_fee=0.0,
            handling_fee=15.0,
            subtotal_before_tax=239.0,
            tax_amount=43.02,
            tax_rate_pct=18.0,
            total_cost=282.02,
        )
        result = format_cost_breakdown(cb)
        assert "Base Freight" in result
        assert "**Total Landed Cost**" in result
        assert "INR" in result["Base Freight"]

    def test_format_weight_breakdown_keys(self):
        wr = WeightResult(
            actual_weight_kg=1.8,
            volumetric_weight_kg=3.5,
            chargeable_weight_kg=3.5,
            billing_slab_kg=4.0,
            divisor_used=5000.0,
            is_volumetric_penalty=True,
        )
        result = format_weight_breakdown(wr)
        assert "Actual (Dead) Weight" in result
        assert "Billing Slab" in result
        assert "Volumetric Penalty" in result["Chargeable Weight"]


# ─────────────────────────────────────────────────────────────────────────────
# Freshness Badge
# ─────────────────────────────────────────────────────────────────────────────

class TestFreshnessBadge:
    def test_fresh_quote(self):
        quote = make_dummy_quote()
        label, colour = freshness_badge(quote, max_age_seconds=900)
        assert colour == "green"
        assert "Fresh" in label

    def test_expired_quote(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
        quote = make_dummy_quote(retrieval_timestamp=old_ts)
        label, colour = freshness_badge(quote, max_age_seconds=900)
        assert colour == "red"
        assert "Expired" in label

    def test_unverifiable_timestamp(self):
        quote = make_dummy_quote(retrieval_timestamp="not-a-timestamp")
        label, colour = freshness_badge(quote, max_age_seconds=900)
        assert colour == "orange"
        assert "Unverifiable" in label


# ─────────────────────────────────────────────────────────────────────────────
# Demo Data Labelling
# ─────────────────────────────────────────────────────────────────────────────

class TestDemoDataLabelling:
    def test_demo_quote_badge(self):
        quote = make_dummy_quote(is_demo=True)
        badge = quote_source_badge(quote)
        assert "DEMO DATA" in badge
        assert "Simulated" in badge

    def test_live_quote_badge(self):
        quote = make_dummy_quote(is_demo=False)
        badge = quote_source_badge(quote)
        assert "LIVE QUOTE" in badge
        assert "DEMO" not in badge

    def test_quote_has_demo_disclaimer(self):
        quote = make_dummy_quote(is_demo=True)
        assert quote.is_demo_data is True
        assert "DEMO" in quote.disclaimer.upper() or "demo" in quote.disclaimer.lower()

    def test_demo_data_flag_on_quote(self):
        """Verify the is_demo_data field that our UI reads is correctly set."""
        quote = make_dummy_quote(is_demo=True)
        assert quote.is_demo_data is True


class TestFriendlyCourierStatusAndPackaging:
    def test_winner_status(self):
        quote = make_dummy_quote()
        label, colour, explanation = friendly_courier_status(quote, is_winner=True)
        assert label == "Recommended"
        assert colour == "green"
        assert "Best verified option" in explanation

    def test_eligible_status(self):
        quote = make_dummy_quote()
        label, colour, explanation = friendly_courier_status(quote, is_winner=False)
        assert label == "Eligible"
        assert colour == "blue"

    def test_ineligible_status(self):
        quote = make_dummy_quote()
        quote.is_eligible = False
        quote.unserviceable_reason = "Remote PIN unserviceable"
        label, colour, explanation = friendly_courier_status(quote)
        assert label == "Not available for this parcel"
        assert colour == "red"
        assert "Remote PIN unserviceable" in explanation

    def test_unverified_status(self):
        quote = make_dummy_quote()
        quote.eligibility_status = "UNVERIFIED"
        label, colour, explanation = friendly_courier_status(quote)
        assert label == "Needs verification"
        assert colour == "orange"

    def test_price_unavailable_status(self):
        quote = make_dummy_quote()
        quote.cost_breakdown = None
        label, colour, explanation = friendly_courier_status(quote)
        assert label == "Price unavailable"
        assert colour == "gray"

    def test_packaging_recommendation_formatter(self):
        msg = format_packaging_recommendation("Demo Medium Box", savings_inr=45.50)
        assert "Recommended packaging:** Demo Medium Box" in msg
        assert "INR 45.50" in msg



# ─────────────────────────────────────────────────────────────────────────────
# Shipment Status Colour Mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestShipmentStatusColour:
    def test_created_is_green(self):
        assert shipment_status_colour(ShipmentStatus.CREATED) == "green"

    def test_uncertain_is_red(self):
        assert shipment_status_colour(ShipmentStatus.CREATION_UNCERTAIN) == "red"

    def test_pending_is_orange(self):
        assert shipment_status_colour(ShipmentStatus.PENDING_APPROVAL) == "orange"

    def test_failed_is_red(self):
        assert shipment_status_colour(ShipmentStatus.FAILED) == "red"

    def test_draft_is_gray(self):
        assert shipment_status_colour(ShipmentStatus.DRAFT) == "gray"


# ─────────────────────────────────────────────────────────────────────────────
# Audit Event Formatting
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatAuditEvent:
    def test_valid_json_details(self):
        import json
        row = {
            "event_type": "QUOTES_RECEIVED",
            "actor": "DemoAdapter",
            "order_reference": "ORD-001",
            "details_json": json.dumps({"count": 3}),
            "timestamp": "2026-10-18T00:00:00+00:00",
        }
        result = format_audit_event(row)
        assert result["event"] == "QUOTES_RECEIVED"
        assert result["actor"] == "DemoAdapter"
        assert result["order_ref"] == "ORD-001"
        assert "count" in result["details_preview"]

    def test_malformed_json_details(self):
        row = {
            "event_type": "ORDER_CREATED",
            "actor": "System",
            "order_reference": None,
            "details_json": "{broken json",
            "timestamp": "bad-timestamp",
        }
        result = format_audit_event(row)
        # Should not raise, falls back gracefully
        assert result["event"] == "ORDER_CREATED"
        assert result["order_ref"] == "—"


# ─────────────────────────────────────────────────────────────────────────────
# Approval Gating Logic (domain-level, not Streamlit)
# ─────────────────────────────────────────────────────────────────────────────

class TestApprovalGating:
    """
    Verify that the underlying state machine and ApprovalRecord correctly
    enforce human approval before dispatch can proceed — tested at domain level.
    """

    def test_approval_record_requires_name(self):
        """ApprovalRecord with empty name should fail Pydantic validation."""
        with pytest.raises(Exception):
            ApprovalRecord(
                approved_by="",
                quote_id="Q-001",
                total_cost=300.0,
            )

    def test_approval_record_requires_min_length(self):
        """ApprovalRecord name must be at least 2 characters."""
        with pytest.raises(Exception):
            ApprovalRecord(
                approved_by="A",
                quote_id="Q-001",
                total_cost=300.0,
            )

    def test_valid_approval_record(self):
        approval = ApprovalRecord(
            approved_by="Operations Lead",
            quote_id="Q-001",
            total_cost=350.0,
            notes="Test approval",
        )
        assert approval.approved_by == "Operations Lead"
        assert approval.total_cost == 350.0

    def test_state_machine_blocks_dispatch_without_approval(self):
        """FSM must reject PENDING_APPROVAL → DISPATCHING without an ApprovalRecord."""
        from src.persistence.state_machine import HumanApprovalRequiredError, ShipmentStateMachine
        fsm = ShipmentStateMachine()
        with pytest.raises(HumanApprovalRequiredError):
            fsm.validate_transition(
                ShipmentStatus.PENDING_APPROVAL,
                ShipmentStatus.DISPATCHING,
                approval=None,
            )

    def test_state_machine_allows_dispatch_with_approval(self):
        """FSM must allow PENDING_APPROVAL → DISPATCHING with a valid ApprovalRecord."""
        from src.persistence.state_machine import ShipmentStateMachine
        fsm = ShipmentStateMachine()
        approval = ApprovalRecord(
            approved_by="Operations Lead",
            quote_id="Q-001",
            total_cost=350.0,
        )
        # Should not raise
        fsm.validate_transition(
            ShipmentStatus.PENDING_APPROVAL,
            ShipmentStatus.DISPATCHING,
            approval=approval,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Integration: Full order-to-quote-to-ranking flow (no Streamlit)
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderToRankingFlow:
    """End-to-end smoke test through the domain using realistic form data."""

    def test_build_order_and_get_quotes(self):
        order, errors = build_order_input_from_form(VALID_FORM_DATA)
        assert errors == []
        assert order is not None

        from src.providers.registry import get_provider
        from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers

        provider = get_provider("demo")
        quotes = provider.get_rates(order)
        assert len(quotes) >= 1

        # All demo quotes must carry DEMO disclosures
        for q in quotes:
            assert q.is_demo_data is True
            assert q.source_type == "DEMO_ADAPTER"

        result = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))
        assert result.has_demo_data is True
        # At least one eligible courier for standard PIN codes
        assert result.total_eligible >= 1

    def test_demo_quotes_never_silently_zero_ineligible(self):
        """Ineligible couriers must have no cost breakdown — never zeroed silently."""
        order, _ = build_order_input_from_form({**VALID_FORM_DATA, "recipient_pin": "790001"})
        from src.providers.registry import get_provider
        provider = get_provider("demo")
        quotes = provider.get_rates(order)
        for q in quotes:
            if not q.is_serviceable:
                assert q.cost_breakdown is None, (
                    f"Ineligible courier {q.courier_name} must not have a silent-zero cost breakdown"
                )



class TestRateComparisonExpanderState:
    """Regression tests for st.expander expanded state and missing winner handling."""

    def test_winner_expander_state_is_strictly_boolean_with_winner(self):
        order, _ = build_order_input_from_form(VALID_FORM_DATA)
        from src.providers.registry import get_provider
        from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers

        provider = get_provider("demo")
        quotes = provider.get_rates(order)
        ranking_result = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))
        assert ranking_result.selected_winner is not None

        winner_quote_id = (
            ranking_result.selected_winner.quote.quote_id
            if (ranking_result and getattr(ranking_result, "selected_winner", None) and getattr(ranking_result.selected_winner, "quote", None))
            else None
        )
        assert winner_quote_id is not None

        eligible = [q for q in quotes if q.is_eligible]
        for quote in eligible:
            is_winner = bool(winner_quote_id and quote.quote_id == winner_quote_id)
            assert isinstance(is_winner, bool)
            assert is_winner is not None

        # Exactly one quote must match the recommended winner
        winner_quotes = [q for q in eligible if bool(winner_quote_id and q.quote_id == winner_quote_id)]
        assert len(winner_quotes) == 1

    def test_missing_winner_expander_state_is_strictly_boolean_false(self):
        """When ranking_result has no winner, is_winner must be False (never None)."""
        ranking_result = None
        winner_quote_id = (
            ranking_result.selected_winner.quote.quote_id
            if (ranking_result and getattr(ranking_result, "selected_winner", None) and getattr(ranking_result.selected_winner, "quote", None))
            else None
        )
        assert winner_quote_id is None

        test_quote_ids = ["Q1", "Q2", "Q3"]
        for qid in test_quote_ids:
            is_winner = bool(winner_quote_id and qid == winner_quote_id)
            assert is_winner is False
            assert isinstance(is_winner, bool)

    def test_ranking_result_with_none_winner_does_not_evaluate_to_none(self):
        """When ranking_result is present but selected_winner is None, is_winner must be False."""
        from unittest.mock import MagicMock
        ranking_result = MagicMock()
        ranking_result.selected_winner = None

        winner_quote_id = (
            ranking_result.selected_winner.quote.quote_id
            if (ranking_result and getattr(ranking_result, "selected_winner", None) and getattr(ranking_result.selected_winner, "quote", None))
            else None
        )
        assert winner_quote_id is None

        is_winner = bool(winner_quote_id and "Q1" == winner_quote_id)
        assert is_winner is False
        assert isinstance(is_winner, bool)

    def test_streamlit_expander_rejects_none_but_accepts_booleans(self):
        """Verifies Streamlit st.expander protobuf behavior with bool vs None."""
        import streamlit as st

        # True and False must succeed
        with st.expander("test true", expanded=True):
            pass
        with st.expander("test false", expanded=False):
            pass

        # None must raise TypeError ('NoneType' object cannot be interpreted as an integer)
        with pytest.raises(TypeError, match="cannot be interpreted as an integer"):
            with st.expander("test none", expanded=None):
                pass

    def test_render_rate_comparison_handles_missing_and_present_winner(self):
        """Smoke test _render_rate_comparison with both valid and missing recommendations."""
        import streamlit as st
        from unittest.mock import MagicMock
        from src.providers.registry import get_provider
        from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers
        from src.ui.app import _render_rate_comparison

        order, _ = build_order_input_from_form(VALID_FORM_DATA)
        provider = get_provider("demo")
        quotes = provider.get_rates(order)
        ranking_result = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))

        # 1. Normal ranking result with winner
        st.session_state["order"] = order
        st.session_state["quotes"] = quotes
        st.session_state["ranking_result"] = ranking_result
        _render_rate_comparison()

        # 2. Missing ranking result (None)
        st.session_state["ranking_result"] = None
        _render_rate_comparison()

        # 3. Missing winner (selected_winner = None)
        mock_rank = MagicMock()
        mock_rank.selected_winner = None
        st.session_state["ranking_result"] = mock_rank
        _render_rate_comparison()
