"""
Unit and integration tests for the 6-Stage Explainable Agent Decision Engine & Benchmark.

Verifies:
1. Complete 6-stage lifecycle (Observe, Evaluate, Decide, Approval, Safe Execution, Verification).
2. Explicit states: VERIFIED, ELIGIBLE, INELIGIBLE, UNVERIFIED, PENDING, FAILED, UNRESOLVED.
3. Evaluator Scenarios A, B, C, D behavior in the engine.
4. Human intervention trigger when all quotes are ineligible.
5. Benchmark runner execution and correctness.
"""

import pytest
import json
from decimal import Decimal

from src.domain.models import (
    Address,
    Dimensions,
    OrderInput,
    PackageType,
    CourierQuote,
    CostBreakdown,
    ShipmentStatus,
)
from src.domain.packaging import DEMO_PACKAGING_CATALOGUE, CustomPackaging
from src.domain.ranking import RankingPreferences, RankingMode
from src.domain.workflow import (
    AgentDecisionEngine,
    AgentDecisionTrace,
    DecisionTraceStatus,
    run_evaluator_scenario,
)
from src.domain.benchmark import run_innovation_benchmark, EvaluationMetrics
from src.providers.demo_adapter import DemoShippingAdapter


@pytest.fixture
def standard_order() -> OrderInput:
    return OrderInput(
        order_reference="ORD-WF-001",
        parcel_weight_kg=1.8,
        dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
        package_type=PackageType.BOX,
        declared_value_inr=1200.0,
        is_cod=True,
        cod_amount=1200.0,
        sender=Address(
            name="Alpha Sender",
            phone="9876543210",
            street_line1="12 Industrial Area",
            city="New Delhi",
            state="Delhi",
            pin_code="110001",
        ),
        recipient=Address(
            name="Beta Receiver",
            phone="9876543211",
            street_line1="45 Brigade Road",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560001",
        ),
    )


def test_agent_decision_engine_lifecycle_complete(standard_order):
    """Test full 6-stage execution from Observe to Verification."""
    engine = AgentDecisionEngine()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)

    trace = engine.execute_full_workflow(
        order=standard_order,
        quotes=quotes,
        preferences=RankingPreferences(mode=RankingMode.LOWEST_COST),
        approved_by="Lead Dispatcher",
        cost_confirmed=quotes[0].cost_breakdown.total_cost if quotes[0].cost_breakdown else 250.0,
        simulate_execution=True,
    )

    assert isinstance(trace, AgentDecisionTrace)
    assert trace.order_reference == standard_order.order_reference
    assert len(trace.steps) == 7

    # Verify stage progression
    stages = [s.stage for s in trace.steps]
    assert stages == [
        "Stage A: Observe",
        "Stage B: Evaluate Alternatives",
        "Stage B: Evaluate Alternatives",
        "Stage C: Decide & Recommend",
        "Stage D: Obtain Approval",
        "Stage E: Safe Execution",
        "Stage F: Verify & Reconcile",
    ][:len(stages)]

    assert trace.final_status == DecisionTraceStatus.VERIFIED
    assert trace.selected_courier_code is not None


def test_trace_json_serializability(standard_order):
    """Test that AgentDecisionTrace correctly exports to JSON and dict."""
    engine = AgentDecisionEngine()
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)

    trace = engine.execute_full_workflow(
        order=standard_order,
        quotes=quotes,
        preferences=RankingPreferences(mode=RankingMode.LOWEST_COST),
        approved_by="Test Operator",
        simulate_execution=True,
    )

    trace_dict = trace.to_dict()
    assert trace_dict["order_reference"] == standard_order.order_reference
    assert len(trace_dict["steps"]) >= 5

    trace_json = trace.to_json()
    parsed = json.loads(trace_json)
    assert parsed["trace_id"] == trace.trace_id
    assert parsed["final_status"] == "VERIFIED"


def test_human_intervention_when_no_quotes_eligible():
    """Verify that Stage C detects infeasibility and triggers human intervention halt."""
    order = OrderInput(
        order_reference="ORD-WF-IMPOSSIBLE",
        parcel_weight_kg=120.0,  # Exceeds max limit for all demo couriers (max 70kg)
        dimensions=Dimensions(length_cm=300.0, width_cm=200.0, height_cm=200.0),
        package_type=PackageType.BOX,
        declared_value_inr=5000.0,
        sender=Address(
            name="Delhi Hub",
            phone="9876543210",
            street_line1="12 Industrial Area",
            city="Delhi",
            state="Delhi",
            pin_code="110001",
        ),
        recipient=Address(
            name="Bengaluru Depot",
            phone="9876543211",
            street_line1="45 Brigade Road",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560001",
        ),
    )

    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(order)

    engine = AgentDecisionEngine()
    trace = engine.create_trace(order.order_reference)

    obs_ok = engine.run_observation_stage(order, trace)
    assert obs_ok is True

    eligible_quotes = engine.run_evaluation_stage(order, quotes, trace)
    assert len(eligible_quotes) == 0

    best_quote = engine.run_decision_stage(quotes, RankingPreferences(), trace)
    assert best_quote is None

    # Stage C should reflect intervention required
    stage_c = next(s for s in trace.steps if "Stage C" in s.stage)
    assert stage_c.status == DecisionTraceStatus.UNRESOLVED
    assert trace.final_status == DecisionTraceStatus.UNRESOLVED


def test_evaluator_scenario_a():
    """Verify Evaluator Scenario A (Standard order, Surface selected)."""
    res = run_evaluator_scenario("A")
    assert res["status"] == "SUCCESS"
    trace = res["trace"]
    assert trace.final_status == DecisionTraceStatus.VERIFIED
    assert res["winner_code"] == "DEMO_SURFACE_STD"


def test_evaluator_scenario_b_oversized():
    """Verify Evaluator Scenario B (Oversized 110cm rejects Overnight Express)."""
    res = run_evaluator_scenario("B")
    assert res["status"] == "SUCCESS"
    assert "rejections" in res
    assert len(res["rejections"]) >= 1
    # Check that Overnight Express is rejected due to max length 100cm
    assert "DEMO_OVERNIGHT" in res["rejections"]
    trace = res["trace"]
    assert trace.final_status == DecisionTraceStatus.VERIFIED


def test_evaluator_scenario_c_remote_pincode():
    """Verify Evaluator Scenario C (Remote pin 791111 rejects Air Express)."""
    res = run_evaluator_scenario("C")
    assert res["status"] == "SUCCESS"
    assert "rejections" in res
    # Air Express rejected due to pincode routing constraints
    assert "DEMO_AIR_EXPRESS" in res["rejections"]
    # Surface Standard remains eligible
    assert res["winner_code"] == "DEMO_SURFACE_STD"


def test_evaluator_scenario_d_ambiguous_execution():
    """Verify Evaluator Scenario D (Timeout / uncertain dispatch)."""
    res = run_evaluator_scenario("D")
    assert res["status"] == "UNCERTAIN_TIMEOUT"
    trace = res["trace"]
    # Stage E should be UNRESOLVED / FAILED
    stage_e = next(s for s in trace.steps if "Stage E" in s.stage)
    assert stage_e.status in (DecisionTraceStatus.UNRESOLVED, DecisionTraceStatus.FAILED)
    # Stage F verification should be UNVERIFIED / PENDING
    stage_f = next(s for s in trace.steps if "Stage F" in s.stage)
    assert stage_f.status in (DecisionTraceStatus.UNVERIFIED, DecisionTraceStatus.PENDING)


def test_benchmark_runner_matrix():
    """Verify that the innovation benchmark runs cleanly and demonstrates agent superiority."""
    metrics: EvaluationMetrics = run_innovation_benchmark()
    assert metrics.total_test_cases == 4
    assert metrics.enhanced_invalid_recommendations == 0
    assert metrics.enhanced_packaging_failures == 0
    assert metrics.baseline_packaging_failures > 0
    assert metrics.enhanced_stale_acceptances == 0
    assert metrics.baseline_stale_acceptances > 0
    assert metrics.enhanced_duplicate_dispatches == 0
    assert metrics.baseline_duplicate_dispatches > 0
    assert metrics.explanation_transparency_score_pct == 100.0



# ── Task C: Approval & Dispatch State Machine Regression Tests ──────────────

def test_record_approval_valid_records_exactly_once(standard_order):
    """Test valid approval records exactly once on AgentDecisionTrace."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    expected_cost = quote.cost_breakdown.total_cost

    trace = AgentDecisionTrace(order_reference=standard_order.order_reference)
    step = trace.record_approval(
        approved_by="Lead Operator",
        cost_confirmed=expected_cost,
        notes="Urgent shipment approved",
        quote_id=quote.quote_id,
        courier_name=quote.courier_name,
        order=standard_order,
        quote=quote,
    )

    assert step.stage == "Stage D: Obtain Approval"
    assert step.status == DecisionTraceStatus.VERIFIED
    assert len(trace.steps) == 1
    assert trace.approval_record is not None
    assert trace.approval_record.approved_by == "Lead Operator"
    assert trace.approval_record.total_cost == expected_cost
    assert trace.completed_stage == "Stage D: Obtain Approval"


def test_record_approval_invalid_operator_rejected(standard_order):
    """Operator name must be at least 2 characters."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    trace = AgentDecisionTrace(order_reference=standard_order.order_reference)

    with pytest.raises(ValueError, match="at least 2 characters"):
        trace.record_approval(
            approved_by="",
            cost_confirmed=quote.cost_breakdown.total_cost,
            quote=quote,
            order=standard_order,
        )


def test_record_approval_negative_price_rejected(standard_order):
    """Confirmed cost must be non-negative."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    trace = AgentDecisionTrace(order_reference=standard_order.order_reference)

    with pytest.raises(ValueError, match="cannot be negative"):
        trace.record_approval(
            approved_by="Operator",
            cost_confirmed=-50.0,
            quote=quote,
            order=standard_order,
        )


def test_record_approval_order_mismatch_rejected(standard_order):
    """Proposal must match trace order reference."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    trace = AgentDecisionTrace(order_reference="DIFFERENT-REF-999")

    with pytest.raises(ValueError, match="Approval proposal inconsistent with order"):
        trace.record_approval(
            approved_by="Operator",
            cost_confirmed=quote.cost_breakdown.total_cost,
            quote=quote,
            order=standard_order,
        )


def test_record_approval_ineligible_quote_rejected(standard_order):
    """Ineligible quotes cannot be approved."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    ineligible_quote = quote.model_copy(update={"is_eligible": False, "ineligibility_reasons": ["Service suspended"]})
    trace = AgentDecisionTrace(order_reference=standard_order.order_reference)

    with pytest.raises(ValueError, match="Cannot approve ineligible or unserviceable"):
        trace.record_approval(
            approved_by="Operator",
            cost_confirmed=100.0,
            quote=ineligible_quote,
        )


def test_record_approval_price_mismatch_rejected(standard_order):
    """Price differing from quote cost breakdown must be rejected."""
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    expected_cost = quote.cost_breakdown.total_cost
    trace = AgentDecisionTrace(order_reference=standard_order.order_reference)

    with pytest.raises(ValueError, match="does not match quote"):
        trace.record_approval(
            approved_by="Operator",
            cost_confirmed=expected_cost + 100.0,
            quote=quote,
            order=standard_order,
        )


def test_record_approval_stale_quote_rejected(standard_order):
    """Stale quote (>900 seconds old) must be rejected."""
    from datetime import datetime, timedelta, timezone
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    stale_timestamp = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    stale_quote = quote.model_copy(update={"retrieval_timestamp": stale_timestamp})
    trace = AgentDecisionTrace(order_reference=standard_order.order_reference)

    with pytest.raises(ValueError, match="Cannot approve stale quote"):
        trace.record_approval(
            approved_by="Operator",
            cost_confirmed=stale_quote.cost_breakdown.total_cost,
            quote=stale_quote,
            order=standard_order,
        )


def test_double_dispatch_prevented_by_idempotency_barrier(standard_order):
    """Repeated dispatch attempts for the same approved proposal are blocked."""
    from src.persistence.database import DatabaseManager
    from src.persistence.audit import AuditLogger
    from src.persistence.idempotency import IdempotencyManager, ShipmentAlreadyCreatedError
    from src.persistence.state_machine import ShipmentStateMachine, ApprovalRecord
    from src.domain.reconciliation import ShipmentDispatchService

    db = DatabaseManager(":memory:")
    db.insert_order({
        "order_id": "ORD-ID-001",
        "order_reference": standard_order.order_reference,
        "sender_json": "{}",
        "recipient_json": "{}",
        "parcel_weight_kg": standard_order.parcel_weight_kg,
        "length_cm": standard_order.dimensions.length_cm,
        "width_cm": standard_order.dimensions.width_cm,
        "height_cm": standard_order.dimensions.height_cm,
        "package_type": "box",
        "is_cod": 0,
        "cod_amount": 0.0,
        "created_at": "2026-10-20T00:00:00Z",
    })
    audit = AuditLogger(db)
    idem = IdempotencyManager(db)
    fsm = ShipmentStateMachine()
    dispatch_service = ShipmentDispatchService(db, audit, idem, fsm)
    adapter = DemoShippingAdapter()
    quotes = adapter.get_rates(standard_order)
    quote = quotes[0]
    db.insert_quotes([{
        "quote_id": quote.quote_id,
        "order_reference": standard_order.order_reference,
        "courier_code": quote.courier_code,
        "courier_name": quote.courier_name,
        "service_type": quote.service_type,
        "estimated_delivery_days": 2,
        "total_cost": quote.cost_breakdown.total_cost,
        "quote_data_json": "{}",
        "source_type": "DEMO_ADAPTER",
        "input_hash": "hash",
        "created_at": "2026-10-20T00:00:00Z",
    }])

    approval = ApprovalRecord(
        approved_by="Alice Operator",
        quote_id=quote.quote_id,
        total_cost=quote.cost_breakdown.total_cost,
    )

    # First attempt: succeeds
    res1 = dispatch_service.attempt_dispatch(
        order=standard_order,
        quote=quote,
        approval=approval,
        provider=adapter,
    )
    assert res1.status == ShipmentStatus.CREATED

    # Second attempt: blocked by idempotency barrier
    with pytest.raises(ShipmentAlreadyCreatedError):
        dispatch_service.attempt_dispatch(
            order=standard_order,
            quote=quote,
            approval=approval,
            provider=adapter,
        )
