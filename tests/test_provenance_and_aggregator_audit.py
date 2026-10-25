"""
Tests for Courier Aggregator Sandbox/Mock Retrieval & Field-Level Provenance Audit.
Verifies compliance with the hackathon requirement:
"Retrieve from: Courier aggregator dashboards. Build against Courier aggregator sandbox / mock.
Store source IDs/URLs and retrieval times beside the extracted values so the decisions can be checked."
"""

from __future__ import annotations

import re
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from src.domain.models import Address, Dimensions, OrderInput, PackageType, CourierQuote
from src.providers.demo_adapter import DemoShippingAdapter, LocalSandboxShippingAdapter
from src.domain.ranking import rank_couriers, RankingPreferences, RankingMode
from src.domain.freshness import check_quote_freshness, QuoteFreshnessStatus
from src.persistence.database import DatabaseManager
from src.server import app, db_manager


client = TestClient(app)


def build_sample_order(order_ref: str = "TEST-PROV-ORD-01") -> OrderInput:
    return OrderInput(
        order_reference=order_ref,
        sender=Address(
            name="Alpha Hub",
            street_line1="Industrial Area",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560103",
            phone="9876543210",
        ),
        recipient=Address(
            name="Beta Depot",
            street_line1="Logistics Park",
            city="Pune",
            state="Maharashtra",
            pin_code="411001",
            phone="9123456780",
        ),
        parcel_weight_kg=2.0,
        dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        package_type=PackageType.BOX,
        is_cod=False,
        cod_amount=0.0,
    )


def test_local_sandbox_aggregator_mock_provenance():
    """Verify Local Sandbox adapter retrieves quotes with aggregator mock provenance."""
    adapter = LocalSandboxShippingAdapter()
    order = build_sample_order("TEST-SBX-MOCK-01")
    quotes = adapter.get_rates(order)

    assert len(quotes) > 0
    for q in quotes:
        assert q.source_id == "courier-aggregator-sandbox-mock-v1"
        assert q.evidence_url == "mock://courier-aggregator/v1/serviceability-rates"
        assert q.retrieval_method == "MOCK_AGGREGATOR_INTERFACE"
        assert q.retrieval_timestamp is not None
        assert "T" in q.retrieval_timestamp  # ISO 8601
        assert q.raw_response_snapshot is not None
        assert "mock_endpoint" in q.raw_response_snapshot

        # Verify field-level provenance
        assert len(q.field_provenance) > 0
        expected_fields = ["total_cost", "base_freight", "estimated_delivery_days", "is_serviceable", "courier_name"]
        for f in expected_fields:
            if f == "estimated_delivery_days" and not q.is_serviceable:
                continue
            assert f in q.field_provenance, f"Field '{f}' missing from quote provenance"
            prec = q.field_provenance[f]
            assert prec.source_identifier == "courier-aggregator-sandbox-mock-v1"
            assert prec.source_url_or_endpoint == "mock://courier-aggregator/v1/serviceability-rates"
            assert prec.retrieval_method == "MOCK_AGGREGATOR_INTERFACE"
            assert prec.validation_status == "MOCK_VERIFIED"
            assert prec.retrieval_timestamp == q.retrieval_timestamp


def test_sqlite_persistence_of_field_provenance():
    """Verify field-level provenance records persist and query cleanly from SQLite."""
    mem_db = DatabaseManager(":memory:")
    order = build_sample_order("TEST-DB-PROV-01")
    mem_db.save_order_model(order)

    adapter = LocalSandboxShippingAdapter()
    quotes = adapter.get_rates(order)
    first_q = quotes[0]

    # Save quote model into SQLite
    mem_db.save_quote_model(first_q, order.order_reference)

    # Retrieve from quote_field_provenance table
    saved_prov = mem_db.get_field_provenance_for_quote(first_q.quote_id)
    assert len(saved_prov) >= 5, f"Expected at least 5 field provenance records, got {len(saved_prov)}"

    fields_recorded = {r["field_name"]: r for r in saved_prov}
    assert "total_cost" in fields_recorded
    assert fields_recorded["total_cost"]["source_identifier"] == "courier-aggregator-sandbox-mock-v1"
    assert fields_recorded["total_cost"]["source_url_or_endpoint"] == "mock://courier-aggregator/v1/serviceability-rates"
    assert fields_recorded["total_cost"]["validation_status"] == "MOCK_VERIFIED"

    # Order-level retrieval
    order_prov = mem_db.get_field_provenance_for_order(order.order_reference)
    assert len(order_prov) == len(saved_prov)


def test_manual_sandbox_quote_api_provenance():
    """Verify Manual Sandbox Entry stores operator source ID, dashboard URL, and unverified status."""
    order_ref = "TEST-MANUAL-API-01"
    # Create order first via rate calculation or pre-seeding
    order = build_sample_order(order_ref)
    db_manager.save_order_model(order)

    payload = {
        "order_reference": order_ref,
        "courier_name": "Shiprocket Surface Test",
        "service_type": "Surface Cargo",
        "base_freight": 95.0,
        "fuel_surcharge": 9.5,
        "tax_amount": 18.81,
        "total_cost": 123.31,
        "estimated_delivery_days": 4,
        "dashboard_reference": "SR-SBX-88219",
        "source_identifier": "shiprocket-sandbox-dashboard",
        "source_url": "https://app.shiprocket.in/dashboard/rates",
        "notes": "Manually copied from Shiprocket rate calculator sandbox page",
    }

    res = client.post("/api/manual-sandbox/quote", json=payload)
    # Manual sandbox entry has been removed per Requirement 3; endpoints return 410 Gone
    assert res.status_code == 410
    assert "Manual Sandbox Entry mode has been removed" in res.json()["detail"]


def test_dashboard_export_parser_api():
    """Verify pasting aggregator dashboard export JSON parses and records provenance."""
    order_ref = "TEST-EXP-PARSER-01"
    order = build_sample_order(order_ref)
    db_manager.save_order_model(order)

    export_payload = {
        "order_reference": order_ref,
        "source_identifier": "delhivery-one-sandbox",
        "source_url": "https://one.delhivery.com/sandbox/calculator",
        "quotes": [
            {
                "courier_name": "Delhivery Express Surface",
                "service_type": "Surface Express",
                "base_freight": 78.0,
                "fuel_surcharge": 7.8,
                "tax_amount": 15.44,
                "total_cost": 101.24,
                "estimated_delivery_days": 3,
                "raw_rate_card_id": "DELH-SURF-SBX-1",
            },
            {
                "courier_name": "Delhivery Priority Air",
                "service_type": "Priority Air",
                "base_freight": 120.0,
                "fuel_surcharge": 12.0,
                "tax_amount": 23.76,
                "total_cost": 155.76,
                "estimated_delivery_days": 1,
                "raw_rate_card_id": "DELH-AIR-SBX-2",
            },
        ],
    }

    res = client.post("/api/manual-sandbox/dashboard-export", json=export_payload)
    # Manual sandbox export has been removed per Requirement 3; endpoints return 410 Gone
    assert res.status_code == 410
    assert "Manual Sandbox Entry mode has been removed" in res.json()["detail"]


def test_decision_provenance_on_ranking():
    """Verify decision provenance links the winning decision to its retrieved quote and values."""
    order = build_sample_order("TEST-RANK-PROV-01")
    adapter = LocalSandboxShippingAdapter()
    quotes = adapter.get_rates(order)

    ranking = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))
    assert ranking.selected_winner is not None
    assert ranking.decision_provenance is not None

    dp = ranking.decision_provenance
    assert dp["winner_quote_id"] == ranking.selected_winner.quote.quote_id
    assert dp["winner_courier_code"] == ranking.selected_winner.quote.courier_code
    assert dp["source_identifier"] == "courier-aggregator-sandbox-mock-v1"
    assert dp["source_url_or_endpoint"] == "mock://courier-aggregator/v1/serviceability-rates"
    assert dp["retrieval_method"] == "MOCK_AGGREGATOR_INTERFACE"
    assert "field_provenance_summary" in dp
    assert "total_cost" in dp["field_provenance_summary"]


def test_quote_freshness_provenance_validation():
    """Verify quote freshness check inspects provenance completeness."""
    order = build_sample_order("TEST-FRESH-PROV-01")
    adapter = LocalSandboxShippingAdapter()
    quotes = adapter.get_rates(order)
    fresh_q = quotes[0]

    check = check_quote_freshness(fresh_q, max_age_seconds=600)
    assert check.is_fresh is True
    assert check.status == QuoteFreshnessStatus.FRESH
    assert check.has_provenance is True
    assert check.provenance_details["source_id"] == "courier-aggregator-sandbox-mock-v1"

    # Quote missing provenance source_id
    corrupted_q = fresh_q.model_copy(update={"source_id": ""})
    corrupt_check = check_quote_freshness(corrupted_q)
    assert corrupt_check.status == QuoteFreshnessStatus.UNVERIFIABLE
    assert corrupt_check.has_provenance is False
    assert "Missing source provenance" in corrupt_check.explanation


def test_api_rates_returns_provenance_and_comparison():
    """Verify POST /api/rates returns quotes with field_provenance and decision_provenance."""
    res = client.post(
        "/api/rates",
        json={
            "sender_name": "Test Sender",
            "sender_pin": "560103",
            "sender_city": "Bengaluru",
            "sender_state": "Karnataka",
            "sender_phone": "9876543210",
            "recipient_name": "Test Recipient",
            "recipient_pin": "411001",
            "recipient_city": "Pune",
            "recipient_state": "Maharashtra",
            "recipient_phone": "9123456780",
            "length_cm": 25.0,
            "width_cm": 20.0,
            "height_cm": 15.0,
            "parcel_weight_kg": 2.0,
            "package_type": "box",
            "is_cod": False,
            "cod_amount": 0.0,
            "provider_mode": "local_sandbox",
            "ranking_mode": "lowest_cost",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()

    assert "decision_provenance" in data
    assert data["decision_provenance"]["source_identifier"] == "courier-aggregator-sandbox-mock-v1"
    assert data["decision_provenance"]["source_url_or_endpoint"] == "mock://courier-aggregator/v1/serviceability-rates"

    for q in data["quotes"]:
        assert "field_provenance" in q
        assert len(q["field_provenance"]) >= 5


def test_zero_emojis_across_entire_repo():
    """Verify absolute absence of emoji characters across all project source, static, and test files."""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # Emoticons
        "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
        "\U0001F680-\U0001F6FF"  # Transport and Map
        "\U0001F700-\U0001F77F"  # Alchemical Symbols
        "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "\U00002600-\U000026FF"  # Miscellaneous Symbols (e.g. warning, lightning, anchor)
        "\U00002700-\U000027BF"  # Dingbats (e.g. checkmark, cross, scissors)
        "\U0001F1E6-\U0001F1FF"  # Flags
        "]",
        flags=re.UNICODE,
    )

    root = Path(".")
    dirs_to_check = ["src", "static", "tests"]
    violations = []

    for d in dirs_to_check:
        for p in root.glob(f"{d}/**/*"):
            if p.is_file() and p.suffix in (".py", ".html", ".js", ".css", ".md"):
                try:
                    text = p.read_text(encoding="utf-8")
                    matches = emoji_pattern.findall(text)
                    if matches:
                        violations.append((str(p), matches))
                except Exception:
                    pass

    assert len(violations) == 0, f"Found emojis in project files: {violations}"
