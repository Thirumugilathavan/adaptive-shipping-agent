#!/usr/bin/env python
"""
Standalone CLI Runner for the 10 Evaluator Edge-Case Scenarios (Demo A through Demo J).
Executes simulated test scenarios across packaging, re-choice, timeouts,
express routing, COD, heavy bulk cargo, fragile packaging, and defensive gates.

Usage:
    python run_demo_scenarios.py
"""

import json
import sys
from src.domain.evaluator import EvaluatorSuite, ScenarioStatus
from src.persistence.database import DatabaseManager


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 80)
    print("10 EVALUATOR EDGE-CASE SCENARIOS RUNNER (DEMO A - DEMO J)")
    print("=" * 80)

    db = DatabaseManager("shipping_agent.db")
    suite = EvaluatorSuite(db)
    results = suite.run_all()
    all_passed = True

    for r in results:
        status_label = "[PASS]" if r.status == ScenarioStatus.PASSED else "[FAIL]"
        if r.status != ScenarioStatus.PASSED:
            all_passed = False

        print(f"\n{status_label} {r.scenario_id}: {r.title}")
        print(f"  Description: {r.description}")
        print(f"  Expected:    {r.expected_outcome}")
        print(f"  Actual:      {r.actual_outcome}")
        print(f"  Explanation: {r.explanation}")
        if r.subcases:
            print(f"  Subcases ({sum(1 for s in r.subcases if s.passed)}/{len(r.subcases)} passed):")
            for sc in r.subcases:
                sub_status = "[OK]" if sc.passed else "[FAIL]"
                print(f"    {sub_status} {sc.subcase_id} {sc.title}: {sc.actual}")

    print("\n" + "=" * 80)
    passed_count = sum(1 for r in results if r.status == ScenarioStatus.PASSED)
    print(f"SUMMARY: {passed_count}/{len(results)} demo scenarios passed.")
    print("All results persistently logged to SQLite database ('shipping_agent.db').")
    print("=" * 80)

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
