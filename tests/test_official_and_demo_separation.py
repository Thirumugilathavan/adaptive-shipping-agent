"""
Regression tests verifying clean separation between:
1. Official Competition Test Cases (strictly 4: TC1, TC2, TC3, TC4)
2. Evaluator Demo Scenarios (strictly 10: Demo A through Demo J)
3. Zero emojis throughout the codebase.
"""

from __future__ import annotations

import os
import re
import pytest
from fastapi.testclient import TestClient

from src.domain.evaluator import EvaluatorSuite, SCENARIO_METADATA, ScenarioStatus
from src.domain.official_scenarios import OfficialScenariosRunner
from src.persistence.database import DatabaseManager
from src.server import OFFICIAL_TC_METADATA, app


@pytest.fixture
def memory_db():
    return DatabaseManager(":memory:")


@pytest.fixture
def client():
    return TestClient(app)


# ==============================================================================
# 1. Exact Counts and Registries
# ==============================================================================

def test_official_test_cases_count_and_keys():
    """Official competition registry must contain exactly 4 test cases: TC1, TC2, TC3, TC4."""
    assert len(OFFICIAL_TC_METADATA) == 4
    expected_keys = {"TC1", "TC2", "TC3", "TC4"}
    assert set(OFFICIAL_TC_METADATA.keys()) == expected_keys


def test_demo_scenarios_count_and_keys():
    """Demo scenarios registry must contain exactly 10 scenarios: DEMO_A through DEMO_J."""
    assert len(SCENARIO_METADATA) == 10
    expected_keys = {
        "DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E",
        "DEMO_F", "DEMO_G", "DEMO_H", "DEMO_I", "DEMO_J",
    }
    assert set(SCENARIO_METADATA.keys()) == expected_keys


# ==============================================================================
# 2. Strict Disjointness
# ==============================================================================

def test_disjointness_of_ids():
    """No scenario ID can exist in both the official registry and the demo registry."""
    official_ids = set(OFFICIAL_TC_METADATA.keys())
    demo_ids = set(SCENARIO_METADATA.keys())
    assert official_ids.isdisjoint(demo_ids)


def test_no_official_tc_names_in_demo_metadata():
    """Demo scenario titles and descriptions must not be proxies or aliases for TC1-TC4."""
    for demo_id, meta in SCENARIO_METADATA.items():
        title = meta.get("title", "")
        full_title = meta.get("full_title", "")
        # Must not have "Official TC" in title
        assert "Official TC" not in title, f"{demo_id} has 'Official TC' in title: {title}"
        assert "Official TC" not in full_title, f"{demo_id} has 'Official TC' in full_title: {full_title}"
        assert not re.search(r"\bTC[1-4]\b", title), f"{demo_id} references TC[1-4] in title: {title}"


def test_no_demo_names_in_official_metadata():
    """Official test case titles must not reference Demo A through J."""
    for tc_id, meta in OFFICIAL_TC_METADATA.items():
        title = meta.get("title", "")
        assert not re.search(r"\bDemo [A-J]\b", title), f"{tc_id} references Demo in title: {title}"


# ==============================================================================
# 3. Independent Domain Execution
# ==============================================================================

def test_official_runner_executes_only_4_cases(memory_db):
    """OfficialScenariosRunner.run_all must execute exactly TC1 to TC4."""
    runner = OfficialScenariosRunner(memory_db)
    results = runner.run_all()
    assert len(results) == 4
    result_ids = [r.test_case_id for r in results]
    assert result_ids == ["TC1", "TC2", "TC3", "TC4"]
    assert all(r.status == "PASSED" for r in results)


def test_evaluator_suite_executes_only_10_demos(memory_db):
    """EvaluatorSuite.run_all must execute exactly DEMO_A to DEMO_J without calling official test cases."""
    suite = EvaluatorSuite(memory_db)
    results = suite.run_all()
    assert len(results) == 10
    result_ids = [r.scenario_id for r in results]
    assert result_ids == [
        "DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E",
        "DEMO_F", "DEMO_G", "DEMO_H", "DEMO_I", "DEMO_J",
    ]
    assert all(r.status == ScenarioStatus.PASSED for r in results)


# ==============================================================================
# 4. API Endpoints Separation
# ==============================================================================

def test_api_official_scenarios_list(client):
    """GET /api/official-scenarios must return exactly 4 items."""
    resp = client.get("/api/official-scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 4
    ids = [item["id"] for item in data]
    assert ids == ["TC1", "TC2", "TC3", "TC4"]


def test_api_demo_scenarios_list(client):
    """GET /api/scenarios must return exactly 10 items."""
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 10
    ids = [item["id"] for item in data]
    assert ids == [
        "DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E",
        "DEMO_F", "DEMO_G", "DEMO_H", "DEMO_I", "DEMO_J",
    ]


def test_api_run_all_official_scenarios(client):
    """POST /api/official-scenarios/run must execute exactly 4 official test cases."""
    resp = client.post("/api/official-scenarios/run")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 4
    assert [r["test_case_id"] for r in data] == ["TC1", "TC2", "TC3", "TC4"]
    assert all(r["status"] == "PASSED" for r in data)


def test_api_run_all_demo_scenarios(client):
    """POST /api/scenarios/run must execute exactly 10 demo scenarios."""
    resp = client.post("/api/scenarios/run")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 10
    assert [r["scenario_id"] for r in data] == [
        "DEMO_A", "DEMO_B", "DEMO_C", "DEMO_D", "DEMO_E",
        "DEMO_F", "DEMO_G", "DEMO_H", "DEMO_I", "DEMO_J",
    ]
    assert all(r["status"] == "PASSED" for r in data)


# ==============================================================================
# 5. Project-Wide Zero Emojis Verification
# ==============================================================================

def test_zero_emojis_across_entire_codebase():
    """Verify that zero emojis or emoji-style decorative symbols exist in the repository."""
    emoji_pattern = re.compile(
        r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u203c-\u2049]|[\u25aa-\u25fe]"
    )
    ignore_dirs = {".git", "__pycache__", ".pytest_cache", "venv", ".venv", "models"}
    ignore_exts = {".cbm", ".pkl", ".parquet", ".db", ".png", ".jpg", ".pdf"}

    violations = []
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in ignore_exts:
                continue
            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line_no, line in enumerate(f, 1):
                        found = emoji_pattern.findall(line)
                        if found:
                            rel_path = os.path.relpath(filepath, root_dir)
                            violations.append((rel_path, line_no, [hex(ord(c)) for c in found], line.strip()))
            except UnicodeDecodeError:
                pass

    assert len(violations) == 0, f"Found {len(violations)} emoji violations: {violations[:5]}"
