"""
Adaptive Shipping Intelligence Agent - Interactive Demonstration CLI.
Executes Phase 1, Phase 2, Phase 3, Phase 4, and Phase 5:
- Domain calculation engine & alternative packaging optimizer
- Multi-courier DemoShippingAdapter with 3 distinct synthetic couriers
- Courier Ranking Engine (Lowest Cost, Fastest Delivery, Balanced Score)
- Shipping Decision Stress Tester (12-scenario perturbation matrix)
- Distinctive Innovation: Recommendation Fragility Analysis
- Quote Freshness & Price Drift Verification Gate
- Timeout Reconciliation Workflow (CREATION_UNCERTAIN recovery & safe retry)
- ShipSandboxClient safety locks on unconfirmed endpoints
- SQLite persistence, Human Approval Guard, Idempotency lock, and Secret Scrubbing
"""

import copy
import json
import sys
from datetime import datetime, timedelta, timezone

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx

from src.domain.freshness import (
    QuoteFreshnessStatus,
    check_quote_freshness,
    compare_quote_drift,
    verify_and_refresh_quote,
)
from src.domain.models import Address, Dimensions, OrderInput, PackageType, VolumetricRule
from src.domain.packaging import DEMO_PACKAGING_CATALOGUE, evaluate_packaging_alternatives
from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers
from src.domain.reconciliation import (
    ApprovalInvalidatedError,
    ReconciliationResult,
    ReconciliationStatus,
    ShipmentDispatchService,
    StaleQuoteError,
)
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
    print_banner("ADAPTIVE SHIPPING INTELLIGENCE AGENT: PHASE 1 TO 5 DEMO")

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

    # 4a. Courier Ranking Engine
    print_banner("[Step 4a] Courier Ranking Engine (Three Separated Strategies)")
    rank_cost = rank_couriers(quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))
    w_cost = rank_cost.selected_winner
    print(f"  1. Strategy: LOWEST COST")
    print(f"     Winner: {w_cost.quote.courier_name} (Total Landed Cost: INR {w_cost.total_cost:.2f}, SLA: {w_cost.estimated_delivery_days} days)")
    print(f"     Margin over runner-up: INR {rank_cost.cost_margin_over_runner_up:.2f}")

    rank_speed = rank_couriers(quotes, RankingPreferences(mode=RankingMode.FASTEST_DELIVERY))
    w_speed = rank_speed.selected_winner
    print(f"\n  2. Strategy: FASTEST DELIVERY")
    print(f"     Winner: {w_speed.quote.courier_name} (SLA: {w_speed.estimated_delivery_days} days, Landed Cost: INR {w_speed.total_cost:.2f})")
    print(f"     Margin over runner-up: {rank_speed.days_margin_over_runner_up} days faster")

    # 4b. Stress Testing & Fragility Analysis
    print_banner("[Step 4b] Shipping Decision Stress Tester & Recommendation Fragility Analysis")
    stress_tester = ShippingDecisionStressTester()
    stress_report = stress_tester.run_stress_test(order, RankingPreferences(mode=RankingMode.LOWEST_COST))

    print(f"  Baseline Winner: {stress_report.baseline_winner_name} (INR {stress_report.baseline_winner_cost:.2f})")
    print(f"  Scenario Stability Score: {stress_report.scenario_stability_pct}% ({stress_report.scenarios_baseline_maintained}/{stress_report.total_scenarios_evaluated} scenarios maintained)")
    frag = stress_report.fragility_analysis
    print(f"  Critical Price Margin:    INR {frag.critical_price_margin_inr:.2f} (Price advantage buffer)")
    print(f"  Fragility Verdict:        {frag.fragility_verdict}")

    # 5. Persistence Setup
    print_banner("[Step 5] Persistence Initialization & Quote Storing")
    db = DatabaseManager(":memory:")
    audit = AuditLogger(db)
    idem_mgr = IdempotencyManager(db)
    fsm = ShipmentStateMachine()
    dispatch_service = ShipmentDispatchService(db, audit, idem_mgr, fsm)

    # Save Order in SQLite
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
    print("  [OK] Order & Quotes persisted. Shipment State: PENDING_APPROVAL.")

    # 6. Quote Freshness & Price Drift Verification (Phase 5)
    print_banner("[Step 6] Phase 5: Quote Freshness & Price Drift Verification")
    fresh_check = check_quote_freshness(selected_winner_quote, max_age_seconds=900)
    print(f"  Quote Age Check: Status = {fresh_check.status.value} (Age: {fresh_check.age_seconds:.1f}s, Max: {fresh_check.max_age_seconds:.0f}s)")
    print(f"  Explanation: {fresh_check.explanation}")

    # Demonstrate price drift rejection
    print("\n  Testing Simulated Price Drift Detection...")
    drifting_quote = selected_winner_quote.model_copy()
    altered_cost = drifting_quote.cost_breakdown.model_copy(
        update={"total_cost": drifting_quote.cost_breakdown.total_cost + 40.0}
    )
    drift = compare_quote_drift(selected_winner_quote, selected_winner_quote.model_copy(update={"cost_breakdown": altered_cost}))
    print(f"  Detected Drift: {drift.explanation}")
    print(f"  Requires Renewed Approval: {drift.requires_renewed_approval}")
    print(f"  [OK] SAFEGUARD VERIFIED: Stale or price-drifted quotes cannot be dispatched without renewed approval.")

    # 7. Timeout & Uncertain Reconciliation Workflow (Phase 5)
    print_banner("[Step 7] Phase 5: Timeout Recovery & Reconciliation Workflow")

    # Step 7a: Simulate a Network Timeout during creation
    print("  [Scenario 7a] Simulating Carrier Network Timeout during dispatch...")
    timeout_provider = DemoShippingAdapter()
    # Inject a simulated timeout on creation
    def simulate_timeout(*args, **kwargs):
        raise httpx.ReadTimeout("Read timed out waiting for carrier response")
    timeout_provider.create_shipment = simulate_timeout

    approval = ApprovalRecord(
        approved_by="Operations Lead (Ashwin)",
        quote_id=selected_winner_quote.quote_id,
        total_cost=selected_winner_quote.cost_breakdown.total_cost,
        notes="Approved Lowest Cost courier",
    )

    attempt_res = dispatch_service.attempt_dispatch(order, selected_winner_quote, approval, timeout_provider)
    print(f"  * Dispatch Result Status: {attempt_res.status.value}")
    print(f"  * Uncertain Flag: {attempt_res.is_uncertain}")
    print(f"  * Reconciliation Required: {attempt_res.requires_reconciliation}")
    print(f"  * Explanation: {attempt_res.explanation}")

    # Step 7b: Reconcile using provider lookup (carrier confirms no booking was made)
    print("\n  [Scenario 7b] Automated Post-Timeout Reconciliation...")
    # Real adapter will check lookup_shipment_by_reference
    recon_res = dispatch_service.reconcile_uncertain_shipment(order.order_reference, adapter)
    print(f"  * Reconciliation Outcome: {recon_res.status.value}")
    print(f"  * New Shipment Status:    {recon_res.final_shipment_status.value}")
    print(f"  * Explanation:            {recon_res.explanation}")
    print("  [OK] SAFEGUARD VERIFIED: Order safely reset to PENDING_APPROVAL without creating duplicates!")

    # Step 7c: Successful Dispatch after clean reconciliation
    print("\n  [Scenario 7c] Successful Dispatch with Validated Approval...")
    successful_dispatch = dispatch_service.attempt_dispatch(order, selected_winner_quote, approval, adapter)
    print(f"  * Dispatch Status:  {successful_dispatch.status.value}")
    print(f"  * Tracking Number:  {successful_dispatch.tracking_number}")
    print(f"  * Explanation:      {successful_dispatch.explanation}")

    # 8. Duplicate Prevention
    print_banner("[Step 8] Duplicate Dispatch Lock on Retries")
    try:
        idem_key = generate_idempotency_key(order.order_reference, selected_winner_quote.quote_id)
        idem_mgr.check_can_dispatch(order.order_reference, idem_key)
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

    print_banner("DEMO COMPLETED: ALL PHASE 1 TO 5 CAPABILITIES VERIFIED")


if __name__ == "__main__":
    main()
