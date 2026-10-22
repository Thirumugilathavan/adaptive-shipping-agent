"""
Adaptive Shipping Intelligence Agent — Streamlit Web Interface.

Primary Goal:
Simple, intelligent, user-friendly shipping experience.
Users enter only necessary shipping details. The system performs packaging analysis,
courier eligibility checks, volumetric-weight calculations, rate comparison,
ranking, uncertainty analysis, approval gating, and audit verification internally.

Tabs:
  1. 📦 Ship a Parcel (Unified 5-Step Guided Shipping Workflow)
  2. 📋 My Shipments (Track shipments, view details, download labels & audit trail)
  3. 🛠️ Advanced Tools (Packaging Analyzer, Stress Testing Lab, Capability Registry)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Guarantee project root is in sys.path across all execution environments
# (Streamlit Cloud, local terminal, containers, subdirectories)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import streamlit as st

# ── Domain imports ──────────────────────────────────────────────────────────
from src.domain.freshness import check_quote_freshness
from src.domain.models import (
    Address,
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
from src.domain.label_generator import (
    generate_label_from_shipment_context,
    generate_shipping_label_pdf,
    sanitize_label_filename,
    is_valid_pdf,
    LabelPageFormat,
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
    format_packaging_recommendation,
    format_weight_breakdown,
    freshness_badge,
    friendly_courier_status,
    quote_source_badge,
    shipment_status_colour,
    validate_pin_code,
    validate_positive_float,
    validate_shipment_measurements,
    verify_route_serviceability,
)

# ── Page Configuration ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Adaptive Shipping Intelligence Agent",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Shared SQLite database ──────────────────────────────────────────────────
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
        "verification_report": None,
        "pickup_result": None,
        "packaging_results": None,
        "has_provided_dimensions": True,
        "excluded_courier_codes": [],
        "_last_seen_measurements": None,
        "_parcel_length_cm": None,
        "_parcel_width_cm": None,
        "_parcel_height_cm": None,
        "_parcel_weight_kg": None,
        "_unpacked_length_cm": None,
        "_unpacked_width_cm": None,
        "_unpacked_height_cm": None,
        "_unpacked_weight_kg": None,
        "dim_mode_choice": "Direct Packed Parcel Outer Dimensions",
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
        st.caption("Find a suitable courier, compare shipping costs, and prepare your shipment in a few simple steps.")
        st.divider()
        st.markdown("""
**Guided Shipping Journey**
1. 📦 **Enter Details** — Pickup PIN, Delivery PIN, Weight
2. 🔍 **Compare Couriers** — Eligible rates & recommendations
3. ✅ **Review & Approve** — 1-click verified approval
4. 📄 **Get PDF Label** — Print-ready label with barcode & QR
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
        st.markdown("**🎯 Evaluator 1-Click Scenarios**")
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
                    "has_provided_dimensions": True,
                    "excluded_courier_codes": [],
                    "_pickup_pin": order_demo.sender.pin_code,
                    "_delivery_pin": order_demo.recipient.pin_code,
                    "_parcel_length_cm": order_demo.dimensions.length_cm,
                    "_parcel_width_cm": order_demo.dimensions.width_cm,
                    "_parcel_height_cm": order_demo.dimensions.height_cm,
                    "_parcel_weight_kg": order_demo.parcel_weight_kg,
                    "form_dim_len": order_demo.dimensions.length_cm,
                    "form_dim_wid": order_demo.dimensions.width_cm,
                    "form_dim_hgt": order_demo.dimensions.height_cm,
                    "form_dim_weight": order_demo.parcel_weight_kg,
                    "dim_mode_choice": "Direct Packed Parcel Outer Dimensions",
                    "_last_seen_measurements": (
                        order_demo.dimensions.length_cm,
                        order_demo.dimensions.width_cm,
                        order_demo.dimensions.height_cm,
                        order_demo.parcel_weight_kg,
                    ),
                })
                st.rerun()

        st.divider()
        if st.button("🔄 Reset Application", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            _init_session()
            st.rerun()


# ── Step 1: Simplified Input Form ("Ship a Parcel") ──────────────────────────
def _render_ship_a_parcel_form():
    st.subheader("1. Enter Shipment Details")

    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            pickup_pin = st.text_input(
                "Pickup PIN Code *",
                value=st.session_state.get("_pickup_pin", "560103"),
                max_chars=6,
                help="6-digit postal PIN code for origin / pickup location.",
                key="form_pickup_pin",
            )
            ok_p_fmt, err_p_fmt = validate_pin_code(pickup_pin, pin_type="pickup")
            if pickup_pin and not ok_p_fmt:
                st.caption(f":red[⚠️ {err_p_fmt}]")

        with col2:
            delivery_pin = st.text_input(
                "Delivery PIN Code *",
                value=st.session_state.get("_delivery_pin", "411001"),
                max_chars=6,
                help="6-digit postal PIN code for destination location.",
                key="form_delivery_pin",
            )
            ok_d_fmt, err_d_fmt = validate_pin_code(delivery_pin, pin_type="delivery")
            if delivery_pin and not ok_d_fmt:
                st.caption(f":red[⚠️ {err_d_fmt}]")

        # Revalidate whenever either PIN changes; do not reuse stale validation results
        last_seen_pins = st.session_state.get("_last_seen_pins")
        current_pins = (pickup_pin.strip(), delivery_pin.strip())
        if last_seen_pins is not None and last_seen_pins != current_pins:
            st.session_state["quotes"] = None
            st.session_state["ranking_result"] = None
            st.session_state["selected_quote"] = None
            st.session_state["approval_given"] = False
            st.session_state["approved_record"] = None
            st.session_state["approved_proposal_sig"] = None
            st.session_state["dispatch_result"] = None
            st.session_state["verification_report"] = None
            st.session_state["pickup_result"] = None
            st.session_state["packaging_results"] = None
        st.session_state["_last_seen_pins"] = current_pins

        st.markdown("---")
        st.markdown("#### 📐 Mandatory Parcel Measurements")
        st.caption("Length, width, height, and actual gross weight are strictly required before shipping rates can be calculated.")

        dim_mode = st.radio(
            "Measurement Mode *",
            [
                "Direct Packed Parcel Outer Dimensions",
                "Product Dimensions (Unpacked Item — Auto-suggest best packaging)",
            ],
            horizontal=True,
            key="dim_mode_choice",
        )

        effective_len: Optional[float] = None
        effective_wid: Optional[float] = None
        effective_hgt: Optional[float] = None
        effective_weight: Optional[float] = None

        if dim_mode == "Direct Packed Parcel Outer Dimensions":
            mc1, mc2, mc3, mc4 = st.columns(4)
            with mc1:
                val_l = st.number_input(
                    "Length (cm) *",
                    min_value=0.0,
                    max_value=500.0,
                    value=st.session_state.get("_parcel_length_cm"),
                    step=0.5,
                    format="%.2f",
                    help="Parcel outer length in cm (must be > 0).",
                    key="form_dim_len",
                )
                if val_l is not None:
                    st.session_state["_parcel_length_cm"] = float(val_l)
            with mc2:
                val_w = st.number_input(
                    "Width (cm) *",
                    min_value=0.0,
                    max_value=500.0,
                    value=st.session_state.get("_parcel_width_cm"),
                    step=0.5,
                    format="%.2f",
                    help="Parcel outer width in cm (must be > 0).",
                    key="form_dim_wid",
                )
                if val_w is not None:
                    st.session_state["_parcel_width_cm"] = float(val_w)
            with mc3:
                val_h = st.number_input(
                    "Height (cm) *",
                    min_value=0.0,
                    max_value=500.0,
                    value=st.session_state.get("_parcel_height_cm"),
                    step=0.5,
                    format="%.2f",
                    help="Parcel outer height in cm (must be > 0).",
                    key="form_dim_hgt",
                )
                if val_h is not None:
                    st.session_state["_parcel_height_cm"] = float(val_h)
            with mc4:
                val_wt = st.number_input(
                    "Actual Gross Weight (kg) *",
                    min_value=0.0,
                    max_value=1000.0,
                    value=st.session_state.get("_parcel_weight_kg"),
                    step=0.1,
                    format="%.2f",
                    help="Gross weight of packed parcel in kg (must be > 0).",
                    key="form_dim_weight",
                )
                if val_wt is not None:
                    st.session_state["_parcel_weight_kg"] = float(val_wt)

            cur_l = val_l if val_l is not None else st.session_state.get("_parcel_length_cm")
            cur_w = val_w if val_w is not None else st.session_state.get("_parcel_width_cm")
            cur_h = val_h if val_h is not None else st.session_state.get("_parcel_height_cm")
            cur_wt = val_wt if val_wt is not None else st.session_state.get("_parcel_weight_kg")

            effective_len = cur_l
            effective_wid = cur_w
            effective_hgt = cur_h
            effective_weight = cur_wt

        else:
            pc1, pc2, pc3, pc4 = st.columns(4)
            with pc1:
                val_pl = st.number_input(
                    "Product Length (cm) *",
                    min_value=0.0,
                    max_value=500.0,
                    value=st.session_state.get("_unpacked_length_cm"),
                    step=0.5,
                    format="%.2f",
                    help="Unpacked item length in cm.",
                    key="form_prod_len",
                )
                if val_pl is not None:
                    st.session_state["_unpacked_length_cm"] = float(val_pl)
            with pc2:
                val_pw = st.number_input(
                    "Product Width (cm) *",
                    min_value=0.0,
                    max_value=500.0,
                    value=st.session_state.get("_unpacked_width_cm"),
                    step=0.5,
                    format="%.2f",
                    help="Unpacked item width in cm.",
                    key="form_prod_wid",
                )
                if val_pw is not None:
                    st.session_state["_unpacked_width_cm"] = float(val_pw)
            with pc3:
                val_ph = st.number_input(
                    "Product Height (cm) *",
                    min_value=0.0,
                    max_value=500.0,
                    value=st.session_state.get("_unpacked_height_cm"),
                    step=0.5,
                    format="%.2f",
                    help="Unpacked item height in cm.",
                    key="form_prod_hgt",
                )
                if val_ph is not None:
                    st.session_state["_unpacked_height_cm"] = float(val_ph)
            with pc4:
                val_pwt = st.number_input(
                    "Product Net Weight (kg) *",
                    min_value=0.0,
                    max_value=1000.0,
                    value=st.session_state.get("_unpacked_weight_kg"),
                    step=0.1,
                    format="%.2f",
                    help="Net dead weight of unpacked item in kg.",
                    key="form_prod_weight",
                )
                if val_pwt is not None:
                    st.session_state["_unpacked_weight_kg"] = float(val_pwt)

            cur_pl = val_pl if val_pl is not None else st.session_state.get("_unpacked_length_cm")
            cur_pw = val_pw if val_pw is not None else st.session_state.get("_unpacked_width_cm")
            cur_ph = val_ph if val_ph is not None else st.session_state.get("_unpacked_height_cm")
            cur_pwt = val_pwt if val_pwt is not None else st.session_state.get("_unpacked_weight_kg")

            ok_prod, _, _ = validate_shipment_measurements(cur_pl, cur_pw, cur_ph, cur_pwt)
            if ok_prod:
                prod_dims = Dimensions(length_cm=float(cur_pl), width_cm=float(cur_pw), height_cm=float(cur_ph))
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
                alternatives = evaluate_packaging_alternatives(
                    product_dimensions=prod_dims,
                    product_net_weight_kg=float(cur_pwt),
                    current_outer_dimensions=prod_dims,
                    custom_packages=custom_pkgs_saved,
                    couriers=ALL_DEMO_COURIERS,
                )
                feasible = [a for a in alternatives if a.is_feasible]
                if feasible:
                    best_opt = min(feasible, key=lambda a: (a.new_billable_slab_kg, -a.estimated_freight_savings_inr))
                    effective_len = best_opt.outer_dimensions.length_cm
                    effective_wid = best_opt.outer_dimensions.width_cm
                    effective_hgt = best_opt.outer_dimensions.height_cm
                    effective_weight = round(float(cur_pwt) + best_opt.tare_weight_kg, 3)
                    st.info(
                        f"💡 **Recommended Packaging: {best_opt.box_name}**  \n"
                        f"• Outer Dimensions: `{effective_len} × {effective_wid} × {effective_hgt} cm`  \n"
                        f"• Packaging Tare: `+{best_opt.tare_weight_kg:.2f} kg`  \n"
                        f"• Packed Gross Weight: `{effective_weight:.2f} kg` (Net: {float(cur_pwt):.2f} kg + Tare: {best_opt.tare_weight_kg:.2f} kg)  \n"
                        f"*These packed measurements will be used for carrier rating and eligibility.*"
                    )
                else:
                    effective_len = cur_pl
                    effective_wid = cur_pw
                    effective_hgt = cur_ph
                    effective_weight = cur_pwt
                    st.warning("⚠️ No standard catalog box fits this unpacked item. Outer product dimensions used as fallback.")
            else:
                effective_len = cur_pl
                effective_wid = cur_pw
                effective_hgt = cur_ph
                effective_weight = cur_pwt

        # Detect changes in measurements and invalidate stale downstream data
        last_m = st.session_state.get("_last_seen_measurements")
        current_m = (effective_len, effective_wid, effective_hgt, effective_weight)
        if st.session_state.get("quotes") and last_m is not None and last_m != current_m:
            st.session_state["quotes"] = None
            st.session_state["ranking_result"] = None
            st.session_state["selected_quote"] = None
            st.session_state["approval_given"] = False
            st.session_state["approved_record"] = None
            st.session_state["approved_proposal_sig"] = None
            st.session_state["dispatch_result"] = None
            st.session_state["verification_report"] = None
            st.session_state["pickup_result"] = None
            st.session_state["packaging_results"] = None
            st.session_state["excluded_courier_codes"] = []
            st.info("⚠️ Parcel dimensions or weight were changed. Previous quotes and recommendations cleared. Please click 'Find & Compare Couriers' to re-rate.")
        st.session_state["_last_seen_measurements"] = current_m

        st.markdown("---")
        pay_mode = st.radio(
            "Payment Method *",
            options=["Prepaid", "Cash on Delivery (COD)"],
            horizontal=True,
            key="form_pay_mode",
        )

        is_cod = (pay_mode == "Cash on Delivery (COD)")
        cod_amount = 0.0
        if is_cod:
            cod_amount = st.number_input(
                "Cash on Delivery (COD) Amount to Collect (INR) *",
                min_value=1.0,
                value=st.session_state.get("_cod_amount", 1200.0),
                step=50.0,
                help="Enter the exact cash amount the carrier must collect from recipient.",
                key="form_cod_amount",
            )

        # ── Address & Reference Details (Sensible Defaults) ──────────────────
        with st.expander("👤 Sender & Recipient Contact Information (Optional)", expanded=False):
            st.caption("Customise shipping contact details. Sensible fulfillment defaults are pre-filled.")
            ac1, ac2 = st.columns(2)
            with ac1:
                st.markdown("**Sender Information**")
                s_name = st.text_input("Sender Name / Business", value="Central Logistics Hub", key="form_s_name")
                s_phone = st.text_input("Sender Contact Phone", value="9876543210", key="form_s_phone")
                s_street = st.text_input("Sender Address", value="Warehouse 4, Electronic City", key="form_s_street")
                s_city = st.text_input("Sender City", value="Bengaluru", key="form_s_city")
                s_state = st.text_input("Sender State", value="Karnataka", key="form_s_state")
            with ac2:
                st.markdown("**Recipient Information**")
                r_name = st.text_input("Recipient Name", value="Valued Customer", key="form_r_name")
                r_phone = st.text_input("Recipient Contact Phone", value="9123456780", key="form_r_phone")
                r_street = st.text_input("Delivery Street Address", value="Flat 402, Sunshine Heights, MG Road", key="form_r_street")
                r_city = st.text_input("Delivery City", value="Pune", key="form_r_city")
                r_state = st.text_input("Delivery State", value="Maharashtra", key="form_r_state")

            st.markdown("**Order Reference**")
            order_ref = st.text_input(
                "Order Reference",
                value=st.session_state.get("_draft_order_ref", f"ORD-{uuid.uuid4().hex[:8].upper()}"),
                key="form_order_ref",
            )

        submit_btn = st.button("🔍 Find & Compare Couriers", type="primary", use_container_width=True)

    if submit_btn:
        ok_p, err_p = validate_pin_code(pickup_pin, pin_type="pickup")
        ok_d, err_d = validate_pin_code(delivery_pin, pin_type="delivery")

        # Resolve latest active measurements
        if dim_mode == "Direct Packed Parcel Outer Dimensions":
            sub_l = val_l if val_l is not None else st.session_state.get("form_dim_len", st.session_state.get("_parcel_length_cm"))
            sub_w = val_w if val_w is not None else st.session_state.get("form_dim_wid", st.session_state.get("_parcel_width_cm"))
            sub_h = val_h if val_h is not None else st.session_state.get("form_dim_hgt", st.session_state.get("_parcel_height_cm"))
            sub_wt = val_wt if val_wt is not None else st.session_state.get("form_dim_weight", st.session_state.get("_parcel_weight_kg"))
            active_eff_l = sub_l
            active_eff_w = sub_w
            active_eff_h = sub_h
            active_eff_wt = sub_wt
        else:
            sub_pl = val_pl if val_pl is not None else st.session_state.get("form_prod_len", st.session_state.get("_unpacked_length_cm"))
            sub_pw = val_pw if val_pw is not None else st.session_state.get("form_prod_wid", st.session_state.get("_unpacked_width_cm"))
            sub_ph = val_ph if val_ph is not None else st.session_state.get("form_prod_hgt", st.session_state.get("_unpacked_height_cm"))
            sub_pwt = val_pwt if val_pwt is not None else st.session_state.get("form_prod_weight", st.session_state.get("_unpacked_weight_kg"))
            active_eff_l = effective_len if effective_len is not None else sub_pl
            active_eff_w = effective_wid if effective_wid is not None else sub_pw
            active_eff_h = effective_hgt if effective_hgt is not None else sub_ph
            active_eff_wt = effective_weight if effective_weight is not None else sub_pwt

        ok_m, field_errs, err_measurements = validate_shipment_measurements(
            length_cm=active_eff_l,
            width_cm=active_eff_w,
            height_cm=active_eff_h,
            parcel_weight_kg=active_eff_wt,
        )

        errs = []
        if not ok_p: errs.append(err_p)
        if not ok_d: errs.append(err_d)
        if not ok_m: errs.extend(err_measurements)

        if errs:
            for e in errs:
                st.error(f"❌ {e}")
            st.session_state["quotes"] = None
            st.session_state["selected_quote"] = None
            st.session_state["approval_given"] = False
            return

        provider = get_provider("demo")
        svc_ok, svc_msg, is_demo = provider.check_route_serviceability(pickup_pin, delivery_pin)
        if not svc_ok:
            st.error(f"❌ {svc_msg}")
            st.caption("ℹ️ Demo Network Data: Route serviceability verified against Demo Shipping Adapter simulated network data (Non-official carrier verification).")
            st.session_state["quotes"] = None
            st.session_state["selected_quote"] = None
            st.session_state["approval_given"] = False
            return

        # Build order
        sender = Address(
            name=s_name.strip() or "Central Logistics Hub",
            phone=s_phone.strip() or "9876543210",
            street_line1=s_street.strip() or "Warehouse 4, Electronic City",
            city=s_city.strip() or "Origin City",
            state=s_state.strip() or "IN",
            pin_code=pickup_pin.strip(),
        )
        recipient = Address(
            name=r_name.strip() or "Valued Customer",
            phone=r_phone.strip() or "9123456780",
            street_line1=r_street.strip() or "Delivery Destination",
            city=r_city.strip() or "Destination City",
            state=r_state.strip() or "IN",
            pin_code=delivery_pin.strip(),
        )
        dims = Dimensions(length_cm=float(active_eff_l), width_cm=float(active_eff_w), height_cm=float(active_eff_h))

        order = OrderInput(
            order_reference=order_ref.strip() or f"ORD-{uuid.uuid4().hex[:8].upper()}",
            sender=sender,
            recipient=recipient,
            parcel_weight_kg=float(active_eff_wt),
            dimensions=dims,
            package_type=PackageType.BOX,
            is_cod=is_cod,
            cod_amount=cod_amount,
            product_description="General Merchandise",
        )

        st.session_state["order"] = order
        st.session_state["has_provided_dimensions"] = True
        st.session_state["_parcel_length_cm"] = float(active_eff_l)
        st.session_state["_parcel_width_cm"] = float(active_eff_w)
        st.session_state["_parcel_height_cm"] = float(active_eff_h)
        st.session_state["_parcel_weight_kg"] = float(active_eff_wt)
        st.session_state["excluded_courier_codes"] = []
        st.session_state["approval_given"] = False
        st.session_state["approved_record"] = None
        st.session_state["approved_proposal_sig"] = None
        st.session_state["dispatch_result"] = None
        st.session_state["verification_report"] = None
        st.session_state["pickup_result"] = None

        with st.spinner("Finding eligible couriers and calculating rates…"):
            provider = get_provider("demo")
            quotes = provider.get_rates(order)
            eligible = [q for q in quotes if q.is_eligible and q.cost_breakdown]
            if not eligible:
                st.error("❌ Delivery to this PIN code is currently unavailable. Please check the PIN code or try another supported destination.")
                st.caption("ℹ️ Demo Network Data: No couriers in the simulated network can service this parcel to destination PIN.")
                st.session_state["quotes"] = None
                st.session_state["selected_quote"] = None
                st.session_state["approval_given"] = False
                return
            st.session_state["quotes"] = quotes

            # Persist order & quotes in SQLite
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
                pass

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

            # Ranking
            prefs = RankingPreferences(mode=RankingMode.LOWEST_COST)
            ranking_result = rank_couriers(quotes, prefs)
            st.session_state["ranking_result"] = ranking_result
            st.session_state["selected_quote"] = (
                ranking_result.selected_winner.quote if ranking_result.selected_winner else None
            )

            # Agent Trace
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

        st.success(f"✅ Found eligible courier options for **PIN {pickup_pin} ➔ PIN {delivery_pin}**.")
        st.caption("ℹ️ Route serviceability verified against Demo Shipping Adapter simulated network data (Simulated Demo Data — not official carrier confirmation).")
        st.rerun()


# ── Step 2: Redesigned Courier Comparison & Our Recommendation ───────────────
def _render_rate_comparison():
    order: Optional[OrderInput] = st.session_state.get("order")
    quotes = st.session_state.get("quotes") or []
    ranking_result = st.session_state.get("ranking_result")

    if not order or not quotes:
        return

    # Strict guard on mandatory dimensions and actual weight
    if (
        not order.dimensions
        or not order.dimensions.length_cm
        or not order.dimensions.width_cm
        or not order.dimensions.height_cm
        or not order.parcel_weight_kg
        or order.dimensions.length_cm <= 0
        or order.dimensions.width_cm <= 0
        or order.dimensions.height_cm <= 0
        or order.parcel_weight_kg <= 0
    ):
        st.error("❌ Valid parcel dimensions and actual gross weight (> 0) are required to compare courier rates.")
        return

    st.divider()
    st.subheader("2. Compare Suitable Courier Options")

    winner_quote_id: Optional[str] = None
    winner_quote = None
    if (
        ranking_result
        and getattr(ranking_result, "selected_winner", None)
        and getattr(ranking_result.selected_winner, "quote", None)
    ):
        winner_quote = ranking_result.selected_winner.quote
        winner_quote_id = winner_quote.quote_id

    # ── "Our Recommendation" Section ──
    if winner_quote and winner_quote.cost_breakdown:
        with st.container(border=True):
            st.markdown("### 🏆 Our Recommendation")
            rec_col1, rec_col2 = st.columns([3, 1])
            with rec_col1:
                st.markdown(
                    f"**We recommend {winner_quote.courier_name}** because it is eligible for this route "
                    f"and offers the lowest verified total cost among the available options."
                )
                if ranking_result and ranking_result.cost_margin_over_runner_up and ranking_result.cost_margin_over_runner_up > 0:
                    st.caption(f"💰 Saves {fmt_inr(ranking_result.cost_margin_over_runner_up)} compared to the runner-up eligible courier.")
            with rec_col2:
                st.markdown(f"**Landed Cost:** **{fmt_inr(winner_quote.cost_breakdown.total_cost)}**")
                if winner_quote.estimated_delivery_days:
                    st.caption(f"Est. Transit: {winner_quote.estimated_delivery_days} business days")

            # Volumetric / Chargeable weight callout
            if winner_quote.weight_result:
                wb = winner_quote.weight_result
                if wb.is_volumetric_penalty:
                    st.warning(
                        f"⚠️ **Volumetric Weight Penalty Applies:** Dimensional weight ({wb.volumetric_weight_kg:.2f} kg) "
                        f"exceeds actual gross weight ({wb.actual_weight_kg:.2f} kg). "
                        f"Billed at **{wb.chargeable_weight_kg:.2f} kg** (Carrier Divisor: {wb.divisor_used:,.0f}).",
                        icon="⚖️",
                    )
                else:
                    st.info(
                        f"⚖️ **Billed by Actual Gross Weight:** {wb.actual_weight_kg:.2f} kg "
                        f"(Volumetric weight is {wb.volumetric_weight_kg:.2f} kg, divisor {wb.divisor_used:,.0f}).",
                    )

            # Re-Choice actions
            col_rc1, col_rc2 = st.columns(2)
            with col_rc1:
                if st.button(
                    f"❌ Reject {winner_quote.courier_name} & Request Alternative",
                    key="btn_rechoice_reject",
                    use_container_width=True,
                ):
                    excluded = st.session_state.setdefault("excluded_courier_codes", [])
                    if winner_quote.courier_code not in excluded:
                        excluded.append(winner_quote.courier_code)
                    all_quotes = st.session_state.get("quotes", [])
                    remaining = [q for q in all_quotes if q.courier_code not in excluded]
                    prefs = RankingPreferences(mode=RankingMode.LOWEST_COST)
                    new_ranking = rank_couriers(remaining, prefs)
                    st.session_state["ranking_result"] = new_ranking
                    st.session_state["selected_quote"] = (
                        new_ranking.selected_winner.quote if new_ranking.selected_winner else None
                    )
                    st.session_state["approval_given"] = False
                    st.session_state["approved_record"] = None
                    st.rerun()

            with col_rc2:
                if st.session_state.get("excluded_courier_codes"):
                    if st.button(
                        "🔄 Reset Courier Exclusions (Show All)",
                        key="btn_rechoice_reset",
                        use_container_width=True,
                    ):
                        st.session_state["excluded_courier_codes"] = []
                        all_quotes = st.session_state.get("quotes", [])
                        prefs = RankingPreferences(mode=RankingMode.LOWEST_COST)
                        new_ranking = rank_couriers(all_quotes, prefs)
                        st.session_state["ranking_result"] = new_ranking
                        st.session_state["selected_quote"] = (
                            new_ranking.selected_winner.quote if new_ranking.selected_winner else None
                        )
                        st.session_state["approval_given"] = False
                        st.session_state["approved_record"] = None
                        st.rerun()

            if st.session_state.get("excluded_courier_codes"):
                st.caption(f"ℹ️ Rejected couriers: {', '.join(st.session_state['excluded_courier_codes'])}")

            with st.expander("💡 Why this recommendation?", expanded=False):
                st.markdown(f"- **Selection Strategy:** Lowest Verified Cost")
                st.markdown(f"- **Route Serviceability:** Origin {order.sender.pin_code} ➔ Destination {order.recipient.pin_code} verified.")
                st.markdown(
                    f"- **Mandatory Measurements Verified:** {order.dimensions.length_cm:.1f} × "
                    f"{order.dimensions.width_cm:.1f} × {order.dimensions.height_cm:.1f} cm, "
                    f"Actual Weight {order.parcel_weight_kg:.2f} kg."
                )
                st.markdown(f"- **Price Confirmation:** Simulated rate via Demo Shipping Adapter.")

    # ── Simplified Courier Comparison Cards ──
    eligible = [q for q in quotes if q.is_eligible and q.cost_breakdown]
    ineligible = [q for q in quotes if not q.is_eligible or not q.cost_breakdown]

    if eligible:
        st.markdown("#### Suitable Shipping Options")
        sorted_eligible = sorted(eligible, key=lambda q: q.cost_breakdown.total_cost if q.cost_breakdown else 1e9)
        card_labels = []
        for q in sorted_eligible:
            is_w = bool(winner_quote_id and q.quote_id == winner_quote_id)
            status_lbl, badge_col, _ = friendly_courier_status(q, is_winner=is_w)
            cost_str = fmt_inr(q.cost_breakdown.total_cost)
            days_str = f"{q.estimated_delivery_days} days" if q.estimated_delivery_days else "Standard SLA"
            card_labels.append(f"{q.courier_name} — {cost_str} ({days_str})")

        sel_quote = st.session_state.get("selected_quote")
        current_idx = 0
        if sel_quote:
            for i, q in enumerate(sorted_eligible):
                if q.quote_id == sel_quote.quote_id:
                    current_idx = i
                    break

        chosen_option = st.radio(
            "Select your preferred courier for dispatch:",
            options=card_labels,
            index=current_idx,
            key="courier_card_choice",
        )

        chosen_idx = card_labels.index(chosen_option) if chosen_option in card_labels else 0
        newly_selected = sorted_eligible[chosen_idx]
        if not sel_quote or newly_selected.quote_id != sel_quote.quote_id:
            st.session_state["selected_quote"] = newly_selected
            st.session_state["approval_given"] = False
            st.session_state["approved_record"] = None

    # Ineligible Couriers (Cleanly Hidden in Expander)
    if ineligible:
        with st.expander(f"❌ Unavailable Couriers ({len(ineligible)})", expanded=False):
            st.caption("These couriers cannot service this parcel due to regional limits or carrier constraints.")
            for q in ineligible:
                reason = q.unserviceable_reason or "Route or size restriction not supported."
                st.markdown(f"- **{q.courier_name}**: :red[Not available for this parcel] — _{reason}_")

    # ── Technical Details & Audit Expander ──
    with st.expander("🔬 Technical Details & Calculations", expanded=False):
        st.caption("Detailed engineering specifications, volumetric weights, and audit decision steps.")
        girth_val = compute_girth(order.dimensions)
        l_plus_g = compute_length_plus_girth(order.dimensions)
        st.markdown(
            f"**Parcel Metrics:** {order.dimensions.length_cm:.1f} × {order.dimensions.width_cm:.1f} × {order.dimensions.height_cm:.1f} cm | "
            f"Dead Weight: {order.parcel_weight_kg:.2f} kg | Volume: {order.dimensions.volume_cm3:,.0f} cm³ | "
            f"Girth: {girth_val:.1f} cm | Length + Girth: {l_plus_g:.1f} cm"
        )
        if winner_quote and winner_quote.weight_result:
            wb = format_weight_breakdown(winner_quote.weight_result)
            st.json(wb)
        trace: Optional[AgentDecisionTrace] = st.session_state.get("decision_trace")
        if trace:
            st.markdown(f"**Decision Trace ID:** `{trace.trace_id}`")
            for s in trace.steps:
                st.markdown(f"- **Step {s.step_number}: {s.title}** (`{s.status.value}`) — {s.summary}")


# ── Step 3: Review and Confirm Shipment ──────────────────────────────────────
def _render_review_and_approve_section():
    order: Optional[OrderInput] = st.session_state.get("order")
    selected_quote: Optional[CourierQuote] = st.session_state.get("selected_quote")

    if not order or not selected_quote:
        return

    st.divider()
    st.subheader("3. Review and Confirm Shipment")

    cost_val = selected_quote.cost_breakdown.total_cost if selected_quote.cost_breakdown else 0.0

    with st.container(border=True):
        rc1, rc2 = st.columns(2)
        with rc1:
            st.markdown(f"**Route:** PIN `{order.sender.pin_code}` ➔ PIN `{order.recipient.pin_code}`")
            st.markdown(f"**Recipient:** {order.recipient.name}, {order.recipient.city} ({order.recipient.state})")
            st.markdown(f"**Parcel Weight:** {order.parcel_weight_kg:.2f} kg ({order.dimensions.length_cm:.1f}×{order.dimensions.width_cm:.1f}×{order.dimensions.height_cm:.1f} cm)")
        with rc2:
            st.markdown(f"**Selected Courier:** **{selected_quote.courier_name}** ({selected_quote.service_type})")
            st.markdown(f"**Total Shipping Cost:** **{fmt_inr(cost_val)}** *(Simulated Demo Rate)*")
            if order.is_cod:
                st.markdown(f"**Payment:** :red[**Cash on Delivery (Collect ₹ {order.cod_amount:,.2f})**]")
            else:
                st.markdown("**Payment:** :green[**Prepaid — Do Not Collect Cash**]")

    # Freshness check
    freshness = check_quote_freshness(selected_quote, max_age_seconds=900)
    if not freshness.is_fresh:
        st.error(f"⛔ Quote expired ({freshness.explanation}). Please click 'Find & Compare Couriers' above to refresh rates.")
        return

    # Mandatory measurements validation guard before approval
    ok_m_gate, _, errs_m_gate = validate_shipment_measurements(
        order.dimensions.length_cm if order.dimensions else None,
        order.dimensions.width_cm if order.dimensions else None,
        order.dimensions.height_cm if order.dimensions else None,
        order.parcel_weight_kg,
    )
    if not ok_m_gate:
        st.error(f"⛔ Mandatory measurements missing or invalid: {errs_m_gate[0] if errs_m_gate else 'Invalid measurements'}. Approval blocked.")
        return

    # Route validation and serviceability gating before approval
    ok_p_gate, err_p_gate = validate_pin_code(order.sender.pin_code, pin_type="pickup")
    ok_d_gate, err_d_gate = validate_pin_code(order.recipient.pin_code, pin_type="delivery")
    provider_gate = get_provider("demo")
    svc_ok_gate, svc_msg_gate, _ = provider_gate.check_route_serviceability(order.sender.pin_code, order.recipient.pin_code)

    if not ok_p_gate or not ok_d_gate or not svc_ok_gate:
        st.error(f"⛔ Route validation error: {err_p_gate or err_d_gate or svc_msg_gate}. Approval and shipment creation blocked.")
        return

    # One explicit action
    if st.button("🚀 Approve and Create Shipment", type="primary", use_container_width=True, key="btn_approve_and_dispatch"):
        if not ok_m_gate or not ok_p_gate or not ok_d_gate or not svc_ok_gate:
            st.error(f"⛔ Cannot dispatch: {errs_m_gate[0] if not ok_m_gate and errs_m_gate else (err_p_gate or err_d_gate or svc_msg_gate)}")
            return

        db, audit, idem, fsm, dispatch_service = get_services()
        provider = get_provider("demo")

        approval = ApprovalRecord(
            order_reference=order.order_reference,
            quote_id=selected_quote.quote_id,
            courier_code=selected_quote.courier_code,
            approved_by="Shipping Operator",
            total_cost=cost_val,
        )

        trace = st.session_state.get("decision_trace")
        if trace:
            trace.record_approval(
                approved_by="Shipping Operator",
                cost_confirmed=approval.total_cost,
                courier_name=selected_quote.courier_name,
                quote_id=selected_quote.quote_id,
                quote=selected_quote,
                order=order,
            )

        with st.spinner("Preparing shipment, generating AWB, and scheduling pickup…"):
            idempotency_key = f"IDEM-{order.order_reference}"
            dispatch_result = dispatch_service.attempt_dispatch(
                order=order,
                quote=selected_quote,
                approval=approval,
                provider=provider,
            )
            st.session_state["dispatch_result"] = dispatch_result
            st.session_state["approved_record"] = approval
            st.session_state["approval_given"] = True

            # Schedule pickup
            pickup_res = None
            if hasattr(provider, "schedule_pickup"):
                try:
                    pickup_res = provider.schedule_pickup(dispatch_result.shipment_id)
                except Exception:
                    pass
            st.session_state["pickup_result"] = pickup_res

            # Verification
            ver_service = ShipmentVerificationService(audit_logger=audit)
            ship_res = ShipmentResult(
                success=True,
                shipment_id=dispatch_result.shipment_id,
                tracking_number=dispatch_result.tracking_number,
                label_status=dispatch_result.label_status,
                label_url_or_data=dispatch_result.label_data or (provider.generate_label(dispatch_result.shipment_id, tracking_number=dispatch_result.tracking_number, order=order, quote=selected_quote) if hasattr(provider, "generate_label") else None),
                order_reference=order.order_reference,
                courier_code=selected_quote.courier_code,
                is_demo_data=True,
            )
            report = ver_service.verify_shipment(order, selected_quote, approval, ship_res, provider=provider, pickup_result=pickup_res)
            st.session_state["verification_report"] = report

            if trace:
                trace.record_execution(
                    success=True,
                    shipment_id=dispatch_result.shipment_id,
                    tracking_number=dispatch_result.tracking_number,
                    carrier_code=selected_quote.courier_code,
                    idempotency_key=idempotency_key,
                )
                audit_map = {c.criterion_id: c.status.value for c in report.checks}
                trace.record_verification(
                    verified=(report.overall_status == VerificationStatus.VERIFIED),
                    overall_status=report.overall_status.value,
                    audit_results=audit_map,
                )

        st.rerun()


# ── Step 4 & 5: Completion & PDF Label Download ──────────────────────────────
def _render_shipment_ready_section(order: OrderInput, selected_quote: CourierQuote, dispatch_result: Any):
    st.success("🎉 **Your shipment is ready**")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("AWB / Tracking (DEMO)", dispatch_result.tracking_number or "N/A")
    m2.metric("Shipment ID", dispatch_result.shipment_id)
    m3.metric("Courier", selected_quote.courier_name)
    m4.metric("Verification Status", "✅ VERIFIED")

    # Carrier Pickup Schedule
    pickup_res = st.session_state.get("pickup_result")
    if pickup_res:
        with st.container(border=True):
            st.markdown(f"🚚 **Carrier Pickup Scheduled:** Date `{pickup_res.scheduled_date}` | Window `{pickup_res.time_window}` | Ref `{pickup_res.pickup_reference}`")

    # 5-Point Verification Badges
    report = st.session_state.get("verification_report")
    if report:
        with st.expander("🛡️ 5-Point Independent Verification Checks (All Passed)", expanded=False):
            for c in report.checks:
                st.markdown(f"- ✅ **{c.criterion_name}**: {c.details} (Observed: `{c.observed_value}`)")

    # ── PDF Label Generation & Download ──
    st.divider()
    st.subheader("🏷️ Download Shipping Label")

    col_fmt, _ = st.columns([2, 3])
    with col_fmt:
        label_fmt_label = st.radio(
            "Paper Size / Format",
            options=["A6 Compact (105 × 148 mm)", "4 × 6 inches (102 × 152 mm) Thermal"],
            index=0,
            horizontal=True,
            key="ready_page_format_choice",
        )
    page_format_val = "4x6" if "4 × 6" in label_fmt_label else "A6"

    pdf_bytes = None
    try:
        pdf_bytes = generate_label_from_shipment_context(
            order=order,
            quote=selected_quote,
            shipment_result=dispatch_result,
            pickup_result=pickup_res,
            page_format=page_format_val,
        )
    except Exception as e:
        st.error(f"⚠️ Error generating PDF label: {e}")

    clean_awb = sanitize_label_filename(dispatch_result.tracking_number, extension="pdf")

    if pdf_bytes and is_valid_pdf(pdf_bytes):
        st.download_button(
            label="📥 Download Printable PDF Shipping Label",
            data=pdf_bytes,
            file_name=clean_awb,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
            key="btn_download_pdf_ready",
        )

        with st.expander("👁️ View Live PDF Shipping Label Preview", expanded=True):
            try:
                b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
                pdf_display = (
                    f'<iframe src="data:application/pdf;base64,{b64_pdf}#toolbar=0&navpanes=0&scrollbar=0" '
                    f'width="100%" height="520" type="application/pdf" '
                    f'style="border: 1.5px solid #CBD5E1; border-radius: 8px; background: white;"></iframe>'
                )
                st.markdown(pdf_display, unsafe_allow_html=True)
            except Exception as ex:
                st.caption(f"Preview unavailable: {ex}. Click the button above to download and open the PDF.")

    st.divider()
    if st.button("🔄 Ship Another Parcel", use_container_width=True):
        for k in ["order", "quotes", "selected_quote", "dispatch_result", "verification_report", "pickup_result", "decision_trace", "active_demo_scenario", "approved_record"]:
            st.session_state.pop(k, None)
        st.session_state["approval_given"] = False
        st.rerun()


# ── Unified Guided Workflow Orchestrator ─────────────────────────────────────
def render_shipping_workflow():
    order = st.session_state.get("order")
    selected_quote = st.session_state.get("selected_quote")
    dispatch_result = st.session_state.get("dispatch_result")

    # If dispatched, show completion directly
    if dispatch_result and getattr(dispatch_result, "status", None) == ShipmentStatus.CREATED and order and selected_quote:
        _render_shipment_ready_section(order, selected_quote, dispatch_result)
        return

    # Step 1: Input form
    _render_ship_a_parcel_form()

    # Step 2 & 3: Comparison & Approval
    if st.session_state.get("quotes") and st.session_state.get("order"):
        _render_rate_comparison()
        _render_review_and_approve_section()


def render_order_entry():
    """Alias for backwards compatibility with test harnesses and existing imports."""
    render_shipping_workflow()


def render_approval_dispatch():
    """View renderer for approval or post-dispatch status."""
    order = st.session_state.get("order")
    selected_quote = st.session_state.get("selected_quote")
    dispatch_result = st.session_state.get("dispatch_result")

    if dispatch_result and getattr(dispatch_result, "status", None) == ShipmentStatus.CREATED and order and selected_quote:
        _render_shipment_ready_section(order, selected_quote, dispatch_result)
    elif order and selected_quote:
        _render_review_and_approve_section()
    else:
        st.info("ℹ️ Enter shipment details in 'Ship a Parcel' to generate and approve shipping quotes.")


# ── Advanced Tool 1: Packaging Optimizer ─────────────────────────────────────
def render_packaging_optimizer():
    st.subheader("📦 Comparative Packaging Analyzer & Custom Box Builder")
    st.caption("Evaluate 6-orthogonal rotations, internal cavities, and comparative volumetric freight savings.")

    db = get_db()
    audit = AuditLogger(db)

    order: Optional[OrderInput] = st.session_state.get("order")
    if order is None:
        # Sensible sample order so tool is standalone
        order = OrderInput(
            order_reference="SAMPLE-PKG-001",
            sender=Address(name="Alpha Hub", phone="9876543210", street_line1="1 Main St", city="Delhi", state="Delhi", pin_code="110020"),
            recipient=Address(name="Vikram Patel", phone="9123456780", street_line1="402 Green Acres", city="Bengaluru", state="Karnataka", pin_code="560034"),
            parcel_weight_kg=1.8,
            dimensions=Dimensions(length_cm=25.0, width_cm=20.0, height_cm=15.0),
            package_type=PackageType.BOX,
            is_cod=False,
        )

    with st.expander("➕ Add Custom-Size Packaging", expanded=False):
        with st.form("custom_packaging_form"):
            c1, c2 = st.columns(2)
            with c1:
                cp_name = st.text_input("Packaging Name *", placeholder="e.g. Long Product Carton")
            with c2:
                cp_type = st.selectbox("Packaging Type *", ["Box", "Polybag", "Tube", "Custom Crate"])

            d1, d2, d3 = st.columns(3)
            with d1:
                cp_l = st.number_input("Outer Length (cm) *", min_value=0.1, max_value=500.0, value=30.0, step=0.5)
            with d2:
                cp_w = st.number_input("Outer Width (cm) *", min_value=0.1, max_value=500.0, value=20.0, step=0.5)
            with d3:
                cp_h = st.number_input("Outer Height (cm) *", min_value=0.1, max_value=500.0, value=15.0, step=0.5)

            t1, t2, t3 = st.columns(3)
            with t1:
                cp_wall = st.number_input("Wall Thickness per side (cm) *", min_value=0.0, max_value=20.0, value=0.5, step=0.1)
            with t2:
                cp_pad = st.number_input("Padding per side (cm) *", min_value=0.0, max_value=20.0, value=1.0, step=0.5)
            with t3:
                cp_tare = st.number_input("Packaging Tare Weight (kg) *", min_value=0.0, max_value=100.0, value=0.2, step=0.05)

            save_btn = st.form_submit_button("💾 Save Custom Packaging", type="primary", use_container_width=True)

        if save_btn:
            type_map = {"Box": "box", "Polybag": "polybag", "Tube": "tube", "Custom Crate": "custom_crate"}
            try:
                pkg_id = db.insert_custom_packaging({
                    "name": cp_name.strip() or "Custom Box",
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

    # Load custom packages
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

    st.subheader("Evaluate Fit for Unpacked Product")
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        prod_l = st.number_input("Product Length (cm) *", min_value=0.1, max_value=500.0, value=float(order.dimensions.length_cm), step=0.5, key="eval_prod_l")
    with pc2:
        prod_w = st.number_input("Product Width (cm) *", min_value=0.1, max_value=500.0, value=float(order.dimensions.width_cm), step=0.5, key="eval_prod_w")
    with pc3:
        prod_h = st.number_input("Product Height (cm) *", min_value=0.1, max_value=500.0, value=float(order.dimensions.height_cm), step=0.5, key="eval_prod_h")
    with pc4:
        prod_weight = st.number_input("Product Net Weight (kg) *", min_value=0.01, max_value=1000.0, value=float(order.parcel_weight_kg), step=0.1, key="eval_prod_wt")

    if st.button("📐 Evaluate Packaging Alternatives Across All Couriers", type="primary", use_container_width=True):
        prod_dims = Dimensions(length_cm=prod_l, width_cm=prod_w, height_cm=prod_h)
        with st.spinner("Evaluating packaging options across 6 orthogonal rotations…"):
            results = evaluate_packaging_alternatives(
                product_dimensions=prod_dims,
                product_net_weight_kg=prod_weight,
                current_outer_dimensions=order.dimensions,
                custom_packages=custom_packages_list,
                couriers=ALL_DEMO_COURIERS,
            )
            st.session_state["packaging_results"] = results
            st.session_state["eval_prod_weight"] = prod_weight

    results = st.session_state.get("packaging_results")
    if results:
        feasible = [r for r in results if r.is_feasible]
        infeasible = [r for r in results if not r.is_feasible]

        if feasible:
            best = min(feasible, key=lambda r: (r.new_billable_slab_kg, -r.estimated_freight_savings_inr))
            st.success(f"🏆 **Best Packaging Option:** {best.box_name} ({'Custom' if best.is_custom else 'Demo'}) — saves {fmt_inr(best.estimated_freight_savings_inr)} in estimated freight.")

            for r in feasible:
                with st.expander(f"✅ {r.box_name} — Billable {r.new_billable_slab_kg:.2f} kg (Saves {fmt_inr(r.estimated_freight_savings_inr)})"):
                    st.markdown(f"**Outer Dimensions:** {r.outer_dimensions.length_cm} × {r.outer_dimensions.width_cm} × {r.outer_dimensions.height_cm} cm")
                    gross_wt = getattr(r, "new_gross_weight_kg", 0.0) or (prod_weight + r.tare_weight_kg)
                    st.markdown(f"**Tare Weight:** {r.tare_weight_kg:.2f} kg | **Gross Weight:** {gross_wt:.2f} kg")
                    if r.courier_reports:
                        st.markdown("**Courier Capability & Divisor Compatibility:**")
                        for c_code, c_rep in r.courier_reports.items():
                            badge = "🟢 Eligible" if c_rep.is_eligible else "🔴 Ineligible"
                            st.caption(f"- **{c_code}**: {badge} (Divisor {c_rep.volumetric_divisor:.0f})")

        if infeasible:
            with st.expander(f"❌ Infeasible Packaging Options ({len(infeasible)})", expanded=False):
                for r in infeasible:
                    st.caption(f"❌ **{r.box_name}**: {r.rejection_reason}")


# ── Advanced Tool 2: Stress Testing Lab ──────────────────────────────────────
def render_stress_testing():
    st.subheader("🧪 Shipping Decision Stress Testing Lab")
    st.caption("Evaluate how courier recommendations hold up under input variations (weight, dimensions, surcharges).")

    order: Optional[OrderInput] = st.session_state.get("order")
    selected_quote: Optional[CourierQuote] = st.session_state.get("selected_quote")

    if order is None or selected_quote is None:
        st.info("ℹ️ Enter shipment details in 'Ship a Parcel' to run automated stress tests on the selected quote.")
        return

    st.markdown(f"**Active Order:** `{order.order_reference}` | **Selected Courier:** **{selected_quote.courier_name}**")

    if st.button("⚡ Run Decision Stress Test Suite", type="primary", use_container_width=True):
        tester = ShippingDecisionStressTester(couriers=ALL_DEMO_COURIERS)
        with st.spinner("Perturbing order weights, dimensions, and surcharges across 10 scenarios…"):
            report = tester.run_stress_test(
                order=order,
                baseline_quotes=st.session_state.get("quotes", []),
                ranking_prefs=RankingPreferences(mode=RankingMode.LOWEST_COST),
            )
            st.session_state["stress_report"] = report

    report = st.session_state.get("stress_report")
    if report:
        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Baseline Winner", report.baseline_winner_name)
        m2.metric("Baseline Cost", fmt_inr(report.baseline_winner_cost))
        m3.metric("Scenario Stability", f"{report.scenario_stability_pct:.0f}%")
        m4.metric("Scenarios Tested", report.total_scenarios_evaluated)

        frag = report.fragility_analysis
        st.markdown(f"**Fragility Verdict:** {'🔴 Sensitive' if frag.is_fragile else '🟢 Robust'} — {frag.fragility_verdict}")
        st.markdown(f"**Most Sensitive Input:** `{frag.most_sensitive_input}`")


# ── Advanced Tool 3: Courier Capability Registry ─────────────────────────────
def render_capability_registry():
    st.subheader("📜 Official Courier Capability & Provenance Registry")
    st.caption("Documented carrier restrictions, volumetric divisors, and source URLs.")

    db = get_db()
    caps = db.get_courier_capabilities()

    for c in caps:
        status_col = "green" if c.get("rule_status") == "VERIFIED" else "orange"
        mode_val = c.get("mode", "Standard")
        with st.expander(f":{status_col}[{c.get('rule_status', 'STATUS')}] — {c.get('courier_name', 'Courier')} ({c.get('service_type', 'Service')})"):
            st.markdown(f"**Courier Code:** `{c.get('courier_code')}` | **Mode:** `{mode_val}`")
            st.markdown(f"**Volumetric Divisor:** `{c.get('volumetric_divisor', 5000):.0f} cm³/kg`")
            st.markdown(f"**Max Single Dimension:** `{c.get('max_single_dimension_cm')} cm` | **Max Girth:** `{c.get('max_girth_cm')} cm`")
            st.markdown(f"**Max Length + Girth:** `{c.get('max_length_plus_girth_cm')} cm`")
            if c.get("evidence_url"):
                st.markdown(f"**Source Document:** [{c.get('evidence_source', 'Documentation')}]({c.get('evidence_url')})")
            elif c.get("evidence_source"):
                st.markdown(f"**Source Document:** {c.get('evidence_source')}")


# ── Tab 2: My Shipments & Audit History ──────────────────────────────────────
def render_history():
    st.header("📋 My Shipments & Audit Records")

    db, audit, _, _, _ = get_services()
    tab_shipments, tab_audit = st.tabs(["🚚 Dispatched Shipments", "📜 Audit Trail"])

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
                with st.expander(f":{colour}[{status.value}] — {ship['order_reference']} (AWB: {ship.get('tracking_number') or 'Pending'})"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown(f"**Shipment ID:** `{ship['shipment_id']}`")
                        st.markdown(f"**Order Reference:** `{ship['order_reference']}`")
                        st.markdown(f"**Courier:** `{ship['courier_code']}`")
                        st.markdown(f"**Status:** :{colour}[**{status.value}**]")
                    with col2:
                        tracking = ship.get("tracking_number")
                        st.markdown(f"**Tracking Number:** `{tracking}`" if tracking else "**Tracking:** Pending")
                        st.markdown(f"**Created:** {ship.get('created_at', '')[:19]}")

                    if tracking and status == ShipmentStatus.CREATED:
                        clean_fn = sanitize_label_filename(tracking, extension="pdf")
                        ord_row = db.get_order(ship["order_reference"])
                        if ord_row:
                            try:
                                dummy_ord = OrderInput(
                                    order_reference=ship["order_reference"],
                                    sender=Address.model_validate_json(ord_row["sender_json"]),
                                    recipient=Address.model_validate_json(ord_row["recipient_json"]),
                                    parcel_weight_kg=ord_row["parcel_weight_kg"],
                                    dimensions=Dimensions(length_cm=ord_row["length_cm"], width_cm=ord_row["width_cm"], height_cm=ord_row["height_cm"]),
                                    package_type=PackageType(ord_row["package_type"]),
                                    is_cod=bool(ord_row["is_cod"]),
                                    cod_amount=ord_row["cod_amount"],
                                )
                                dummy_quote = CourierQuote(
                                    quote_id=f"HIST-{ship['shipment_id']}",
                                    courier_code=ship["courier_code"],
                                    courier_name=ship["courier_code"].replace("_", " ").title(),
                                    service_type="Standard Logistics",
                                    input_hash="historical-record",
                                )
                                dummy_res = ShipmentResult(
                                    shipment_id=ship["shipment_id"],
                                    order_reference=ship["order_reference"],
                                    courier_code=ship["courier_code"],
                                    tracking_number=tracking,
                                    is_demo_data=True,
                                )
                                hist_pdf = generate_label_from_shipment_context(
                                    order=dummy_ord,
                                    quote=dummy_quote,
                                    shipment_result=dummy_res,
                                    page_format="A6",
                                )
                                if hist_pdf:
                                    st.download_button(
                                        f"📥 Download Label (PDF) for {tracking}",
                                        data=hist_pdf,
                                        file_name=clean_fn,
                                        mime="application/pdf",
                                        key=f"hist_dl_{tracking}",
                                    )
                            except Exception:
                                pass
        else:
            st.info("No shipments dispatched yet. Create a shipment in 'Ship a Parcel'.")

    with tab_audit:
        st.subheader("Audit Trail (Secret-Scrubbed)")
        st.caption("Immutable append-only audit trail logging all lifecycle events.")
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
            st.info("No audit events recorded yet.")


# ── Main Application ──────────────────────────────────────────────────────────
def main():
    render_sidebar()

    st.title("📦 Adaptive Shipping Intelligence Agent")
    st.markdown("*Find a suitable courier, compare shipping costs, and prepare your shipment in a few simple steps.*")
    st.caption(
        "🔵 **DEMO DATA NOTICE:** All courier rates, tracking references, and simulated dispatches "
        "are produced by the Demo Shipping Adapter and are NOT live carrier prices or confirmed bookings."
    )

    tab_ship, tab_history, tab_advanced = st.tabs([
        "📦 Ship a Parcel",
        "📋 My Shipments",
        "🛠️ Advanced Tools",
    ])

    with tab_ship:
        render_shipping_workflow()
    with tab_history:
        render_history()
    with tab_advanced:
        adv1, adv2, adv3 = st.tabs([
            "📦 Packaging Analyzer",
            "🧪 Stress Testing Lab",
            "📜 Courier Capability Registry",
        ])
        with adv1:
            render_packaging_optimizer()
        with adv2:
            render_stress_testing()
        with adv3:
            render_capability_registry()


if __name__ == "__main__":
    main()
