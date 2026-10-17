"""
Adaptive Shipping Intelligence Agent — Streamlit Web Interface (Phase 6).

Entry-point: streamlit run src/ui/app.py

Tabs
  1. Order Entry & Rate Comparison
  2. Packaging Optimizer
  3. Decision Stress Testing Lab
  4. Approval & Dispatch Gate
  5. Shipment Tracker & Audit Trail

All business logic is delegated to the existing domain modules.
No calculations are duplicated here.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

# ── Domain imports ──────────────────────────────────────────────────────────
from src.domain.freshness import check_quote_freshness
from src.domain.models import (
    Dimensions,
    OrderInput,
    PackageType,
    ShipmentStatus,
)
from src.domain.packaging import (
    DEMO_PACKAGING_CATALOGUE,
    evaluate_packaging_alternatives,
)
from src.domain.ranking import RankingMode, RankingPreferences, rank_couriers
from src.domain.reconciliation import (
    ApprovalInvalidatedError,
    ReconciliationStatus,
    ShipmentDispatchService,
    StaleQuoteError,
)
from src.domain.stress_tester import ShippingDecisionStressTester
from src.domain.weight import compute_weight_breakdown
from src.persistence.audit import AuditEventType, AuditLogger
from src.persistence.database import DatabaseManager
from src.persistence.idempotency import IdempotencyManager
from src.persistence.state_machine import ApprovalRecord, ShipmentStateMachine
from src.providers.registry import get_provider

# ── UI helpers (pure functions, no Streamlit) ────────────────────────────────
from src.ui.helpers import (
    build_order_input_from_form,
    describe_ranking_result,
    fmt_inr,
    fmt_kg,
    format_audit_event,
    format_cost_breakdown,
    format_weight_breakdown,
    freshness_badge,
    quote_source_badge,
    shipment_status_colour,
)

# ── Page Configuration ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Adaptive Shipping Intelligence Agent",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Shared SQLite database (file-based for persistence across reruns) ──────────
_DB_PATH = "shipping_agent.db"


@st.cache_resource
def get_db() -> DatabaseManager:
    return DatabaseManager(_DB_PATH)


@st.cache_resource
def get_services():
    db = get_db()
    audit = AuditLogger(db)
    idem = IdempotencyManager(db)
    fsm = ShipmentStateMachine()
    dispatch = ShipmentDispatchService(db, audit, idem, fsm)
    return db, audit, idem, fsm, dispatch


# ── Session state initialisation ──────────────────────────────────────────────
def _init_session():
    defaults = {
        "order": None,           # OrderInput
        "quotes": [],            # list[CourierQuote]
        "ranking_result": None,  # RankingResult
        "selected_quote": None,  # CourierQuote
        "stress_report": None,   # StressTestSummaryReport
        "dispatch_result": None, # DispatchAttemptResult
        "recon_result": None,    # ReconciliationResult
        "approval_given": False,
        "approved_by": "",
        "saved_order_id": None,
        "form_errors": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_session()


# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.image("https://raw.githubusercontent.com/twitter/twemoji/master/assets/svg/1f4e6.svg", width=48)
        st.title("Adaptive Shipping\nIntelligence Agent")
        st.caption("Phase 6 — Streamlit UI")
        st.divider()
        st.markdown("""
**Navigation**
1. 📝 Order Entry & Rates
2. 📦 Packaging Optimizer
3. 🧪 Stress Testing Lab
4. ✅ Approval & Dispatch
5. 📋 Shipment History
""")
        st.divider()
        st.warning(
            "**DEMO DATA MODE**\n\nAll courier rates shown are "
            "synthetic and produced by the Demo Shipping Adapter. "
            "They are NOT live carrier prices.",
            icon="🔵",
        )
        st.caption("Provider: Demo Multi-Courier Adapter (Simulated)")
        if st.button("🗑️ Reset Session", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            _init_session()
            st.rerun()


# ── Tab 1: Order Entry & Rate Comparison ─────────────────────────────────────
def render_order_entry():
    st.header("📝 Order Entry & Courier Rate Comparison")

    st.info(
        "Enter shipment details below. Rates are fetched from the **Demo Shipping Adapter** "
        "(simulated) — clearly labelled **DEMO DATA**.",
        icon="ℹ️",
    )

    with st.form("order_form"):
        st.subheader("Order Reference")
        order_ref = st.text_input(
            "Order Reference *",
            value=st.session_state.get("_draft_order_ref", f"ORD-{uuid.uuid4().hex[:8].upper()}"),
            help="Unique merchant reference for this shipment.",
        )

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Sender Details")
            sender_name = st.text_input("Full Name / Business *", key="s_name")
            sender_phone = st.text_input("Phone (10–15 digits) *", key="s_phone")
            sender_street = st.text_input("Street Address *", key="s_street")
            sender_street2 = st.text_input("Street Line 2 (optional)", key="s_street2")
            sender_city = st.text_input("City *", key="s_city")
            sender_state = st.text_input("State *", key="s_state")
            sender_pin = st.text_input("PIN Code (6 digits) *", key="s_pin", max_chars=6)

        with col2:
            st.subheader("Recipient Details")
            recip_name = st.text_input("Full Name / Business *", key="r_name")
            recip_phone = st.text_input("Phone (10–15 digits) *", key="r_phone")
            recip_street = st.text_input("Street Address *", key="r_street")
            recip_street2 = st.text_input("Street Line 2 (optional)", key="r_street2")
            recip_city = st.text_input("City *", key="r_city")
            recip_state = st.text_input("State *", key="r_state")
            recip_pin = st.text_input("PIN Code (6 digits) *", key="r_pin", max_chars=6)

        st.divider()
        st.subheader("Parcel Details")
        pcol1, pcol2, pcol3, pcol4 = st.columns(4)
        with pcol1:
            weight = st.number_input("Actual Weight (kg) *", min_value=0.01, max_value=1000.0, value=1.8, step=0.1)
        with pcol2:
            length = st.number_input("Length (cm) *", min_value=0.1, max_value=500.0, value=35.0, step=0.5)
        with pcol3:
            width = st.number_input("Width (cm) *", min_value=0.1, max_value=500.0, value=25.0, step=0.5)
        with pcol4:
            height = st.number_input("Height (cm) *", min_value=0.1, max_value=500.0, value=20.0, step=0.5)

        pcol5, pcol6, pcol7 = st.columns(3)
        with pcol5:
            pkg_type = st.selectbox(
                "Package Type *",
                options=[p.value for p in PackageType],
                format_func=lambda x: x.replace("_", " ").title(),
            )
        with pcol6:
            declared_value = st.number_input("Declared Value (INR)", min_value=0.0, value=0.0, step=100.0)
        with pcol7:
            product_desc = st.text_input("Product Description", value="General Merchandise")

        st.subheader("Payment Mode")
        is_cod = st.toggle("Cash on Delivery (COD)", value=False)
        cod_amount = 0.0
        if is_cod:
            cod_amount = st.number_input("COD Amount to Collect (INR) *", min_value=0.01, value=1500.0, step=50.0)

        st.divider()
        st.subheader("Ranking Preferences")
        rcol1, rcol2, rcol3 = st.columns(3)
        with rcol1:
            rank_mode = st.selectbox(
                "Primary Ranking Strategy",
                options=[m.value for m in RankingMode],
                format_func=lambda x: x.replace("_", " ").title(),
            )
        with rcol2:
            max_budget = st.number_input("Max Budget (INR, 0 = no limit)", min_value=0.0, value=0.0, step=50.0)
        with rcol3:
            max_days = st.number_input("Max Delivery Days (0 = no limit)", min_value=0, value=0, step=1)

        submitted = st.form_submit_button("🔍 Get Courier Rates", type="primary", use_container_width=True)

    if submitted:
        form_data = {
            "order_reference": order_ref,
            "sender_name": sender_name,
            "sender_phone": sender_phone,
            "sender_street": sender_street,
            "sender_street2": sender_street2,
            "sender_city": sender_city,
            "sender_state": sender_state,
            "sender_pin": sender_pin,
            "recipient_name": recip_name,
            "recipient_phone": recip_phone,
            "recipient_street": recip_street,
            "recipient_street2": recip_street2,
            "recipient_city": recip_city,
            "recipient_state": recip_state,
            "recipient_pin": recip_pin,
            "parcel_weight_kg": weight,
            "length_cm": length,
            "width_cm": width,
            "height_cm": height,
            "package_type": pkg_type,
            "is_cod": is_cod,
            "cod_amount": cod_amount,
            "declared_value": declared_value,
            "product_description": product_desc,
        }

        order, errors = build_order_input_from_form(form_data)

        if errors:
            for err in errors:
                st.error(f"❌ {err}")
            st.session_state["form_errors"] = errors
            return

        st.session_state["form_errors"] = []
        st.session_state["order"] = order
        st.session_state["approval_given"] = False
        st.session_state["dispatch_result"] = None
        st.session_state["recon_result"] = None

        # Fetch quotes
        with st.spinner("Fetching courier rates from Demo Adapter…"):
            provider = get_provider("demo")
            quotes = provider.get_rates(order)
            st.session_state["quotes"] = quotes

        # Persist order + quotes in DB
        db, audit, _, _, _ = get_services()
        order_id = f"ORD-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            db.insert_order({
                "order_id": order_id,
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
                "created_at": now_iso,
            })
            st.session_state["saved_order_id"] = order_id
        except Exception:
            pass  # Order may already exist if same reference

        try:
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
                    "created_at": now_iso,
                }
                for q in quotes
            ]
            db.insert_quotes(quotes_data)
            audit.record_event(
                AuditEventType.QUOTES_RECEIVED,
                "DemoAdapter",
                {"quote_count": len(quotes), "order_reference": order.order_reference},
                order_reference=order.order_reference,
            )
        except Exception:
            pass

        # Run ranking
        prefs = RankingPreferences(
            mode=RankingMode(rank_mode),
            max_budget=max_budget if max_budget > 0 else None,
            max_delivery_days=int(max_days) if max_days > 0 else None,
        )
        ranking_result = rank_couriers(quotes, prefs)
        st.session_state["ranking_result"] = ranking_result
        st.session_state["selected_quote"] = (
            ranking_result.selected_winner.quote if ranking_result.selected_winner else None
        )

        st.success(f"✅ Rates fetched for **{order.order_reference}**. Scroll down to review.")

    # ── Display quotes if available ──────────────────────────────────────────
    if st.session_state.get("quotes") and st.session_state.get("order"):
        _render_rate_comparison()


def _render_rate_comparison():
    order: OrderInput = st.session_state["order"]
    quotes = st.session_state["quotes"]
    ranking_result = st.session_state["ranking_result"]

    st.divider()
    st.subheader("📊 Courier Rate Comparison")
    st.caption(quote_source_badge(quotes[0]) if quotes else "")

    if ranking_result and ranking_result.selected_winner:
        with st.container(border=True):
            st.markdown("### 🏆 Recommended Courier")
            st.markdown(describe_ranking_result(ranking_result))

    # Eligible couriers
    eligible = [q for q in quotes if q.is_eligible]
    ineligible = [q for q in quotes if not q.is_eligible]

    if eligible:
        st.markdown("#### ✅ Eligible Couriers (Ranked)")
        for idx, quote in enumerate(sorted(eligible, key=lambda q: q.cost_breakdown.total_cost if q.cost_breakdown else 1e9)):
            is_winner = (
                ranking_result
                and ranking_result.selected_winner
                and quote.quote_id == ranking_result.selected_winner.quote.quote_id
            )
            label = f"🥇 #{idx + 1} — {quote.courier_name}" if is_winner else f"#{idx + 1} — {quote.courier_name}"
            with st.expander(label + (" 🏆 **RECOMMENDED**" if is_winner else ""), expanded=is_winner):
                _render_quote_card(quote)

    if ineligible:
        st.markdown("#### ❌ Ineligible Couriers (Excluded from Ranking)")
        for quote in ineligible:
            with st.expander(f"❌ {quote.courier_name} — {quote.unserviceable_reason or 'Ineligible'}"):
                st.error(f"**Reason:** {quote.unserviceable_reason or 'Unserviceable or missing data'}")
                st.caption(f"Quote ID: `{quote.quote_id}`")
                st.caption(quote_source_badge(quote))
                if quote.missing_evidence:
                    st.caption("Missing evidence: " + "; ".join(quote.missing_evidence))

    # Quote selection for approval tab
    st.divider()
    eligible_names = [f"{q.courier_name} — {fmt_inr(q.cost_breakdown.total_cost)}" for q in eligible if q.cost_breakdown]
    if eligible_names and eligible:
        st.markdown("#### 🎯 Select Courier for Approval & Dispatch")
        selected_label = st.selectbox(
            "Choose courier to approve & dispatch",
            options=eligible_names,
            key="selected_courier_label",
        )
        if selected_label:
            idx = eligible_names.index(selected_label)
            st.session_state["selected_quote"] = [q for q in eligible if q.cost_breakdown][idx]
            sel = st.session_state["selected_quote"]
            badge_label, badge_colour = freshness_badge(sel)
            st.caption(f"Quote freshness: :{badge_colour}[{badge_label}]")


def _render_quote_card(quote):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Service:** {quote.service_type}")
        st.markdown(f"**Source:** {quote_source_badge(quote)}")
        badge_label, badge_colour = freshness_badge(quote)
        st.markdown(f"**Freshness:** :{badge_colour}[{badge_label}]")
        if quote.estimated_delivery_days:
            st.markdown(f"**Estimated Delivery:** {quote.estimated_delivery_days} business days")
        st.caption(f"Quote ID: `{quote.quote_id}`")
        st.caption(f"Retrieved at: {quote.retrieval_timestamp[:19]} UTC")
    with col2:
        if quote.cost_breakdown:
            st.markdown("**Fee Breakdown:**")
            cb = format_cost_breakdown(quote.cost_breakdown)
            for label, val in cb.items():
                st.markdown(f"- {label}: {val}")
        if quote.weight_result:
            st.markdown("**Weight Calculation:**")
            wb = format_weight_breakdown(quote.weight_result)
            for label, val in wb.items():
                st.markdown(f"- {label}: {val}")

    if quote.missing_evidence:
        st.caption("⚠️ Missing evidence: " + "; ".join(quote.missing_evidence))
    if quote.decision_explanation:
        st.caption(f"_Explanation: {quote.decision_explanation}_")
    st.caption(f"_{quote.disclaimer}_")


# ── Tab 2: Packaging Optimizer ────────────────────────────────────────────────
def render_packaging_optimizer():
    st.header("📦 Packaging Optimizer")
    st.warning(
        "**DEMO PACKAGING OPTIONS** — Box sizes below are configurable examples for demonstration only. "
        "They are not official courier packaging standards.",
        icon="📦",
    )

    order: Optional[OrderInput] = st.session_state.get("order")

    if order is None:
        st.info("ℹ️ Please complete Order Entry first to load parcel details.")
        return

    st.success(f"Loaded parcel from **{order.order_reference}**: "
               f"{order.dimensions.length_cm} × {order.dimensions.width_cm} × {order.dimensions.height_cm} cm, "
               f"{order.parcel_weight_kg} kg")

    st.subheader("Product Net Dimensions")
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        prod_l = st.number_input("Product Length (cm)", min_value=0.1, value=22.0, step=0.5)
    with pc2:
        prod_w = st.number_input("Product Width (cm)", min_value=0.1, value=16.0, step=0.5)
    with pc3:
        prod_h = st.number_input("Product Height (cm)", min_value=0.1, value=10.0, step=0.5)
    with pc4:
        prod_weight = st.number_input("Product Net Weight (kg)", min_value=0.01, value=order.parcel_weight_kg, step=0.1)

    freight_estimate = st.number_input(
        "Per-kg Freight Rate Estimate (INR, for savings calculation)",
        min_value=1.0, value=50.0, step=5.0,
        help="Used only for comparative savings estimate — not a live rate.",
    )

    if st.button("📐 Evaluate Packaging Alternatives", type="primary"):
        product_dims = Dimensions(length_cm=prod_l, width_cm=prod_w, height_cm=prod_h)
        with st.spinner("Evaluating packaging options…"):
            results = evaluate_packaging_alternatives(
                product_dimensions=product_dims,
                product_net_weight_kg=prod_weight,
                current_outer_dimensions=order.dimensions,
                volumetric_rule=None,  # uses default 5000 cm³/kg demo rule
                per_kg_freight_estimate_inr=freight_estimate,
            )

        db, audit, _, _, _ = get_services()
        try:
            audit.record_event(
                AuditEventType.PACKAGING_OPTIMIZED,
                "PackagingOptimizer",
                {"product_dims": f"{prod_l}x{prod_w}x{prod_h}", "alternatives_evaluated": len(results)},
                order_reference=order.order_reference,
            )
        except Exception:
            pass

        feasible = [r for r in results if r.is_feasible]
        infeasible = [r for r in results if not r.is_feasible]

        st.divider()
        st.subheader("Results")
        if feasible:
            best = min(feasible, key=lambda r: r.new_billable_slab_kg)
            st.success(f"**Best option:** {best.box_name} — saves {fmt_inr(best.estimated_freight_savings_inr)} in estimated freight.")

            st.markdown("#### ✅ Feasible Packaging Options")
            for r in sorted(feasible, key=lambda r: r.estimated_freight_savings_inr, reverse=True):
                with st.expander(f"✅ {r.box_name} — saves {fmt_inr(r.estimated_freight_savings_inr)}", expanded=(r == best)):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric("Volume Reduction", f"{r.volume_reduction_pct:.1f}%")
                        st.metric("Dead Space Saved", f"{r.volume_difference_cm3:,.0f} cm³")
                    with col2:
                        st.metric("Billable Slab (Before)", fmt_kg(r.original_billable_slab_kg))
                        st.metric("Billable Slab (After)", fmt_kg(r.new_billable_slab_kg))
                    st.metric("Estimated Freight Savings", fmt_inr(r.estimated_freight_savings_inr))
                    st.caption(f"_{r.disclaimer}_")
        else:
            st.warning("No feasible packaging alternatives found in the demo catalogue for these product dimensions.")

        if infeasible:
            st.markdown("#### ❌ Infeasible Options")
            for r in infeasible:
                st.caption(f"❌ **{r.box_name}**: {r.rejection_reason}")

        st.caption("⚠️ All savings are estimates based on the configured per-kg rate. Not actual carrier invoices.")

    # Show catalogue
    with st.expander("📋 View Demo Packaging Catalogue"):
        for opt in DEMO_PACKAGING_CATALOGUE:
            st.markdown(
                f"**{opt.box_name}** — Outer: "
                f"{opt.outer_dimensions.length_cm}×{opt.outer_dimensions.width_cm}×{opt.outer_dimensions.height_cm} cm | "
                f"Tare: {opt.tare_weight_kg} kg | Type: {opt.package_type.value}"
            )
        st.caption("_These are configurable demo examples, not official courier packaging standards._")


# ── Tab 3: Stress Testing Lab ─────────────────────────────────────────────────
def render_stress_testing():
    st.header("🧪 Shipping Decision Stress Testing Lab")
    st.warning(
        "**SIMULATION ONLY** — This lab evaluates how courier recommendations change under "
        "hypothetical perturbations. Scenario stability scores are **NOT** empirical "
        "statistical probabilities of real-world outcomes.",
        icon="🧪",
    )

    order: Optional[OrderInput] = st.session_state.get("order")

    if order is None:
        st.info("ℹ️ Please complete Order Entry first to load parcel and quotes.")
        return

    st.success(f"Loaded order: **{order.order_reference}**")

    rank_mode = st.selectbox(
        "Ranking strategy for stress test baseline",
        options=[m.value for m in RankingMode],
        format_func=lambda x: x.replace("_", " ").title(),
        key="stress_rank_mode",
    )

    if st.button("🚀 Run Stress Test (12 scenarios)", type="primary"):
        prefs = RankingPreferences(mode=RankingMode(rank_mode))
        tester = ShippingDecisionStressTester()

        with st.spinner("Running 12 stress scenarios across dimensional, price, and constraint perturbations…"):
            try:
                report = tester.run_stress_test(order, prefs)
                st.session_state["stress_report"] = report
            except Exception as exc:
                st.error(f"Stress test failed: {exc}")
                return

        db, audit, _, _, _ = get_services()
        try:
            audit.record_event(
                AuditEventType.STRESS_TEST_RUN,
                "StressTester",
                {
                    "scenarios": report.total_scenarios_evaluated,
                    "stability_pct": report.scenario_stability_pct,
                    "is_fragile": report.fragility_analysis.is_fragile,
                },
                order_reference=order.order_reference,
            )
        except Exception:
            pass

    report = st.session_state.get("stress_report")
    if report is None:
        return

    st.divider()
    # ── Summary metrics ──────────────────────────────────────────────────────
    st.subheader("📈 Stress Test Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Baseline Winner", report.baseline_winner_name)
    m2.metric("Baseline Cost", fmt_inr(report.baseline_winner_cost))
    m3.metric("Scenario Stability", f"{report.scenario_stability_pct:.0f}%",
              help="% of tested scenarios where baseline winner stays #1. NOT a probability.")
    m4.metric("Scenarios Tested", report.total_scenarios_evaluated)

    st.caption(f"_{report.disclaimer}_")

    # ── Fragility Analysis ───────────────────────────────────────────────────
    st.subheader("🔬 Recommendation Fragility Analysis")
    frag = report.fragility_analysis
    frag_col1, frag_col2 = st.columns(2)
    with frag_col1:
        colour = "🔴" if frag.is_fragile else "🟢"
        st.markdown(f"**Fragility Verdict:** {colour} {frag.fragility_verdict}")
        st.markdown(f"**Most Sensitive Input:** {frag.most_sensitive_input}")
        if frag.critical_price_margin_inr is not None:
            st.metric(
                "Critical Price Margin",
                fmt_inr(frag.critical_price_margin_inr),
                help="Cost advantage buffer. Recommendation switches if winner's price rises by this amount.",
            )
    with frag_col2:
        st.markdown(f"**Packaging Stabilization:** {frag.packaging_stabilization_effect}")
        if frag.packaging_savings_potential_inr > 0:
            st.metric("Packaging Savings Potential", fmt_inr(frag.packaging_savings_potential_inr))
        if frag.insufficient_evidence_flags:
            st.warning("Data quality flags: " + "; ".join(frag.insufficient_evidence_flags))
    st.caption(f"_{frag.limitations_disclaimer}_")

    # ── Scenario Outcome Table ───────────────────────────────────────────────
    st.subheader("📋 All Scenario Outcomes")
    try:
        import pandas as pd
        rows = []
        for sc in report.scenario_outcomes:
            rows.append({
                "Scenario": sc.scenario.scenario_name,
                "Category": sc.scenario.category,
                "Winner": sc.winner_name or "— None —",
                "Cost (INR)": round(sc.winner_cost, 2) if sc.winner_cost else None,
                "Days": sc.winner_transit_days,
                "Switched?": "⚠️ Yes" if sc.rank_switched else "✅ No",
                "Cause": sc.causative_factor or "—",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    except ImportError:
        for sc in report.scenario_outcomes:
            switch_icon = "⚠️" if sc.rank_switched else "✅"
            st.markdown(
                f"**{switch_icon} {sc.scenario.scenario_name}** | "
                f"Winner: {sc.winner_name or '—'} | Cost: {fmt_inr(sc.winner_cost) if sc.winner_cost else '—'}"
            )

    st.caption(
        "⚠️ **Important**: Stress test scenarios are deterministic hypothetical perturbations. "
        "Stability % counts tested scenarios, NOT real-world probability distributions."
    )


# ── Tab 4: Approval & Dispatch Gate ──────────────────────────────────────────
def render_approval_dispatch():
    st.header("✅ Approval & Dispatch Gate")

    order: Optional[OrderInput] = st.session_state.get("order")
    selected_quote = st.session_state.get("selected_quote")

    if order is None or selected_quote is None:
        st.info("ℹ️ Complete Order Entry and select a courier in Tab 1 before proceeding here.")
        return

    # ── Quote Summary & Freshness Gate ───────────────────────────────────────
    st.subheader("📋 Selected Courier Summary")

    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Order Reference:** `{order.order_reference}`")
            st.markdown(f"**Courier:** {selected_quote.courier_name}")
            st.markdown(f"**Service:** {selected_quote.service_type}")
            st.markdown(f"**Quote ID:** `{selected_quote.quote_id}`")
            if selected_quote.estimated_delivery_days:
                st.markdown(f"**Est. Delivery:** {selected_quote.estimated_delivery_days} business days")
        with col2:
            if selected_quote.cost_breakdown:
                st.markdown(f"**Total Landed Cost:** **{fmt_inr(selected_quote.cost_breakdown.total_cost)}**")
                cb = format_cost_breakdown(selected_quote.cost_breakdown)
                for label, val in list(cb.items())[:-1]:
                    st.markdown(f"- {label}: {val}")
            badge_label, badge_colour = freshness_badge(selected_quote)
            st.markdown(f"**Quote Freshness:** :{badge_colour}[{badge_label}]")
        st.caption(quote_source_badge(selected_quote))
        if selected_quote.missing_evidence:
            st.warning("Missing evidence: " + "; ".join(selected_quote.missing_evidence))

    # Freshness warning
    freshness_check = check_quote_freshness(selected_quote, max_age_seconds=900)
    if not freshness_check.is_fresh:
        st.error(
            f"⛔ **Quote Expired** — {freshness_check.explanation}  \n"
            "Please return to Tab 1 and re-fetch rates before dispatching."
        )
        return

    st.success("✅ Quote is fresh and within validity window.")

    # ── DEMO DATA Notice ─────────────────────────────────────────────────────
    if selected_quote.is_demo_data:
        st.warning(
            "🔵 **DEMO DATA MODE** — This is a simulated shipment dispatch using synthetic demo data. "
            "No real carrier booking will be made. The tracking number generated is a demonstration reference only.",
            icon="🔵",
        )

    # ── Human Approval Form ──────────────────────────────────────────────────
    st.divider()
    st.subheader("🔐 Human Approval Required")
    st.info(
        "Dispatch cannot proceed without explicit human approval. "
        "Provide your name and confirm the total cost before booking.",
        icon="🔐",
    )

    dispatch_result = st.session_state.get("dispatch_result")

    # Block if already dispatched
    if dispatch_result and dispatch_result.status == ShipmentStatus.CREATED:
        st.success(f"✅ **Shipment already created!**")
        st.markdown(f"**Tracking Reference (DEMO):** `{dispatch_result.tracking_number}`")
        st.caption("_This is a simulated tracking reference from the Demo Adapter. Not a real carrier AWB._")
        st.markdown(f"**Explanation:** {dispatch_result.explanation}")
        return

    # Block unsafe retry on uncertain
    if dispatch_result and dispatch_result.status == ShipmentStatus.CREATION_UNCERTAIN:
        st.error(
            "⛔ **CREATION UNCERTAIN** — A previous dispatch attempt timed out or returned an ambiguous response. "
            "Unsafe retry is blocked to prevent duplicate bookings."
        )
        st.markdown(f"**Explanation:** {dispatch_result.explanation}")
        _render_reconciliation_panel(order, selected_quote)
        return

    with st.form("approval_form"):
        approved_by = st.text_input(
            "Approver Name / Operator ID *",
            help="Enter your name or employee ID. Min 2 characters.",
        )
        cost_confirmed = st.number_input(
            f"Confirm total cost (INR) *  [Quote shows: {fmt_inr(selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0)}]",
            min_value=0.0,
            value=float(selected_quote.cost_breakdown.total_cost) if selected_quote.cost_breakdown else 0.0,
            step=0.01,
        )
        notes = st.text_area("Approval Notes (optional)", placeholder="e.g., Approved per customer urgency request.")
        approval_checkbox = st.checkbox(
            f"I, **{approved_by or 'operator'}**, confirm dispatching this shipment via "
            f"**{selected_quote.courier_name}** for **{fmt_inr(cost_confirmed)}** (DEMO booking)."
        )
        submit_approval = st.form_submit_button("🚀 Approve & Dispatch Shipment", type="primary", use_container_width=True)

    if submit_approval:
        if not approved_by or len(approved_by.strip()) < 2:
            st.error("❌ Approver name must be at least 2 characters.")
            return
        if not approval_checkbox:
            st.error("❌ Please tick the confirmation checkbox to proceed.")
            return

        approval = ApprovalRecord(
            approved_by=approved_by.strip(),
            quote_id=selected_quote.quote_id,
            total_cost=cost_confirmed,
            notes=notes or None,
        )

        _, audit, _, _, dispatch_service = get_services()
        provider = get_provider("demo")

        try:
            audit.record_event(
                AuditEventType.HUMAN_APPROVAL_GRANTED,
                approved_by.strip(),
                {"quote_id": selected_quote.quote_id, "cost": cost_confirmed, "notes": notes},
                order_reference=order.order_reference,
            )
        except Exception:
            pass

        with st.spinner("Dispatching shipment via Demo Adapter…"):
            try:
                result = dispatch_service.attempt_dispatch(
                    order=order,
                    quote=selected_quote,
                    approval=approval,
                    provider=provider,
                    max_quote_age_seconds=900,
                )
                st.session_state["dispatch_result"] = result
                st.session_state["approval_given"] = True

                if result.status == ShipmentStatus.CREATED:
                    st.success(f"✅ **Shipment dispatched (DEMO)**  \nTracking Ref: `{result.tracking_number}`")
                    st.caption("_Simulated tracking reference only. Not a real carrier AWB._")
                    st.rerun()
                elif result.status == ShipmentStatus.CREATION_UNCERTAIN:
                    st.error("⛔ Dispatch result is UNCERTAIN (simulated timeout). Retry blocked. Use reconciliation.")
                    st.rerun()

            except ApprovalInvalidatedError as exc:
                st.error(f"⛔ **Approval Invalidated:** {exc}")
            except StaleQuoteError as exc:
                st.error(f"⛔ **Stale Quote:** {exc}  \nReturn to Tab 1 and re-fetch rates.")
            except Exception as exc:
                st.error(f"❌ Dispatch error: {exc}")


def _render_reconciliation_panel(order: OrderInput, selected_quote):
    st.divider()
    st.subheader("🔄 Timeout Reconciliation")
    st.info(
        "The previous dispatch attempt resulted in an uncertain outcome. "
        "Use automated reconciliation (if supported) or manually resolve.",
        icon="🔄",
    )

    _, _, _, _, dispatch_service = get_services()
    provider = get_provider("demo")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔍 Auto-Reconcile via Demo Adapter", use_container_width=True):
            with st.spinner("Querying Demo Adapter for existing booking…"):
                try:
                    recon = dispatch_service.reconcile_uncertain_shipment(order.order_reference, provider)
                    st.session_state["recon_result"] = recon
                    st.session_state["dispatch_result"] = None
                    st.rerun()
                except Exception as exc:
                    st.error(f"Reconciliation error: {exc}")

    with col2:
        with st.expander("🔧 Manual Operator Resolution"):
            resolution = st.radio("Resolution", ["CONFIRMED_NOT_BOOKED", "CONFIRMED_BOOKED"])
            manual_tracking = ""
            if resolution == "CONFIRMED_BOOKED":
                manual_tracking = st.text_input("Carrier-provided Tracking Number")
            manual_actor = st.text_input("Operator ID")
            manual_notes = st.text_area("Notes")
            if st.button("Apply Manual Resolution"):
                if not manual_actor or len(manual_actor.strip()) < 2:
                    st.error("Operator ID required.")
                elif resolution == "CONFIRMED_BOOKED" and not manual_tracking.strip():
                    st.error("Tracking number required when confirming a booking.")
                else:
                    try:
                        recon = dispatch_service.resolve_uncertain_shipment_manually(
                            order_reference=order.order_reference,
                            resolution=resolution,
                            actor=manual_actor.strip(),
                            tracking_number=manual_tracking.strip() or None,
                            notes=manual_notes or None,
                        )
                        st.session_state["recon_result"] = recon
                        st.session_state["dispatch_result"] = None
                        st.success(f"Resolution applied: {recon.status.value}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Manual resolution error: {exc}")

    recon_result = st.session_state.get("recon_result")
    if recon_result:
        st.divider()
        status_icon = "✅" if recon_result.status == ReconciliationStatus.RECONCILED_CREATED else "🔄"
        st.markdown(f"**{status_icon} Reconciliation Outcome:** {recon_result.status.value}")
        st.markdown(f"**Final Shipment Status:** {recon_result.final_shipment_status.value}")
        st.markdown(f"**Explanation:** {recon_result.explanation}")
        if recon_result.tracking_number:
            st.markdown(f"**Tracking Number:** `{recon_result.tracking_number}`")
        if recon_result.requires_manual_resolution:
            st.warning("⚠️ Manual operator resolution still required.")


# ── Tab 5: Shipment History & Audit Trail ─────────────────────────────────────
def render_history():
    st.header("📋 Shipment & Audit History")
    st.info("Records are stored in the local SQLite database (`shipping_agent.db`).", icon="🗄️")

    db, audit, _, _, _ = get_services()

    tab_orders, tab_quotes, tab_shipments, tab_audit = st.tabs(
        ["📦 Orders", "💰 Quotes", "🚚 Shipments", "📜 Audit Trail"]
    )

    # Orders Tab
    with tab_orders:
        st.subheader("Stored Orders")
        order_ref_filter = st.text_input("Filter by Order Reference", key="hist_order_filter")
        try:
            if order_ref_filter:
                row = db.get_order(order_ref_filter.strip())
                orders = [row] if row else []
            else:
                with db.connection() as conn:
                    rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 50").fetchall()
                    orders = [dict(r) for r in rows]
        except Exception as exc:
            orders = []
            st.error(f"DB error: {exc}")

        if orders:
            try:
                import pandas as pd
                df = pd.DataFrame([{
                    "Order Ref": r["order_reference"],
                    "Weight (kg)": r["parcel_weight_kg"],
                    "Dimensions (cm)": f"{r['length_cm']}×{r['width_cm']}×{r['height_cm']}",
                    "Package": r["package_type"],
                    "COD": "Yes" if r["is_cod"] else "No",
                    "COD Amt (INR)": r["cod_amount"],
                    "Created": r["created_at"][:19],
                } for r in orders])
                st.dataframe(df, use_container_width=True, hide_index=True)
            except ImportError:
                for r in orders:
                    st.markdown(f"- `{r['order_reference']}` | {r['parcel_weight_kg']} kg | {r['created_at'][:19]}")
        else:
            st.info("No orders found. Submit an order in Tab 1.")

    # Quotes Tab
    with tab_quotes:
        st.subheader("Stored Quotes")
        q_ref_filter = st.text_input("Filter by Order Reference", key="hist_quote_filter")
        try:
            if q_ref_filter:
                quotes = db.get_quotes_for_order(q_ref_filter.strip())
            else:
                with db.connection() as conn:
                    rows = conn.execute("SELECT * FROM quotes ORDER BY created_at DESC LIMIT 100").fetchall()
                    quotes = [dict(r) for r in rows]
        except Exception as exc:
            quotes = []
            st.error(f"DB error: {exc}")

        if quotes:
            try:
                import pandas as pd
                df = pd.DataFrame([{
                    "Quote ID": r["quote_id"],
                    "Order Ref": r["order_reference"],
                    "Courier": r["courier_name"],
                    "Service": r["service_type"],
                    "Total Cost (INR)": r["total_cost"],
                    "Est. Days": r["estimated_delivery_days"],
                    "Source": r["source_type"],
                    "Retrieved": r["created_at"][:19],
                } for r in quotes])
                st.dataframe(df, use_container_width=True, hide_index=True)
            except ImportError:
                for r in quotes:
                    st.markdown(f"- `{r['quote_id']}` | {r['courier_name']} | INR {r['total_cost']:.2f}")

            # Expandable raw quote data
            with st.expander("🔍 View Raw Quote Data"):
                ref = st.selectbox("Select Quote ID", [r["quote_id"] for r in quotes], key="raw_quote_sel")
                raw = next((r["quote_data_json"] for r in quotes if r["quote_id"] == ref), None)
                if raw:
                    try:
                        st.json(json.loads(raw))
                    except Exception:
                        st.code(raw)
        else:
            st.info("No quotes found. Fetch rates in Tab 1.")

    # Shipments Tab
    with tab_shipments:
        st.subheader("Shipment Records")
        try:
            with db.connection() as conn:
                rows = conn.execute("SELECT * FROM shipments ORDER BY created_at DESC LIMIT 50").fetchall()
                shipments = [dict(r) for r in rows]
        except Exception as exc:
            shipments = []
            st.error(f"DB error: {exc}")

        if shipments:
            for ship in shipments:
                status = ShipmentStatus(ship["status"])
                colour = shipment_status_colour(status)
                with st.expander(f":{colour}[{status.value}] — {ship['order_reference']}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown(f"**Shipment ID:** `{ship['shipment_id']}`")
                        st.markdown(f"**Order Reference:** `{ship['order_reference']}`")
                        st.markdown(f"**Courier:** `{ship['courier_code']}`")
                        st.markdown(f"**Status:** :{colour}[**{status.value}**]")
                    with col2:
                        tracking = ship.get("tracking_number")
                        st.markdown(f"**Tracking Number:** `{tracking}`" if tracking else "**Tracking:** Not yet assigned")
                        st.markdown(f"**Approved By:** {ship.get('approved_by') or '—'}")
                        st.markdown(f"**Created:** {ship.get('created_at', '')[:19]}")
                        st.markdown(f"**Updated:** {ship.get('updated_at', '')[:19]}")

                    if status == ShipmentStatus.CREATION_UNCERTAIN:
                        st.warning(
                            "⛔ This shipment is in an uncertain state. "
                            "Use the Approval & Dispatch tab to reconcile before retrying."
                        )
                    if status == ShipmentStatus.CREATED:
                        st.caption("_Tracking number is a DEMO simulation reference only._")
        else:
            st.info("No shipments dispatched yet. Use Tab 4 after approval.")

    # Audit Trail Tab
    with tab_audit:
        st.subheader("Audit Trail (Secret-Scrubbed)")
        st.caption(
            "All events are recorded with sensitive fields redacted. "
            "This log is append-only and reflects all domain decisions."
        )
        audit_ref = st.text_input("Filter by Order Reference (blank = last 100 events)", key="audit_ref_filter")
        try:
            events = audit.get_trail(order_reference=audit_ref.strip() or None)
        except Exception as exc:
            events = []
            st.error(f"DB error: {exc}")

        if events:
            for event in events:
                ev = format_audit_event(event)
                with st.expander(f"[{ev['timestamp']}] {ev['event']} — {ev['actor']} | Order: {ev['order_ref']}"):
                    st.code(ev["details_preview"], language="json")
        else:
            st.info("No audit events found.")


# ── Main App ──────────────────────────────────────────────────────────────────
def main():
    render_sidebar()

    st.title("📦 Adaptive Shipping Intelligence Agent")
    st.caption(
        "A beginner-friendly shipping rate comparer and label creator. "
        "Phases 1–6 complete. All domain logic is preserved from Phase 5."
    )
    st.caption(
        "🔵 **DEMO DATA NOTICE:** All courier rates, tracking references, and simulated dispatches "
        "are produced by the Demo Shipping Adapter and are NOT live carrier prices or confirmed bookings."
    )

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📝 Order Entry & Rates",
        "📦 Packaging Optimizer",
        "🧪 Stress Testing Lab",
        "✅ Approval & Dispatch",
        "📋 History & Audit",
    ])

    with tab1:
        render_order_entry()
    with tab2:
        render_packaging_optimizer()
    with tab3:
        render_stress_testing()
    with tab4:
        render_approval_dispatch()
    with tab5:
        render_history()


if __name__ == "__main__":
    main()
