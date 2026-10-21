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
    CustomPackaging,
    check_courier_package_eligibility,
    evaluate_orthogonal_fit,
    evaluate_packaging_alternatives,
)
from src.domain.dimension_engine import (
    validate_parcel_dimensions,
    validate_parcel_weight,
    compute_girth,
    compute_length_plus_girth,
    evaluate_courier_dimension_eligibility,
    OFFICIAL_DOCUMENTED_CAPABILITIES,
    CourierCapabilityRule,
    EligibilityStatus,
)
from src.domain.verification import ShipmentVerificationService, VerificationStatus
from src.domain.workflow import AgentDecisionEngine, AgentDecisionTrace, DecisionTraceStatus
from src.providers.base import ShipmentResult, PickupResult
from src.providers.demo_adapter import ALL_DEMO_COURIERS, DemoShippingAdapter
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
        "approved_record": None,
        "approved_proposal_sig": None,
        "saved_order_id": None,
        "form_errors": [],
        "decision_trace": None,
        "active_demo_scenario": None,
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
        st.divider()
        st.markdown("**🎯 Evaluator Demo Scenarios**")
        demo_choice = st.selectbox(
            "Select Judge Demo Scenario",
            [
                "None (Manual Input)",
                "Demo A: Normal Parcel (Standard comparison & booking)",
                "Demo B: Oversized Elongated Item (110cm custom box)",
                "Demo C: Cheapest Option Rejected / Unserviceable",
                "Demo D: Ambiguous Booking & Reconciliation",
            ],
            key="demo_scenario_choice",
        )

        if st.button("🚀 Load Evaluator Scenario", use_container_width=True):
            if "Demo A" in demo_choice:
                order_demo = OrderInput(
                    order_reference=f"DEMO-A-{uuid.uuid4().hex[:6].upper()}",
                    sender=Address(name="Alpha Hub", phone="9876543210", street_line1="1 Main St", city="Delhi", state="Delhi", pin_code="110020"),
                    recipient=Address(name="Vikram Patel", phone="9123456780", street_line1="402 Green Acres", city="Bengaluru", state="Karnataka", pin_code="560034"),
                    parcel_weight_kg=1.8,
                    dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
                    package_type=PackageType.BOX,
                    is_cod=True,
                    cod_amount=1200.0,
                )
            elif "Demo B" in demo_choice:
                order_demo = OrderInput(
                    order_reference=f"DEMO-B-{uuid.uuid4().hex[:6].upper()}",
                    sender=Address(name="Precision Engineering", phone="9876543210", street_line1="12 Industrial Area", city="Pune", state="Maharashtra", pin_code="411001"),
                    recipient=Address(name="Design Studio", phone="9123456780", street_line1="5 High Tech Rd", city="Mumbai", state="Maharashtra", pin_code="400001"),
                    parcel_weight_kg=3.2,
                    dimensions=Dimensions(length_cm=110.0, width_cm=20.0, height_cm=15.0),
                    package_type=PackageType.BOX,
                    is_cod=False,
                )
            elif "Demo C" in demo_choice:
                order_demo = OrderInput(
                    order_reference=f"DEMO-C-{uuid.uuid4().hex[:6].upper()}",
                    sender=Address(name="North Central Depot", phone="9876543210", street_line1="8 Station Rd", city="Delhi", state="Delhi", pin_code="110020"),
                    recipient=Address(name="Remote Border Outpost", phone="9123456780", street_line1="Sector 4", city="Itanagar", state="Arunachal Pradesh", pin_code="791111"),
                    parcel_weight_kg=2.0,
                    dimensions=Dimensions(length_cm=30.0, width_cm=20.0, height_cm=10.0),
                    package_type=PackageType.BOX,
                    is_cod=False,
                )
            elif "Demo D" in demo_choice:
                order_demo = OrderInput(
                    order_reference=f"DEMO-D-{uuid.uuid4().hex[:6].upper()}",
                    sender=Address(name="Metro Logistics", phone="9876543210", street_line1="Plot 99 Okhla", city="Delhi", state="Delhi", pin_code="110020"),
                    recipient=Address(name="Kavita Rao", phone="9123456780", street_line1="MG Road 14", city="Bengaluru", state="Karnataka", pin_code="560001"),
                    parcel_weight_kg=1.5,
                    dimensions=Dimensions(length_cm=20.0, width_cm=15.0, height_cm=10.0),
                    package_type=PackageType.BOX,
                    is_cod=False,
                )
            else:
                order_demo = None

            if order_demo:
                provider = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
                quotes_demo = provider.get_rates(order_demo)
                engine = AgentDecisionEngine()
                trace_demo = engine.run_observe(order_demo)
                db_svc = get_db()
                custom_pkgs_saved = [
                    CustomPackaging(
                        packaging_id=r["packaging_id"],
                        name=r["name"],
                        package_type=PackageType(r["package_type"]),
                        outer_dimensions=Dimensions(length_cm=r["outer_length_cm"], width_cm=r["outer_width_cm"], height_cm=r["outer_height_cm"]),
                        wall_thickness_cm=r["wall_thickness_cm"],
                        padding_cm=r["padding_cm"],
                        tare_weight_kg=r["tare_weight_kg"],
                    )
                    for r in db_svc.get_custom_packagings()
                ]
                engine.run_evaluate(order_demo, quotes_demo, trace_demo, custom_packages=custom_pkgs_saved, couriers=ALL_DEMO_COURIERS)
                rank_demo = engine.run_decide(quotes_demo, RankingPreferences(mode=RankingMode.LOWEST_COST), trace_demo)

                st.session_state.update({
                    "order": order_demo,
                    "quotes": quotes_demo,
                    "ranking_result": rank_demo,
                    "selected_quote": rank_demo.selected_winner.quote if rank_demo.selected_winner else None,
                    "decision_trace": trace_demo,
                    "active_demo_scenario": demo_choice,
                    "dispatch_result": None,
                    "recon_result": None,
                    "approval_given": False,
                })
                st.rerun()

        st.divider()
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
        st.subheader("📦 Universal Parcel & Packaging Sizing Engine")
        st.caption("Supports arbitrary valid dimensions. Evaluates packed dimensions against documented courier restrictions.")

        sizing_mode = st.radio(
            "Packaging Method",
            ["Direct Parcel Outer Dimensions", "Pack into Standard / Custom Packaging"],
            horizontal=True,
            key="sizing_mode_radio",
            help="Choose whether entered dimensions are the final packed box or an unpacked product to fit into packaging.",
        )

        db_svc = get_db()
        saved_custom_pkgs = db_svc.get_custom_packagings()

        if sizing_mode == "Pack into Standard / Custom Packaging":
            st.markdown("##### 1. Product Dimensions (Unpacked Item)")
            prod_c1, prod_c2, prod_c3, prod_c4 = st.columns(4)
            with prod_c1:
                item_wt = st.number_input("Product Net Weight (kg) *", min_value=0.01, max_value=1000.0, value=1.8, step=0.1, key="item_wt_in")
            with prod_c2:
                item_l = st.number_input("Product Length (cm) *", min_value=0.1, max_value=500.0, value=30.0, step=0.5, key="item_l_in")
            with prod_c3:
                item_w = st.number_input("Product Width (cm) *", min_value=0.1, max_value=500.0, value=20.0, step=0.5, key="item_w_in")
            with prod_c4:
                item_h = st.number_input("Product Height (cm) *", min_value=0.1, max_value=500.0, value=15.0, step=0.5, key="item_h_in")

            st.markdown("##### 2. Choose Packaging Candidate")
            pkg_choices = ["Demo Small Polybag Flyer (15×12×3 cm, tare 0.02kg)",
                           "Demo Small Corrugated Box (20×15×10 cm, tare 0.08kg)",
                           "Demo Medium Box (25×20×15 cm, tare 0.12kg)",
                           "Demo Large Box (35×25×20 cm, tare 0.20kg)",
                           "Demo Extra-Large Box (45×35×30 cm, tare 0.35kg)"]
            custom_map = {}
            for cp in saved_custom_pkgs:
                lbl = f"Custom: {cp['name']} ({cp['outer_length_cm']}×{cp['outer_width_cm']}×{cp['outer_height_cm']} cm, tare {cp['tare_weight_kg']}kg)"
                pkg_choices.append(lbl)
                custom_map[lbl] = cp

            chosen_pkg_lbl = st.selectbox("Select Packaging *", pkg_choices, index=2, key="chosen_pkg_box")

            # Parse dimensions of chosen package
            if chosen_pkg_lbl in custom_map:
                cp = custom_map[chosen_pkg_lbl]
                p_outer_l = cp["outer_length_cm"]
                p_outer_w = cp["outer_width_cm"]
                p_outer_h = cp["outer_height_cm"]
                p_tare = cp["tare_weight_kg"]
                p_wall = cp["wall_thickness_cm"]
                p_pad = cp["padding_cm"]
                p_type_val = cp["package_type"]
            else:
                cat_idx = pkg_choices.index(chosen_pkg_lbl)
                opt = DEMO_PACKAGING_CATALOGUE[cat_idx]
                p_outer_l = opt.outer_dimensions.length_cm
                p_outer_w = opt.outer_dimensions.width_cm
                p_outer_h = opt.outer_dimensions.height_cm
                p_tare = opt.tare_weight_kg
                p_wall = 0.5
                p_pad = 0.5
                p_type_val = opt.package_type.value

            # Evaluate 6-orientation fit
            fit_res = evaluate_orthogonal_fit(
                product_dims=Dimensions(length_cm=item_l, width_cm=item_w, height_cm=item_h),
                outer_dims=Dimensions(length_cm=p_outer_l, width_cm=p_outer_w, height_cm=p_outer_h),
                wall_thickness_cm=p_wall,
                padding_cm=p_pad,
            )

            if fit_res.is_feasible:
                st.success(
                    f"✅ **Fit Confirmed:** Item fits in orientation "
                    f"`{fit_res.successful_orientation[0]}×{fit_res.successful_orientation[1]}×{fit_res.successful_orientation[2]} cm` "
                    f"(Internal Cavity: `{fit_res.internal_package_dims[0]}×{fit_res.internal_package_dims[1]}×{fit_res.internal_package_dims[2]} cm`)."
                )
            else:
                st.warning(
                    f"⚠️ **Fit Warning:** Item does not fit inside this packaging across any of the 6 orthogonal rotations! "
                    f"{fit_res.rejection_reason} Please select a larger box or create custom packaging in Tab 2."
                )

            length = p_outer_l
            width = p_outer_w
            height = p_outer_h
            weight = round(item_wt + p_tare, 3)
            pkg_type = p_type_val
            st.info(f"📦 **Final Packed Parcel Sent to Couriers:** {length:.1f} × {width:.1f} × {height:.1f} cm | Packed Gross Weight: {weight:.2f} kg ({item_wt:.2f}kg item + {p_tare:.2f}kg tare)")
        else:
            pcol1, pcol2, pcol3, pcol4 = st.columns(4)
            with pcol1:
                weight = st.number_input("Actual Gross Weight (kg) *", min_value=0.01, max_value=1000.0, value=1.8, step=0.1, key="weight_in")
            with pcol2:
                length = st.number_input("Length (cm) *", min_value=0.1, max_value=500.0, value=35.0, step=0.5, key="length_in")
            with pcol3:
                width = st.number_input("Width (cm) *", min_value=0.1, max_value=500.0, value=25.0, step=0.5, key="width_in")
            with pcol4:
                height = st.number_input("Height (cm) *", min_value=0.1, max_value=500.0, value=20.0, step=0.5, key="height_in")

            pcol_type, _ = st.columns([2, 2])
            with pcol_type:
                pkg_type = st.selectbox(
                    "Package Type *",
                    options=[p.value for p in PackageType],
                    format_func=lambda x: x.replace("_", " ").title(),
                )

        pcol6, pcol7 = st.columns(2)
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
        st.session_state["approved_record"] = None
        st.session_state["approved_proposal_sig"] = None
        st.session_state["dispatch_result"] = None
        st.session_state["verification_report"] = None
        st.session_state["pickup_result"] = None
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

        # Run ranking & agentic decision trace
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

        # Generate Agent Decision Trace
        engine = AgentDecisionEngine()
        trace = engine.run_observe(order)
        custom_pkgs_saved = [
            CustomPackaging(
                packaging_id=r["packaging_id"],
                name=r["name"],
                package_type=PackageType(r["package_type"]),
                outer_dimensions=Dimensions(length_cm=r["outer_length_cm"], width_cm=r["outer_width_cm"], height_cm=r["outer_height_cm"]),
                wall_thickness_cm=r["wall_thickness_cm"],
                padding_cm=r["padding_cm"],
                tare_weight_kg=r["tare_weight_kg"],
            )
            for r in db.get_custom_packagings()
        ]
        engine.run_evaluate(order, quotes, trace, custom_packages=custom_pkgs_saved, couriers=ALL_DEMO_COURIERS)
        engine.run_decide(quotes, prefs, trace)
        st.session_state["decision_trace"] = trace

        st.success(f"✅ Rates fetched for **{order.order_reference}**. Scroll down to review.")

    # ── Display quotes if available ──────────────────────────────────────────
    if st.session_state.get("quotes") and st.session_state.get("order"):
        _render_rate_comparison()


def _render_rate_comparison():
    order: Optional[OrderInput] = st.session_state.get("order")
    quotes = st.session_state.get("quotes") or []
    ranking_result = st.session_state.get("ranking_result")

    if not order or not quotes:
        return

    st.divider()
    st.subheader("📊 Courier Rate Comparison")
    st.caption(quote_source_badge(quotes[0]) if quotes else "")
    girth_val = compute_girth(order.dimensions)
    l_plus_g = compute_length_plus_girth(order.dimensions)
    st.info(
        f"📐 **Evaluated Packed Parcel:** {order.dimensions.length_cm:.1f} × {order.dimensions.width_cm:.1f} × {order.dimensions.height_cm:.1f} cm | "
        f"Gross Dead Weight: {order.parcel_weight_kg:.2f} kg | "
        f"Volume: {order.dimensions.volume_cm3:,.0f} cm³ | "
        f"Girth: {girth_val:.1f} cm | "
        f"Length + Girth: {l_plus_g:.1f} cm",
        icon="📦"
    )

    winner_quote_id: Optional[str] = None
    if (
        ranking_result
        and getattr(ranking_result, "selected_winner", None)
        and getattr(ranking_result.selected_winner, "quote", None)
    ):
        winner_quote_id = ranking_result.selected_winner.quote.quote_id

    if ranking_result and getattr(ranking_result, "selected_winner", None):
        with st.container(border=True):
            st.markdown("### 🏆 Recommended Courier")
            st.markdown(describe_ranking_result(ranking_result))

    # ── Agent Decision Trace Expander ─────────────────────────────────────────
    trace: Optional[AgentDecisionTrace] = st.session_state.get("decision_trace")
    if trace:
        st.divider()
        with st.expander("🧠 Agent Decision Trace (Explainable Multi-Stage Log)", expanded=True):
            st.markdown(
                f"**Order:** `{trace.order_reference}` &nbsp;|&nbsp; "
                f"**Lifecycle State:** `{trace.completed_stage}` &nbsp;|&nbsp; "
                f"**Human Intervention Required:** {'⚠️ Yes' if trace.requires_human_intervention else '✅ No'}"
            )
            if trace.intervention_reason:
                st.warning(f"**Intervention Notice:** {trace.intervention_reason}")

            for s in trace.steps:
                status_icon = "✅" if s.status.value in ["VERIFIED", "ELIGIBLE"] else ("⚠️" if s.status.value in ["UNVERIFIED", "PENDING"] else "❌")
                col_s1, col_s2 = st.columns([4, 1])
                with col_s1:
                    st.markdown(f"**{status_icon} Step {s.step_number}: {s.title}** &nbsp;·&nbsp; `[{s.status.value}]`")
                    st.caption(f"_{s.summary}_")
                    if s.explanation:
                        st.markdown(f"💡 **Rationale:** {s.explanation}")
                with col_s2:
                    st.caption(f"⏱ {s.timestamp[11:19]} UTC")
                    if s.is_demo:
                        st.markdown('<span style="font-size:0.75rem;background:#EFF6FF;color:#1D4ED8;padding:0.15rem 0.4rem;border-radius:4px;border:1px solid #BFDBFE;">🔵 DEMO</span>', unsafe_allow_html=True)

                if s.evidence:
                    with st.expander(f"🔍 Evidence for Step {s.step_number}", expanded=False):
                        st.json(s.evidence)
                st.markdown("<hr style='margin:0.4rem 0;border-top:1px dashed #E2E8F0;'>", unsafe_allow_html=True)

    # Eligible couriers
    eligible = [q for q in quotes if q.is_eligible]
    ineligible = [q for q in quotes if not q.is_eligible]

    if eligible:
        st.markdown("#### ✅ Eligible Couriers (Ranked)")
        for idx, quote in enumerate(sorted(eligible, key=lambda q: q.cost_breakdown.total_cost if q.cost_breakdown else 1e9)):
            is_winner = bool(winner_quote_id and quote.quote_id == winner_quote_id)
            label = f"🥇 #{idx + 1} — {quote.courier_name}" if is_winner else f"#{idx + 1} — {quote.courier_name}"
            with st.expander(label + (" 🏆 **RECOMMENDED**" if is_winner else ""), expanded=is_winner):
                _render_quote_card(quote)

    if ineligible:
        st.markdown("#### ❌ Ineligible Couriers (Excluded from Ranking)")
        for quote in ineligible:
            with st.expander(f"❌ {quote.courier_name} — {quote.unserviceable_reason or 'Ineligible'}", expanded=False):
                st.error(f"**Reason:** {quote.unserviceable_reason or 'Unserviceable or missing data'}")
                st.caption(f"Quote ID: `{quote.quote_id}`")
                st.caption(quote_source_badge(quote))
                if quote.missing_evidence:
                    st.caption("Missing evidence: " + "; ".join(quote.missing_evidence))

    # Quote selection for approval tab
    st.divider()
    eligible_priced = [q for q in eligible if q.cost_breakdown]
    eligible_names = [f"{q.courier_name} — {fmt_inr(q.cost_breakdown.total_cost)}" for q in eligible_priced]
    if eligible_names and eligible_priced:
        st.markdown("#### 🎯 Select Courier for Approval & Dispatch")
        default_idx = 0
        if winner_quote_id:
            for i, q in enumerate(eligible_priced):
                if q.quote_id == winner_quote_id:
                    default_idx = i
                    break
        selected_label = st.selectbox(
            "Choose courier to approve & dispatch",
            options=eligible_names,
            index=default_idx,
            key="selected_courier_label",
        )
        if selected_label and selected_label in eligible_names:
            idx = eligible_names.index(selected_label)
            new_quote = eligible_priced[idx]
            old_quote = st.session_state.get("selected_quote")
            if old_quote and old_quote.quote_id != new_quote.quote_id:
                st.session_state.pop("approved_record", None)
                st.session_state.pop("approved_proposal_sig", None)
                st.session_state.pop("dispatch_result", None)
                st.session_state.pop("verification_report", None)
                st.session_state.pop("pickup_result", None)
                st.session_state["approval_given"] = False
            st.session_state["selected_quote"] = new_quote
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

        # Explicit eligibility status badge
        el_status = getattr(quote, "eligibility_status", "ELIGIBLE")
        if el_status == "ELIGIBLE":
            st.markdown("🔒 **Capability Status:** :green[🟢 ELIGIBLE (Verified Documentation)]")
        elif el_status == "UNVERIFIED":
            st.markdown("🔒 **Capability Status:** :orange[🟡 UNVERIFIED (Size Limits Unconfirmed)]")
        elif el_status == "INELIGIBLE":
            st.markdown("🔒 **Capability Status:** :red[🔴 INELIGIBLE (Restriction Violated)]")
        elif el_status == "STALE":
            st.markdown("🔒 **Capability Status:** :orange[🟠 STALE (Requires Refresh)]")

        ev_src = getattr(quote, "evidence_source", None)
        ev_url = getattr(quote, "evidence_url", None)
        if ev_src:
            if ev_url:
                st.markdown(f"📜 **Documented Source:** [{ev_src}]({ev_url})")
            else:
                st.markdown(f"📜 **Documented Source:** {ev_src}")

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
            st.markdown("**Weight & Volumetric Calculation:**")
            wb = format_weight_breakdown(quote.weight_result)
            for label, val in wb.items():
                st.markdown(f"- {label}: {val}")
            st.caption(f"📐 **Courier Volumetric Divisor:** {quote.weight_result.divisor_used:.0f} cm³/kg")

    if quote.missing_evidence:
        st.caption("⚠️ Notes: " + "; ".join(quote.missing_evidence))
    if quote.decision_explanation:
        st.caption(f"_Explanation: {quote.decision_explanation}_")
    st.caption(f"_{quote.disclaimer}_")


# ── Tab 2: Packaging Optimizer ────────────────────────────────────────────────
def render_packaging_optimizer():
    st.header("📦 Packaging Optimizer & Custom Box Sizing")
    st.warning(
        "**DEMO & CUSTOM PACKAGING** — Box sizes below are configurable examples and user-defined dimensions for simulation.",
        icon="📦",
    )

    order: Optional[OrderInput] = st.session_state.get("order")

    if order is None:
        st.info("ℹ️ Please complete Order Entry first to load parcel details.")
        return

    st.success(f"Loaded parcel from **{order.order_reference}**: "
               f"{order.dimensions.length_cm} × {order.dimensions.width_cm} × {order.dimensions.height_cm} cm, "
               f"{order.parcel_weight_kg} kg ({order.package_type.value})")

    db, audit, _, _, _ = get_services()

    # ── Expandable Section: Add Custom-Size Packaging ─────────────────────────
    with st.expander("➕ Add Custom-Size Packaging", expanded=False):
        st.markdown("Create custom packaging dimensions for elongated or oversized products (e.g. >= 100 cm).")
        with st.form("add_custom_pkg_form"):
            c1, c2 = st.columns([2, 1])
            with c1:
                cp_name = st.text_input("Packaging Name *", value="Custom Long Box")
            with c2:
                cp_type = st.selectbox("Packaging Type *", ["Box", "Polybag", "Tube", "Custom Crate"], index=0)

            d1, d2, d3 = st.columns(3)
            with d1:
                cp_l = st.number_input("Outer Length (cm) *", min_value=1.0, max_value=500.0, value=110.0, step=1.0)
            with d2:
                cp_w = st.number_input("Outer Width (cm) *", min_value=1.0, max_value=500.0, value=20.0, step=1.0)
            with d3:
                cp_h = st.number_input("Outer Height (cm) *", min_value=1.0, max_value=500.0, value=15.0, step=1.0)

            t1, t2, t3 = st.columns(3)
            with t1:
                cp_wall = st.number_input("Wall Thickness per side (cm) *", min_value=0.0, max_value=20.0, value=0.5, step=0.1)
            with t2:
                cp_pad = st.number_input("Padding per side (cm) *", min_value=0.0, max_value=20.0, value=2.0, step=0.5)
            with t3:
                cp_tare = st.number_input("Packaging Tare Weight (kg) *", min_value=0.0, max_value=100.0, value=0.5, step=0.1)

            int_preview_l = max(0.0, cp_l - 2 * cp_wall)
            int_preview_w = max(0.0, cp_w - 2 * cp_wall)
            int_preview_h = max(0.0, cp_h - 2 * cp_wall)
            st.caption(f"📐 **Internal Cavity:** {int_preview_l:.1f} × {int_preview_w:.1f} × {int_preview_h:.1f} cm | Outer Vol: {(cp_l*cp_w*cp_h):,.0f} cm³")

            save_pkg_btn = st.form_submit_button("💾 Save Custom Packaging", type="primary", use_container_width=True)

        if save_pkg_btn:
            type_map = {"Box": "box", "Polybag": "polybag", "Tube": "tube", "Custom Crate": "custom_crate"}
            try:
                pkg_id = db.insert_custom_packaging({
                    "name": cp_name.strip(),
                    "package_type": type_map.get(cp_type, "box"),
                    "outer_length_cm": cp_l,
                    "outer_width_cm": cp_w,
                    "outer_height_cm": cp_h,
                    "wall_thickness_cm": cp_wall,
                    "padding_cm": cp_pad,
                    "tare_weight_kg": cp_tare,
                })
                st.success(f"✅ Saved custom packaging **{cp_name.strip()}** (`{pkg_id}`)!")
                st.rerun()
            except Exception as exc:
                st.error(f"❌ Error saving packaging: {exc}")

    # Load custom packages from DB
    saved_pkgs = db.get_custom_packagings()
    custom_packages_list: list[CustomPackaging] = []
    for r in saved_pkgs:
        try:
            custom_packages_list.append(
                CustomPackaging(
                    packaging_id=r["packaging_id"],
                    name=r["name"],
                    package_type=PackageType(r["package_type"]),
                    outer_dimensions=Dimensions(length_cm=r["outer_length_cm"], width_cm=r["outer_width_cm"], height_cm=r["outer_height_cm"]),
                    wall_thickness_cm=r["wall_thickness_cm"],
                    padding_cm=r["padding_cm"],
                    tare_weight_kg=r["tare_weight_kg"],
                )
            )
        except Exception:
            pass

    if custom_packages_list:
        with st.expander(f"📦 Saved Custom Packagings ({len(custom_packages_list)})", expanded=False):
            for c_pkg in custom_packages_list:
                col_c1, col_c2 = st.columns([3, 1])
                with col_c1:
                    st.markdown(
                        f"**{c_pkg.name}** ({c_pkg.package_type.value.title()}) — "
                        f"Outer: {c_pkg.outer_dimensions.length_cm}×{c_pkg.outer_dimensions.width_cm}×{c_pkg.outer_dimensions.height_cm} cm | "
                        f"Inner: {c_pkg.internal_dimensions.length_cm}×{c_pkg.internal_dimensions.width_cm}×{c_pkg.internal_dimensions.height_cm} cm | "
                        f"Tare: {c_pkg.tare_weight_kg} kg"
                    )
                with col_c2:
                    conf = st.checkbox("Confirm", key=f"conf_del_{c_pkg.packaging_id}")
                    if st.button("🗑️ Delete", key=f"del_{c_pkg.packaging_id}", disabled=not conf):
                        db.delete_custom_packaging(c_pkg.packaging_id)
                        st.success(f"Deleted {c_pkg.name}")
                        st.rerun()

    st.subheader("Product Net Dimensions (Unpacked Item)")
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        prod_l = st.number_input("Product Length (cm) *", min_value=0.1, max_value=500.0, value=float(order.dimensions.length_cm), step=0.5)
    with pc2:
        prod_w = st.number_input("Product Width (cm) *", min_value=0.1, max_value=500.0, value=float(order.dimensions.width_cm), step=0.5)
    with pc3:
        prod_h = st.number_input("Product Height (cm) *", min_value=0.1, max_value=500.0, value=float(order.dimensions.height_cm), step=0.5)
    with pc4:
        prod_weight = st.number_input("Product Net Weight (kg) *", min_value=0.01, max_value=1000.0, value=float(order.parcel_weight_kg), step=0.1)

    freight_estimate = st.number_input(
        "Per-kg Freight Rate Estimate (INR, for savings calculation)",
        min_value=1.0, value=50.0, step=5.0,
        help="Used only for comparative savings estimate — not a live rate.",
    )

    if st.button("📐 Evaluate Packaging Alternatives (Demo & Custom)", type="primary"):
        product_dims = Dimensions(length_cm=prod_l, width_cm=prod_w, height_cm=prod_h)
        with st.spinner("Evaluating packaging options across 6 orthogonal rotations…"):
            results = evaluate_packaging_alternatives(
                product_dimensions=product_dims,
                product_net_weight_kg=prod_weight,
                current_outer_dimensions=order.dimensions,
                volumetric_rule=None,
                per_kg_freight_estimate_inr=freight_estimate,
                custom_packages=custom_packages_list,
                couriers=ALL_DEMO_COURIERS,
            )
            st.session_state["packaging_results"] = results
            st.session_state["eval_prod_weight"] = prod_weight

        try:
            audit.record_event(
                AuditEventType.PACKAGING_OPTIMIZED,
                "PackagingOptimizer",
                {"product_dims": f"{prod_l}x{prod_w}x{prod_h}", "alternatives_evaluated": len(results)},
                order_reference=order.order_reference,
            )
        except Exception:
            pass

    results = st.session_state.get("packaging_results")
    if results:
        feasible = [r for r in results if r.is_feasible]
        infeasible = [r for r in results if not r.is_feasible]

        st.divider()
        st.subheader("Results")
        if feasible:
            best = min(feasible, key=lambda r: (r.new_billable_slab_kg, -r.estimated_freight_savings_inr))
            st.success(f"**Best option:** {best.box_name} ({'Custom' if best.is_custom else 'Demo'}) — saves {fmt_inr(best.estimated_freight_savings_inr)} in estimated freight.")

            # Apply box back to order
            col_ap1, col_ap2 = st.columns([3, 1])
            with col_ap1:
                st.caption(f"Apply **{best.box_name}** to update active order dimensions ({best.outer_dimensions.length_cm}×{best.outer_dimensions.width_cm}×{best.outer_dimensions.height_cm} cm) and recalculate rates:")
            with col_ap2:
                if st.button("📦 Apply Box to Order", use_container_width=True):
                    order.dimensions = best.outer_dimensions
                    order.package_type = best.package_type
                    eval_p_wt = st.session_state.get("eval_prod_weight", order.parcel_weight_kg)
                    order.parcel_weight_kg = round(eval_p_wt + best.tare_weight_kg, 3)
                    st.session_state["order"] = order

                    # Refresh quotes
                    provider = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
                    quotes = provider.get_rates(order)
                    st.session_state["quotes"] = quotes
                    prefs = st.session_state.get("ranking_result").preferences if st.session_state.get("ranking_result") else RankingPreferences()
                    ranking_result = rank_couriers(quotes, prefs)
                    st.session_state["ranking_result"] = ranking_result
                    st.session_state["selected_quote"] = ranking_result.selected_winner.quote if ranking_result.selected_winner else None
                    st.success(f"✅ Applied **{best.box_name}**! Rates refreshed in Tab 1.")
                    st.rerun()

            st.markdown("#### ✅ Feasible Packaging Options")
            import pandas as pd
            feas_rows = []
            for r in sorted(feasible, key=lambda x: (not x.is_custom, -x.estimated_freight_savings_inr)):
                feas_rows.append({
                    "Status": "✅ Fits",
                    "Package Name": r.box_name,
                    "Source": "Custom" if r.is_custom else "Demo",
                    "Type": r.package_type.value.title(),
                    "Outer Dims (cm)": f"{r.outer_dimensions.length_cm}×{r.outer_dimensions.width_cm}×{r.outer_dimensions.height_cm}" if r.outer_dimensions else "-",
                    "Internal Dims (cm)": f"{r.internal_dimensions.length_cm}×{r.internal_dimensions.width_cm}×{r.internal_dimensions.height_cm}" if r.internal_dimensions else "-",
                    "Fit Orientation": f"{r.successful_orientation[0]}×{r.successful_orientation[1]}×{r.successful_orientation[2]}" if r.successful_orientation else "Standard",
                    "Vol Reduction": f"{r.volume_reduction_pct:.1f}%",
                    "Dead Space Saved": f"{r.volume_difference_cm3:,.0f} cm³",
                    "Billable Slab": f"{fmt_kg(r.original_billable_slab_kg)} ➔ {fmt_kg(r.new_billable_slab_kg)}",
                    "Est. Savings": fmt_inr(r.estimated_freight_savings_inr),
                    "Courier Eligibility": r.courier_status_summary,
                })
            st.dataframe(pd.DataFrame(feas_rows), use_container_width=True, hide_index=True)

            with st.expander("🔍 Detailed Courier-by-Courier Capability & Divisor Audit", expanded=False):
                st.markdown("Audited against published carrier constraints (Air: 5000, Surface: 4500, Cargo: 4000 cm³/kg).")
                for r in sorted(feasible, key=lambda x: (not x.is_custom, -x.estimated_freight_savings_inr)):
                    st.markdown(f"**📦 {r.box_name}** ({r.outer_dimensions.length_cm}×{r.outer_dimensions.width_cm}×{r.outer_dimensions.height_cm} cm):")
                    if hasattr(r, "courier_reports") and r.courier_reports:
                        for c_code, rep in r.courier_reports.items():
                            status_badge = "🟢 ELIGIBLE" if rep.status.value == "ELIGIBLE" else ("🔴 INELIGIBLE" if rep.status.value == "INELIGIBLE" else "🟡 UNVERIFIED")
                            doc_link = f"[{rep.evidence_source}]({rep.evidence_url})" if rep.evidence_url else rep.evidence_source
                            if rep.status.value == "ELIGIBLE":
                                st.markdown(f"- **{rep.courier_name}**: {status_badge} — Vol. Wt: `{rep.volumetric_weight_kg:.2f} kg` (Div: `{rep.volumetric_divisor:.0f}`), Billable: `{rep.billing_slab_kg:.1f} kg`. _{doc_link}_")
                            elif rep.status.value == "INELIGIBLE":
                                st.markdown(f"- **{rep.courier_name}**: {status_badge} — _{'; '.join(rep.rejection_reasons)}_. _{doc_link}_")
                            else:
                                st.markdown(f"- **{rep.courier_name}**: {status_badge} — _{'; '.join(rep.unverified_reasons) or 'Size limits unconfirmed'}_. _{doc_link}_")
                    else:
                        st.caption(f"Status summary: {r.courier_status_summary}")
                    st.markdown("---")
        else:
            st.warning("No feasible packaging alternatives found. Use '➕ Add Custom-Size Packaging' above to define a box large enough for this item.")

        if infeasible:
            st.markdown("#### ❌ Infeasible Options")
            for r in infeasible:
                st.caption(f"❌ **{r.box_name}** ({'Custom' if r.is_custom else 'Demo'}): {r.rejection_reason}")

        st.caption("⚠️ All savings are estimates based on the configured per-kg rate. Not actual carrier invoices.")

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
    selected_quote: Optional[CourierQuote] = st.session_state.get("selected_quote")

    if order is None or selected_quote is None:
        st.info("ℹ️ Complete Order Entry and select a courier in Tab 1 before proceeding here.")
        return

    # ── Proposal Drift & Invalidation Check ──────────────────────────────────
    current_cost = float(selected_quote.cost_breakdown.total_cost) if selected_quote.cost_breakdown else 0.0
    current_sig = f"{order.order_reference}:{selected_quote.quote_id}:{current_cost:.2f}"
    approved_sig = st.session_state.get("approved_proposal_sig")

    if approved_sig and approved_sig != current_sig:
        st.session_state.pop("approved_record", None)
        st.session_state.pop("approved_proposal_sig", None)
        st.session_state.pop("dispatch_result", None)
        st.session_state.pop("verification_report", None)
        st.session_state.pop("pickup_result", None)
        st.session_state["approval_given"] = False
        st.warning("⚠️ Selected courier, order parameters, or cost changed since previous approval. A fresh approval is required.")

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

    dispatch_result = st.session_state.get("dispatch_result")

    # ── Block Unsafe Retry on Uncertain (Timeout) ────────────────────────────
    if dispatch_result and dispatch_result.status == ShipmentStatus.CREATION_UNCERTAIN:
        st.error(
            "⛔ **CREATION UNCERTAIN** — A previous dispatch attempt timed out or returned an ambiguous response. "
            "Unsafe retry is blocked to prevent duplicate bookings."
        )
        st.markdown(f"**Explanation:** {dispatch_result.explanation}")
        _render_reconciliation_panel(order, selected_quote)
        return

    # ── Post-Dispatch Operational Hub (Already Created) ──────────────────────
    if dispatch_result and dispatch_result.status == ShipmentStatus.CREATED:
        st.divider()
        st.success(f"🎉 **Shipment Dispatched Successfully (DEMO)**")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Tracking Number (DEMO)", dispatch_result.tracking_number or "N/A")
        m2.metric("Shipment ID", dispatch_result.shipment_id)
        m3.metric("Courier", selected_quote.courier_name)
        m4.metric("Total Landed Cost", fmt_inr(selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0))

        st.caption("_Simulated tracking reference generated by Demo Adapter. No live carrier API was charged._")

        # ── Independent 5-Point Verification Audit ──
        st.subheader("🔍 Independent 5-Point Shipment Verification Audit")
        report = st.session_state.get("verification_report")
        if not report:
            db, audit, _, _, _ = get_services()
            provider = get_provider("demo")
            ver_service = ShipmentVerificationService(audit_logger=audit)
            approval_rec = st.session_state.get("approved_record")
            if not approval_rec:
                approval_rec = ApprovalRecord(
                    approved_by=st.session_state.get("approved_by", "Operator"),
                    quote_id=selected_quote.quote_id,
                    total_cost=selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0.0,
                )
            ship_res = ShipmentResult(
                success=True,
                shipment_id=dispatch_result.shipment_id,
                tracking_number=dispatch_result.tracking_number,
                label_status=dispatch_result.label_status,
                label_url_or_data=dispatch_result.label_data,
                order_reference=order.order_reference,
                courier_code=selected_quote.courier_code,
                is_demo_data=True,
            )
            pickup_res = st.session_state.get("pickup_result")
            report = ver_service.verify_shipment(order, selected_quote, approval_rec, ship_res, provider=provider, pickup_result=pickup_res)
            st.session_state["verification_report"] = report

        if report:
            ver_color = "green" if report.overall_status == VerificationStatus.VERIFIED else ("orange" if report.overall_status == VerificationStatus.PENDING else "red")
            st.markdown(f"**Overall Verification Status:** :{ver_color}[**{report.overall_status.value}**]")

            with st.container(border=True):
                for chk in report.checks:
                    c_icon = "✅" if chk.passed else ("⏳" if chk.status == VerificationStatus.PENDING else "⚠️")
                    c1, c2, c3 = st.columns([1, 4, 3])
                    c1.markdown(f"{c_icon} `{chk.criterion_id}`")
                    c2.markdown(f"**{chk.criterion_name}**: {chk.details}")
                    c3.caption(f"Observed: `{chk.observed_value}`")

        # ── Carrier Pickup Schedule ──
        pickup_res = st.session_state.get("pickup_result")
        if pickup_res:
            st.subheader("🚚 Carrier Pickup Schedule")
            with st.container(border=True):
                p1, p2, p3 = st.columns(3)
                p1.markdown(f"**Pickup Reference:** `{pickup_res.pickup_reference}`")
                p2.markdown(f"**Scheduled Date:** `{pickup_res.scheduled_date}`")
                p3.markdown(f"**Time Window:** `{pickup_res.time_window}`")
                sender_obj = getattr(order, "sender", None) or getattr(order, "sender_address", None)
                pin = getattr(sender_obj, "pin_code", "") if sender_obj else ""
                st.caption(f"Warehouse Address: Main Facility, PIN {pin}")

        # ── Printable Label Preview & Download ──
        st.subheader("🏷️ Printable Shipping Label")
        provider = get_provider("demo")
        label_html = None
        if hasattr(provider, "generate_label"):
            label_html = provider.generate_label(
                dispatch_result.shipment_id,
                tracking_number=dispatch_result.tracking_number,
                order=order,
                quote=selected_quote,
            )

        if label_html:
            st.download_button(
                label="📥 Download Printable Shipping Label (HTML)",
                data=label_html,
                file_name=f"shipping_label_{dispatch_result.tracking_number}.html",
                mime="text/html",
                use_container_width=True,
            )
            with st.expander("👁️ View Rendered Shipping Label Preview", expanded=False):
                try:
                    import streamlit.components.v1 as components
                    components.html(label_html, height=480, scrolling=True)
                except Exception:
                    st.code(label_html[:1500] + "... [HTML preview]", language="html")

        # ── End-to-End Agent Decision Trace ──
        trace: Optional[AgentDecisionTrace] = st.session_state.get("decision_trace")
        if trace:
            with st.expander("🤖 Complete Multi-Stage Agent Decision Trace", expanded=False):
                st.markdown(f"**Trace ID:** `{trace.trace_id}` | **Order:** `{trace.order_reference}` | **Final Status:** `{trace.final_status.value}`")
                for s in trace.steps:
                    st.markdown(f"- **{s.stage}**: `{s.status.value}` ({s.summary}) — _{s.timestamp[:19]}_")
                    evidence_data = getattr(s, "evidence", None) or getattr(s, "details", None)
                    if evidence_data:
                        st.json(evidence_data, expanded=False)

        st.divider()
        if st.button("🔄 Start New Order / Reset Workflow", use_container_width=True):
            for k in [
                "order",
                "quotes",
                "selected_quote",
                "dispatch_result",
                "verification_report",
                "pickup_result",
                "decision_trace",
                "active_demo_scenario",
                "approved_record",
                "approved_proposal_sig",
                "approval_given",
            ]:
                st.session_state.pop(k, None)
            st.rerun()

        return

    # ── Human Approval Gate (Pre-Dispatch Form) ──────────────────────────────
    st.divider()
    st.subheader("🔐 Human Approval Required")
    st.info(
        "Dispatch cannot proceed without explicit human approval. "
        "Provide your name, verify the total cost, and confirm authorization before booking.",
        icon="🔐",
    )

    # Stable, shipment-specific Streamlit widget keys (Task B)
    chk_key = f"approval_chk_{order.order_reference}_{selected_quote.quote_id}"
    name_key = f"approver_name_{order.order_reference}"
    cost_key = f"cost_confirmed_{order.order_reference}_{selected_quote.quote_id}"
    notes_key = f"approval_notes_{order.order_reference}"
    form_key = f"approval_form_{order.order_reference}_{selected_quote.quote_id}"

    with st.form(form_key):
        approved_by = st.text_input(
            "Approver Name / Operator ID *",
            value=st.session_state.get("approved_by", ""),
            key=name_key,
            help="Enter your name or employee ID. Min 2 characters.",
        )
        cost_confirmed = st.number_input(
            f"Confirm total cost (INR) *  [Quote shows: {fmt_inr(current_cost)}]",
            min_value=0.0,
            value=current_cost,
            step=0.01,
            key=cost_key,
        )
        notes = st.text_area(
            "Approval Notes (optional)",
            placeholder="e.g., Approved per customer urgency request.",
            key=notes_key,
        )
        approval_checkbox = st.checkbox(
            f"I, **{approved_by or 'operator'}**, confirm dispatching this shipment via "
            f"**{selected_quote.courier_name}** for **{fmt_inr(cost_confirmed)}** (DEMO booking).",
            key=chk_key,
        )
        submit_approval = st.form_submit_button("🚀 Approve & Dispatch Shipment", type="primary", use_container_width=True)

    if submit_approval:
        if not approved_by or len(approved_by.strip()) < 2:
            st.error("❌ Approver name must be at least 2 characters.")
            return
        if not approval_checkbox:
            st.error("❌ Please tick the confirmation checkbox to proceed.")
            return
        if abs(cost_confirmed - current_cost) > 0.01:
            st.error(f"❌ Confirmed cost ({fmt_inr(cost_confirmed)}) does not match quote landed cost ({fmt_inr(current_cost)}).")
            return

        # Double-dispatch in-flight lock (Task B)
        lock_key = f"dispatch_lock_{order.order_reference}"
        if st.session_state.get(lock_key):
            st.warning("⏳ Dispatch operation is already in progress for this order. Please wait...")
            return
        st.session_state[lock_key] = True

        try:
            approval = ApprovalRecord(
                approved_by=approved_by.strip(),
                quote_id=selected_quote.quote_id,
                total_cost=cost_confirmed,
                notes=notes or None,
            )
            st.session_state["approved_by"] = approved_by.strip()
            st.session_state["approved_record"] = approval
            st.session_state["approved_proposal_sig"] = current_sig
            st.session_state["approval_given"] = True

            _, audit, _, _, dispatch_service = get_services()
            provider = get_provider("demo")

            trace: Optional[AgentDecisionTrace] = st.session_state.get("decision_trace")
            if trace:
                try:
                    trace.record_approval(
                        approved_by=approved_by.strip(),
                        cost_confirmed=cost_confirmed,
                        notes=notes or None,
                        quote_id=selected_quote.quote_id,
                        courier_name=selected_quote.courier_name,
                        order=order,
                        quote=selected_quote,
                    )
                except Exception as exc:
                    st.error(f"❌ Decision trace approval error: {exc}")
                    return

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

                    if result.status == ShipmentStatus.CREATED:
                        pickup_res = None
                        if hasattr(provider, "schedule_pickup"):
                            try:
                                pickup_res = provider.schedule_pickup(result.shipment_id)
                            except Exception:
                                pass
                        st.session_state["pickup_result"] = pickup_res

                        ver_service = ShipmentVerificationService(audit_logger=audit)
                        ship_res = ShipmentResult(
                            success=True,
                            shipment_id=result.shipment_id,
                            tracking_number=result.tracking_number,
                            label_status=result.label_status,
                            label_url_or_data=result.label_data or (provider.generate_label(result.shipment_id, tracking_number=result.tracking_number, order=order, quote=selected_quote) if hasattr(provider, "generate_label") else None),
                            order_reference=order.order_reference,
                            courier_code=selected_quote.courier_code,
                            is_demo_data=True,
                        )
                        report = ver_service.verify_shipment(order, selected_quote, approval, ship_res, provider=provider, pickup_result=pickup_res)
                        st.session_state["verification_report"] = report

                        if trace:
                            trace.record_execution(
                                success=True,
                                shipment_id=result.shipment_id,
                                tracking_number=result.tracking_number,
                                carrier_code=selected_quote.courier_code,
                                idempotency_key=f"idem-{order.order_reference}",
                            )
                            audit_map = {c.criterion_id: c.status.value for c in report.checks}
                            trace.record_verification(
                                verified=(report.overall_status == VerificationStatus.VERIFIED),
                                overall_status=report.overall_status.value,
                                audit_results=audit_map,
                            )

                        st.success(f"✅ **Shipment dispatched (DEMO)**  \nTracking Ref: `{result.tracking_number}`")
                        st.rerun()

                    elif result.status == ShipmentStatus.CREATION_UNCERTAIN:
                        if trace:
                            trace.record_execution(
                                success=False,
                                shipment_id=result.shipment_id,
                                error_type="TIMEOUT_CREATION_UNCERTAIN",
                                notes=result.explanation,
                            )
                        st.error("⛔ Dispatch result is UNCERTAIN (simulated timeout). Retry blocked. Use reconciliation.")
                        st.rerun()

                except ApprovalInvalidatedError as exc:
                    st.error(f"⛔ **Approval Invalidated:** {exc}")
                except StaleQuoteError as exc:
                    st.error(f"⛔ **Stale Quote:** {exc}  \nReturn to Tab 1 and re-fetch rates.")
                except Exception as exc:
                    st.error(f"❌ Dispatch error: {exc}")

        finally:
            st.session_state.pop(lock_key, None)


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
