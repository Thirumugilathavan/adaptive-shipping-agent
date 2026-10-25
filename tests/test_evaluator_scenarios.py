"""
Comprehensive automated test suite for the 10-Scenario Shipping Evaluator
and Official Test Cases Dashboard.
Covers:
- All 10 Evaluator Scenarios (Demo A through Demo J)
- All 4 Official Test Cases (TC1 – TC4)
- Deterministic execution, evidence integrity, error handling
- Streamlit UI AppTest integration for sidebar loading/running and dashboard controls
"""

from __future__ import annotations

from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from src.domain.evaluator import EvaluatorSuite, SCENARIO_METADATA, ScenarioStatus
from src.domain.official_scenarios import OfficialScenariosRunner
from src.domain.models import ShipmentStatus
from src.persistence.database import DatabaseManager

APP_FILE = str(Path(__file__).resolve().parent.parent / "src" / "ui" / "app.py")


@pytest.fixture
def memory_db():
    return DatabaseManager(":memory:")


@pytest.fixture
def evaluator_suite(memory_db):
    return EvaluatorSuite(memory_db)


# ==============================================================================
# 10 Evaluator Scenarios Direct Domain Tests
# ==============================================================================

def test_scenario_a_normal_parcel(evaluator_suite):
    """Demo A: Standard workflow validation, quotes, ranking, no premature booking."""
    res = evaluator_suite.run_scenario("DEMO_A")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_A"
    assert len(res.subcases) == 4
    assert all(sc.passed for sc in res.subcases)
    assert res.evidence["quotes_count"] >= 3
    assert res.evidence["eligible_count"] >= 3
    assert res.evidence["winner_courier"] is not None


def test_scenario_b_oversized_elongated(evaluator_suite):
    """Demo B: 110cm elongated box; oversized carriers excluded, volumetric penalty applied."""
    res = evaluator_suite.run_scenario("DEMO_B")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_B"
    assert res.evidence["volumetric_weight_kg"] == 6.6
    assert len(res.evidence["excluded_oversized"]) > 0
    assert len(res.evidence["eligible_couriers"]) > 0


def test_scenario_c_rechoice(evaluator_suite):
    """Demo C: Recommendation re-choice; rejecting cheapest gives next alternative without looping."""
    res = evaluator_suite.run_scenario("DEMO_C")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_C"
    seq = res.evidence["sequence"]
    assert len(seq) == 3
    # All 3 selected couriers must be unique
    assert len(set(seq)) == 3
    assert all(sc.passed for sc in res.subcases)


def test_scenario_d_ambiguous_booking(evaluator_suite):
    """Demo D: Carrier timeout transitions to CREATION_UNCERTAIN; idempotency blocks duplicates; safe reconciliation."""
    res = evaluator_suite.run_scenario("DEMO_D")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_D"
    assert res.evidence["attempt_status"] == ShipmentStatus.CREATION_UNCERTAIN.value
    assert res.evidence["duplicate_blocked"] is True
    assert "RECONCILED" in res.evidence["reconciliation_status"]


def test_scenario_e_speed_express(evaluator_suite):
    """Demo E: Speed-optimized routing prioritizing lowest transit time under FASTEST_DELIVERY."""
    res = evaluator_suite.run_scenario("DEMO_E")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_E"
    assert res.evidence["quotes_count"] == 3
    assert res.evidence["fastest_courier"] is not None
    assert res.evidence["fastest_days"] <= 2
    assert res.evidence["speed_cost_premium_inr"] >= 0


def test_scenario_f_high_value_cod(evaluator_suite):
    """Demo F: High-value COD order testing collection surcharges and price differential."""
    res = evaluator_suite.run_scenario("DEMO_F")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_F"
    assert len(res.evidence["cod_fees"]) >= 3
    assert all(fee > 0 for fee in res.evidence["cod_fees"].values())
    assert res.evidence["collection_amount"] == 4500.0


def test_scenario_g_heavy_cargo_slab(evaluator_suite):
    """Demo G: Bulk parcel (18kg) testing heavy cargo multi-tier slab pricing."""
    res = evaluator_suite.run_scenario("DEMO_G")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_G"
    assert res.evidence["winner_courier"] == "DTDC Heavy Cargo (Simulated)"
    assert res.evidence["winner_total_cost"] < 600.0


def test_scenario_h_fragile_packaging_fit(evaluator_suite):
    """Demo H: Packaging optimization selecting protective corrugated box over flyer for fragile item."""
    res = evaluator_suite.run_scenario("DEMO_H")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_H"
    assert res.evidence["recommended_box"] is not None
    assert res.evidence["volume_reduction_pct"] > 0
    assert res.evidence["estimated_freight_savings_inr"] > 0
    assert len(res.evidence["rejected_packages"]) > 0


def test_scenario_i_mandatory_and_cod(evaluator_suite):
    """Demo I: Exhaustive input validation across all 9 subcases (missing fields, zero/negative, NaN, COD)."""
    res = evaluator_suite.run_scenario("DEMO_I")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_I"
    assert res.evidence["subcases_passed"] == 9
    assert res.evidence["total_subcases"] == 9


def test_scenario_j_defensive_error_handling(evaluator_suite):
    """Demo J: Defensive resiliency across 5 failure modes (unserviceable, oversized, gateway down, tamper, drift)."""
    res = evaluator_suite.run_scenario("DEMO_J")
    assert res.status == ScenarioStatus.PASSED
    assert res.scenario_id == "DEMO_J"
    assert res.evidence["subcases_passed"] == 5
    assert res.evidence["total_subcases"] == 5


def test_evaluator_suite_run_all(evaluator_suite):
    """Ensure all 10 scenarios can execute sequentially without state leakage."""
    all_res = evaluator_suite.run_all()
    assert len(all_res) == 10
    assert all(r.status == ScenarioStatus.PASSED for r in all_res)
    ids = [r.scenario_id for r in all_res]
    expected_ids = ["DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E", "DEMO_F", "DEMO_G", "DEMO_H", "DEMO_I", "DEMO_J"]
    assert ids == expected_ids


def test_evaluator_suite_unknown_scenario(evaluator_suite):
    """Ensure invalid scenario IDs return an ERROR status rather than crashing."""
    res = evaluator_suite.run_scenario("DEMO_UNKNOWN_XYZ")
    assert res.status == ScenarioStatus.ERROR
    assert "Unknown Scenario" in res.title


# ==============================================================================
# Official Scenarios Runner Direct Tests
# ==============================================================================

def test_official_runner_run_single_valid(memory_db):
    """Test running individual official test cases via run_single."""
    runner = OfficialScenariosRunner(memory_db)
    for tc in ["TC1", "TC2", "TC3", "TC4"]:
        r = runner.run_single(tc)
        assert r.status == "PASSED"
        assert r.test_case_id == tc


def test_official_runner_run_single_unknown(memory_db):
    """Test running an invalid test case ID returns ERROR status."""
    runner = OfficialScenariosRunner(memory_db)
    r = runner.run_single("TC999")
    assert r.status == "ERROR"


def test_official_runner_database_clear(memory_db):
    """Test clearing official test runs in SQLite."""
    runner = OfficialScenariosRunner(memory_db)
    runner.run_all()
    assert len(memory_db.get_official_test_runs()) >= 4

    memory_db.clear_official_test_runs()
    assert len(memory_db.get_official_test_runs()) == 0


# ==============================================================================
# Streamlit UI AppTest Integration Tests
# ==============================================================================

def test_ui_official_test_cases_dashboard():
    """Verify Official Test Cases Dashboard buttons and results in Streamlit UI."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    # Click Run All Official Tests button
    run_all_btn = [b for b in at.button if "Run All Official Tests" in b.label][0]
    run_all_btn.click().run()

    assert len(at.exception) == 0
    ot_results = at.session_state.get("official_test_results", {})
    assert len(ot_results) == 4
    for tc in ["TC1", "TC2", "TC3", "TC4"]:
        assert ot_results[tc]["status"] == "PASSED"

    # Click Reset button
    reset_btn = [b for b in at.button if "Reset Official Test Results" in b.label][0]
    reset_btn.click().run()
    assert len(at.session_state.get("official_test_results", {})) == 0


def test_ui_sidebar_evaluator_scenario_load_and_run():
    """Verify selecting, loading, and running an evaluator scenario from sidebar."""
    at = AppTest.from_file(APP_FILE, default_timeout=30)
    at.run()

    # Select Demo A
    select = at.sidebar.selectbox(key="demo_scenario_choice")
    select.set_value("Demo A: Normal Parcel (Standard comparison & booking)").run()

    # Click Load
    load_btn = [b for b in at.sidebar.button if "Load" in b.label][0]
    load_btn.click().run()

    assert at.session_state.get("order") is not None
    assert at.session_state.get("_parcel_length_cm") == 25.0
    assert at.session_state.get("quotes") is not None
    assert len(at.session_state["quotes"]) > 0

    # Click Run
    run_btn = [b for b in at.sidebar.button if "Run" in b.label][0]
    run_btn.click().run()

    sc_results = at.session_state.get("scenario_test_results", {})
    assert "DEMO_A" in sc_results
    assert sc_results["DEMO_A"].status.value == "PASSED"

    # Click Reset
    reset_btn = [b for b in at.sidebar.button if "Reset" in b.label][0]
    reset_btn.click().run()
    assert "DEMO_A" not in at.session_state.get("scenario_test_results", {})
