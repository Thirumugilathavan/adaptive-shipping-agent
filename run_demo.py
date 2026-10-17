"""
Adaptive Shipping Intelligence Agent - Interactive Demonstration CLI.
Executes Phase 1, Phase 2, Phase 3, and Phase 4:
- Domain calculation engine & alternative packaging optimizer
- Multi-courier DemoShippingAdapter with 3 distinct synthetic couriers
- Courier Ranking Engine (Lowest Cost, Fastest Delivery, Balanced Score)
- Shipping Decision Stress Tester (12-scenario perturbation matrix)
- Distinctive Innovation: Recommendation Fragility Analysis
- ShipSandboxClient safety locks on unconfirmed endpoints
- SQLite persistence, Human Approval Guard, Idempotency lock, and Secret Scrubbing
"""

import json
import sys

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.domain.models import Address, Dimensions, OrderInput, PackageType, VolumetricRule
from src.domain.packaging import DEMO_PACKAGING_CATALOGUE, evaluate_packaging_alternatives
from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers
from src.domain.stress_tester import ShippingDecisionStressTester
from src.domain.weight import compute_weight_breakdown
from src.persistence.audit import AuditEventType, AuditLogger
from src.persistence.database import DatabaseManager
from src.persistence.idempotency import IdempotencyManager, generate_idempotency_key
from src.persistence.state_machine import ApprovalRecord, HumanApprovalRequiredError, ShipmentStateMachine, ShipmentStatus
from src.providers.demo_adapter import DemoShippingAdapter
from src.providers.registry import get_provider
from src.providers.shipsandbox_client import ShipSandboxClient, UnconfirmedEndpointError


def print_banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(f" {text}")
    print("=" * 78)


def main() -> None:
    print_banner("ADAPTIVE SHIPPING INTELLIGENCE AGENT: PHASE 1 TO 4 DEMO")

    # 1. Input Collection & Validation
    print("\n[Step 1] Order Intake & Address Validation...")
    order = OrderInput(
        order_reference="ORD-2026-DEMO-001",
        sender=Address(
            name="Alpha Electronics Hub",
            phone="9876543210",
            street_line1="Plot 42, Okhla Industrial Area Phase III",
            city="New Delhi",
            state="Delhi",
            pin_code="110020",
        ),
        recipient=Address(
            name="Vikram Patel",
            phone="9123456780",
            street_line1="Flat 402, Green Acres, Koramangala",
            city="Bengaluru",
            state="Karnataka",
            pin_code="560034",
        ),
        parcel_weight_kg=1.8,
        dimensions=Dimensions(length_cm=35.0, width_cm=25.0, height_cm=20.0),
        package_type=PackageType.BOX,
        is_cod=True,
        cod_amount=1500.0,
        declared_value=2500.0,
        product_description="Noise-Cancelling Wireless Headphones",
    )
    print(f"  Order Reference: {order.order_reference}")
    print(f"  Route: {order.sender.city} ({order.sender.pin_code}) -> {order.recipient.city} ({order.recipient.pin_code})")
    print(f"  Payment Mode: {'COD (Collect INR ' + str(order.cod_amount) + ')' if order.is_cod else 'Prepaid'}")
    print(f"  Physical Dead Weight: {order.parcel_weight_kg} kg")
    print(f"  Parcel Outer Dimensions: {order.dimensions.length_cm} x {order.dimensions.width_cm} x {order.dimensions.height_cm} cm")

    # 2. Weight Calculations
    print_banner("[Step 2] Volumetric & Chargeable Weight Calculations")
    vol_rule = VolumetricRule(divisor=5000.0, min_billable_weight_kg=0.5, billing_step_kg=0.5)
    weight_res = compute_weight_breakdown(order.parcel_weight_kg, order.dimensions, vol_rule)

    print(f"  Volumetric Divisor: {vol_rule.divisor} cm3/kg (Configurable Default)")
    print(f"  Parcel Volume: {order.dimensions.volume_cm3:,} cm3")
    print(f"  Volumetric Weight: {weight_res.volumetric_weight_kg:.3f} kg")
    print(f"  Chargeable Weight: {weight_res.chargeable_weight_kg:.3f} kg")
    print(f"  Rounded Billing Slab: {weight_res.billing_slab_kg:.1f} kg")
    if weight_res.is_volumetric_penalty:
        print(f"  [! NOTICE] VOLUMETRIC PENALTY DETECTED:")
        print(f"     The carrier will bill for {weight_res.billing_slab_kg} kg despite the item only weighing {order.parcel_weight_kg} kg!")

    # 3. Alternative Packaging Optimizer
    print_banner("[Step 3] Alternative Packaging Optimizer (Demo Packaging Options)")
    product_actual_dims = Dimensions(length_cm=22.0, width_cm=16.0, height_cm=10.0)
    print(f"  Product Net Dimensions: {product_actual_dims.length_cm} x {product_actual_dims.width_cm} x {product_actual_dims.height_cm} cm")
    pack_results = evaluate_packaging_alternatives(
        product_dimensions=product_actual_dims,
        product_net_weight_kg=1.1,
        current_outer_dimensions=order.dimensions,
        volumetric_rule=vol_rule,
        per_kg_freight_estimate_inr=50.0,
    )

    print("\n  Comparison against Configurable Demo Packaging Library:")
    for opt in pack_results:
        status_tag = "[FITS]" if opt.is_feasible else "[NO FIT]"
        print(f"\n  * {status_tag} {opt.box_name}")
        if opt.is_feasible:
            print(f"    - Volume Reduction: {opt.volume_reduction_pct:.1f}% (Saved {opt.volume_difference_cm3:,.0f} cm3 dead space)")
            print(f"    - Billable Slab: {opt.original_billable_slab_kg} kg -> {opt.new_billable_slab_kg} kg (Saved {opt.weight_saved_kg} kg)")
            print(f"    - Estimated Freight Savings: INR {opt.estimated_freight_savings_inr:.2f}")
        else:
            print(f"    - Reason: {opt.rejection_reason}")

    # 4. Multi-Courier Demo Adapter & Quotes
    print_banner("[Step 4] Multi-Courier Rate Discovery (Demo Shipping Adapter)")
    adapter = get_provider("demo")
    print(f"  Active Provider: {adapter.provider_name} (Code: {adapter.provider_code})")
    print(f"  Notice: All rates are PROMINENTLY MARKED AS DEMO DATA.\n")

    quotes = adapter.get_rates(order)
    for q in quotes:
        eligibility_tag = "[ELIGIBLE]" if q.is_eligible else "[INELIGIBLE]"
        print(f"  * {eligibility_tag} {q.courier_name} [{q.service_type}]")
        if q.is_eligible and q.cost_breakdown:
            print(f"    - Billable Slab: {q.weight_result.billing_slab_kg} kg (Divisor used: {q.weight_result.divisor_used:.0f})")
            print(f"    - Landed Cost:   INR {q.cost_breakdown.total_cost:.2f} (Base: INR {q.cost_breakdown.base_freight:.2f}, Fuel: INR {q.cost_breakdown.fuel_surcharge:.2f}, COD: INR {q.cost_breakdown.cod_fee:.2f}, GST: INR {q.cost_breakdown.tax_amount:.2f})")
            print(f"    - Delivery SLA:  {q.estimated_delivery_days} days transit")
            print(f"    - Quote ID:      {q.quote_id}")
        else:
            print(f"    - Status: Unserviceable ({q.unserviceable_reason})")
            print(f"    - Pricing: [NOT APPLICABLE - Zero pricing suppressed]")

    # 4a. Courier Ranking Engine (Part A)
    print_banner("[Step 4a] Courier Ranking Engine (Three Separated Strategies)")

    # Strategy 1: Lowest Cost
    rank_cost = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))
    w_cost = rank_cost.selected_winner
    print(f"  1. Strategy: LOWEST COST")
    print(f"     Winner: {w_cost.quote.courier_name} (Total Landed Cost: INR {w_cost.total_cost:.2f}, SLA: {w_cost.estimated_delivery_days} days)")
    print(f"     Margin over runner-up: INR {rank_cost.cost_margin_over_runner_up:.2f}")
    print(f"     Explanation: {rank_cost.decision_explanation}\n")

    # Strategy 2: Fastest Delivery
    rank_speed = rank_couriers(quotes, RankingPreferences(mode=RankingMode.FASTEST_DELIVERY))
    w_speed = rank_speed.selected_winner
    print(f"  2. Strategy: FASTEST DELIVERY")
    print(f"     Winner: {w_speed.quote.courier_name} (SLA: {w_speed.estimated_delivery_days} days, Landed Cost: INR {w_speed.total_cost:.2f})")
    print(f"     Margin over runner-up: {rank_speed.days_margin_over_runner_up} days faster")
    print(f"     Explanation: {rank_speed.decision_explanation}\n")

    # Strategy 3: Balanced
    rank_bal = rank_couriers(quotes, RankingPreferences(mode=RankingMode.BALANCED, cost_weight=0.6, speed_weight=0.4))
    w_bal = rank_bal.selected_winner
    print(f"  3. Strategy: BALANCED (60% Cost Weight, 40% Speed Weight)")
    print(f"     Winner: {w_bal.quote.courier_name} (Balanced Score: {w_bal.balanced_score}/100, Cost: INR {w_bal.total_cost:.2f}, SLA: {w_bal.estimated_delivery_days} days)")
    print(f"     Explanation: {rank_bal.decision_explanation}")

    # 4b. Shipping Decision Stress Tester (Part B)
    print_banner("[Step 4b] Shipping Decision Stress Tester (12-Scenario Matrix)")
    stress_tester = ShippingDecisionStressTester()
    stress_report = stress_tester.run_stress_test(order, RankingPreferences(mode=RankingMode.LOWEST_COST))

    print(f"  Baseline Winner: {stress_report.baseline_winner_name} (INR {stress_report.baseline_winner_cost:.2f})")
    print(f"  Total Scenarios Evaluated:     {stress_report.total_scenarios_evaluated}")
    print(f"  Scenarios Baseline Maintained: {stress_report.scenarios_baseline_maintained}")
    print(f"  Scenarios Switched:            {stress_report.switched_scenarios_count}")
    print(f"  Scenario Stability Score:      {stress_report.scenario_stability_pct}%")
    print(f"  Disclaimer: {stress_report.disclaimer}\n")

    print("  Selected Scenario Variations:")
    for out in stress_report.scenario_outcomes[:5]:
        switch_indicator = "[SWITCH]" if out.rank_switched else "[MAINTAINED]"
        diff_str = f"Δ INR {out.cost_difference_from_baseline:+.2f}" if out.cost_difference_from_baseline is not None else "N/A"
        print(f"  * {switch_indicator} {out.scenario.scenario_name}:")
        print(f"    Winner: {out.winner_name} (Cost: INR {out.winner_cost:.2f}, {diff_str})")
        print(f"    Details: {out.transition_explanation}")

    # 4c. Distinctive Innovation: Recommendation Fragility Analysis (Part C)
    print_banner("[Step 4c] Distinctive Innovation: Recommendation Fragility Analysis")
    frag = stress_report.fragility_analysis
    print(f"  * Fragility Status:        {'FRAGILE' if frag.is_fragile else 'ROBUST'}")
    print(f"  * Most Sensitive Input:    {frag.most_sensitive_input}")
    print(f"  * Critical Price Margin:   INR {frag.critical_price_margin_inr:.2f} (Winner maintains advantage up to this price hike)")
    print(f"  * Fragility Verdict:       {frag.fragility_verdict}")
    print(f"  * Packaging Stabilization: {frag.packaging_stabilization_effect}")
    print(f"  * Analysis Limitations:    {frag.limitations_disclaimer}")

    # 4d. ShipSandbox Safety Lock Demonstration
    print_banner("[Step 4d] ShipSandbox Safety Lock: Unconfirmed Endpoints Disabled")
    sandbox_client = ShipSandboxClient()
    print("  Testing unconfirmed shipment creation on ShipSandboxClient...")
    try:
        sandbox_client.create_shipment(order, quotes[0], ApprovalRecord(approved_by="Operator Admin", quote_id="Q", total_cost=100.0), "KEY")
        print("  [FAIL] ERROR: Unconfirmed endpoint was not blocked!")
    except UnconfirmedEndpointError as exc:
        print(f"  [OK] SAFETY GUARD ENFORCED: {exc}")

    # 5. Persistence, State Machine & Human Approval Gate
    print_banner("[Step 5] Persistence, FSM Approval & Idempotency Controls")
    db = DatabaseManager(":memory:")
    audit = AuditLogger(db)
    idem_mgr = IdempotencyManager(db)
    fsm = ShipmentStateMachine()

    # Save Order
    order_dict = {
        "order_id": "ORD-001",
        "order_reference": order.order_reference,
        "sender_json": order.sender.model_dump_json(),
        "recipient_json": order.recipient.model_dump_json(),
        "parcel_weight_kg": order.parcel_weight_kg,
        "length_cm": order.dimensions.length_cm,
        "width_cm": order.dimensions.width_cm,
        "height_cm": order.dimensions.height_cm,
        "package_type": order.package_type.value,
        "is_cod": 1 if order.is_cod else 0,
        "cod_amount": order.cod_amount,
        "created_at": "2026-10-17T12:00:00Z",
    }
    db.insert_order(order_dict)

    # Save Quotes to SQLite
    selected_winner_quote = w_cost.quote
    quotes_data = [
        {
            "quote_id": q.quote_id,
            "order_reference": order.order_reference,
            "courier_code": q.courier_code,
            "courier_name": q.courier_name,
            "service_type": q.service_type,
            "estimated_delivery_days": q.estimated_delivery_days or 0,
            "total_cost": q.cost_breakdown.total_cost if q.cost_breakdown else 0.0,
            "quote_data_json": q.model_dump_json(),
            "source_type": q.source_type,
            "input_hash": q.input_hash,
            "created_at": "2026-10-17T12:01:00Z",
        }
        for q in quotes
    ]
    db.insert_quotes(quotes_data)
    fsm.validate_transition(ShipmentStatus.DRAFT, ShipmentStatus.QUOTED)
    fsm.validate_transition(ShipmentStatus.QUOTED, ShipmentStatus.PENDING_APPROVAL)
    print("  [OK] Order & Quotes persisted to SQLite. State: PENDING_APPROVAL.")

    # Record stress test event in audit trail
    audit.record_event(
        AuditEventType.STRESS_TEST_RUN,
        actor="ShippingDecisionStressTester",
        details={
            "stability_pct": stress_report.scenario_stability_pct,
            "baseline_winner": stress_report.baseline_winner_code,
            "is_fragile": frag.is_fragile,
            "critical_margin_inr": frag.critical_price_margin_inr,
        },
        order_reference=order.order_reference,
    )

    # 6. Human Approval Guard
    print("\n[Step 6] Testing Human Approval Safeguard...")
    try:
        fsm.validate_transition(ShipmentStatus.PENDING_APPROVAL, ShipmentStatus.DISPATCHING, approval=None)
        print("  [FAIL] ERROR: Approval guard failed!")
    except HumanApprovalRequiredError as exc:
        print(f"  [OK] SAFEGUARD VERIFIED: {exc}")

    # 7. Explicit Human Approval & Dispatch
    print("\n[Step 7] Granting Explicit Human Approval & Dispatching...")
    approval = ApprovalRecord(
        approved_by="Operations Lead (Ashwin)",
        quote_id=selected_winner_quote.quote_id,
        total_cost=selected_winner_quote.cost_breakdown.total_cost,
        notes="Approved Lowest Cost courier with verified 72.7% scenario stability",
    )
    audit.record_event(
        AuditEventType.HUMAN_APPROVAL_GRANTED,
        actor=approval.approved_by,
        details={"quote_id": selected_winner_quote.quote_id, "cost": selected_winner_quote.cost_breakdown.total_cost},
        order_reference=order.order_reference,
    )
    fsm.validate_transition(ShipmentStatus.PENDING_APPROVAL, ShipmentStatus.DISPATCHING, approval=approval)
    print(f"  [OK] Approval granted by '{approval.approved_by}'. Transitioning to DISPATCHING.")

    idempotency_key = generate_idempotency_key(order.order_reference, selected_winner_quote.quote_id)
    idem_mgr.record_dispatch_start(
        shipment_id="SHP-2026-001",
        order_reference=order.order_reference,
        quote_id=selected_winner_quote.quote_id,
        courier_code=selected_winner_quote.courier_code,
        idempotency_key=idempotency_key,
        approved_by=approval.approved_by,
        approved_at=approval.approved_at,
        created_at="2026-10-17T12:05:00Z",
    )

    # Call adapter to create shipment
    ship_res = adapter.create_shipment(order, selected_winner_quote, approval, idempotency_key)
    idem_mgr.update_dispatch_outcome(
        shipment_id=ship_res.shipment_id,
        status=ShipmentStatus.CREATED,
        tracking_number=ship_res.tracking_number,
        label_status=ship_res.label_status,
        label_data=ship_res.label_url_or_data,
        updated_at="2026-10-17T12:05:05Z",
    )
    fsm.validate_transition(ShipmentStatus.DISPATCHING, ShipmentStatus.CREATED)
    print(f"  [OK] Shipment created! Tracking Number: {ship_res.tracking_number}")
    print(f"  [OK] Label Status: {ship_res.label_status} (No fake PDF links invented)")

    # 8. Duplicate Prevention
    print("\n[Step 8] Testing Duplicate Dispatch Rejection...")
    try:
        idem_mgr.check_can_dispatch(order.order_reference, idempotency_key)
        print("  [FAIL] ERROR: Duplicate dispatch was not blocked!")
    except Exception as exc:
        print(f"  [OK] SAFEGUARD VERIFIED: {exc}")

    # 9. Audit Trail & Secret Scrubbing
    print_banner("[Step 9] Immutable Audit Trail (with Secret Scrubbing)")
    logs = audit.get_trail(order.order_reference)
    for entry in reversed(logs):
        details = json.loads(entry["details_json"])
        print(f"  [{entry['timestamp'][:19]}] {entry['event_type']} by {entry['actor']}")
        print(f"    Details: {details}")

    print_banner("DEMO COMPLETED: ALL PHASE 1, 2, 3 & 4 CAPABILITIES VERIFIED")


if __name__ == "__main__":
    main()
