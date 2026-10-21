"""
Automated unit and integration tests for Official Test Cases (TC1, TC2, TC3, TC4).
Phase 6 compliance verification.
"""

import pytest
from src.domain.official_scenarios import OfficialScenariosRunner
from src.persistence.database import DatabaseManager


@pytest.fixture
def runner():
    db = DatabaseManager(":memory:")
    return OfficialScenariosRunner(db)


def test_tc1_same_parcel_three_couriers(runner):
    """TC1: One parcel compared across three couriers, ranked by lowest cost with transparent breakdown."""
    result = runner.run_tc1()
    assert result.test_case_id == "TC1"
    assert result.status == "PASSED"
    assert "Winner" in result.actual_result
    assert result.evidence_references["quotes_count"] == 3
    assert len(result.evidence_references["breakdowns"]) == 3


def test_tc2_large_light_box(runner):
    """TC2: Light parcel with large dimensions triggers volumetric weight calculation per courier divisor."""
    result = runner.run_tc2()
    assert result.test_case_id == "TC2"
    assert result.status == "PASSED"
    assert result.evidence_references["air_vol_kg"] == 24.0
    assert result.evidence_references["surface_vol_kg"] == pytest.approx(26.667, 0.01)
    assert result.evidence_references["cargo_vol_kg"] == 30.0


def test_tc3_destination_pin_not_serviceable(runner):
    """TC3: Destination with remote PIN is marked unserviceable and excluded from ranking."""
    result = runner.run_tc3()
    assert result.test_case_id == "TC3"
    assert result.status == "PASSED"
    assert "remote region" in result.evidence_references["rejection_reason"]
    assert result.evidence_references["ineligible_count"] >= 1


def test_tc4_label_created_and_verified(runner):
    """TC4: Shipment created, label generated, AWB matched, pickup scheduled and independently verified."""
    result = runner.run_tc4()
    assert result.test_case_id == "TC4"
    assert result.status == "PASSED"
    assert result.evidence_references["tracking_number"].startswith("DEMO-TRK-")
    assert result.evidence_references["pickup_reference"].startswith("DEMO-PKP-")
    
    checks = result.evidence_references["verification_checks"]
    assert len(checks) == 5
    assert all(c["passed"] for c in checks)


def test_run_all_and_persistence(runner):
    """Ensure all 4 test cases can run sequentially and are persisted to SQLite."""
    results = runner.run_all()
    assert len(results) == 4
    assert all(r.status == "PASSED" for r in results)

    # Verify SQLite persistence
    stored_runs = runner.db.get_official_test_runs()
    assert len(stored_runs) >= 4
    tc_ids = [r["test_case_id"] for r in stored_runs]
    assert "TC1" in tc_ids
    assert "TC2" in tc_ids
    assert "TC3" in tc_ids
    assert "TC4" in tc_ids
