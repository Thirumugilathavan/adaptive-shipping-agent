"""
Adaptive Shipping Intelligence Agent — Streamlit Web Interface.

Primary Goal:
Simple, intelligent, user-friendly shipping experience.
Users enter only necessary shipping details. The system performs packaging analysis,
courier eligibility checks, volumetric-weight calculations, rate comparison,
ranking, uncertainty analysis, approval gating, and audit verification internally.

Tabs:
  1.  Ship a Parcel (Unified 5-Step Guided Shipping Workflow)
  2.  My Shipments (Track shipments, view details, download labels & audit trail)
  3.  Advanced Tools (Packaging Analyzer, Stress Testing Lab, Capability Registry)
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
from src.domain.advisor import (
    AdvisorRecommendationResult,
    CourierComplianceCategory,
    CourierPreferenceCompliance,
    DeliveryDeadlineOption,
    UserShippingPreferences,
    build_advisor_recommendation,
)
from src.domain.evaluator import EvaluatorSuite, SCENARIO_METADATA, ScenarioStatus
from src.domain.official_scenarios import OfficialScenariosRunner, OfficialTestCaseResult
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
    generate_label_preview_from_shipment_context,
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
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Clean White & Deep Dark Blue Palette (Accessible, High-Contrast) ─────────
st.markdown("""
<style>
    /* White background & dark typography */
    .stApp {
        background-color: #F8FAFC !important;
        color: #0F172A !important;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }
    
    /* Top Header Bar */
    header[data-testid="stHeader"] {
        background-color: #0F172A !important;
    }
    
    /* Headings */
    h1, h2, h3, h4, h5, h6 {
        color: #0F172A !important;
        font-weight: 700 !important;
    }
    
    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #FFFFFF !important;
        border-right: 1px solid #E2E8F0 !important;
    }
    
    /* Primary Buttons: Deep Dark Blue / Navy */
    button[kind="primary"], .stButton > button[kind="primary"] {
        background-color: #1E3A8A !important;
        color: #FFFFFF !important;
        border: 1px solid #1E3A8A !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
    }
    button[kind="primary"]:hover, .stButton > button[kind="primary"]:hover {
        background-color: #0F172A !important;
        border-color: #0F172A !important;
        color: #FFFFFF !important;
    }
    
    /* Secondary Buttons */
    button[kind="secondary"], .stButton > button[kind="secondary"] {
        background-color: #FFFFFF !important;
        color: #1E3A8A !important;
        border: 1.5px solid #1E3A8A !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
    }
    button[kind="secondary"]:hover, .stButton > button[kind="secondary"]:hover {
        background-color: #EFF6FF !important;
        color: #0F172A !important;
    }
    
    /* Form inputs */
    input, select, textarea, div[data-baseweb="input"], div[data-baseweb="select"] {
        background-color: #FFFFFF !important;
        border-color: #CBD5E1 !important;
        color: #0F172A !important;
        border-radius: 6px !important;
    }
    
    /* Expanders & Metrics */
    div[data-testid="stExpander"] {
        background-color: #FFFFFF !important;
        border: 1px solid #E2E8F0 !important;
        border-radius: 8px !important;
    }
    div[data-testid="stMetric"] {
        background-color: #FFFFFF !important;
        border: 1px solid #E2E8F0 !important;
        border-radius: 8px !important;
        padding: 12px !important;
    }
    div[data-testid="stMetricLabel"] {
        color: #475569 !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] {
        color: #1E3A8A !important;
        font-weight: 700 !important;
    }
</style>
""", unsafe_allow_html=True)

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
        "scenario_test_results": {},
        "official_test_results": {},
        "active_provider_mode": "demo",
        "shipengine_connection_status": None,
        "pref_flexible_budget": True,
        "pref_max_budget": 500.0,
        "pref_delivery_deadline": "Flexible delivery time",
        "advisor_result": None,
        "primary_recommended_quote": None,
        "user_confirmed_preference_tradeoff": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_session()


# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.title("Adaptive Shipping\nIntelligence Agent")
        st.caption("Find a suitable courier, compare shipping costs, and prepare your shipment in a few simple steps.")
        st.divider()
        st.markdown("""
**Guided Shipping Journey**
1.  **Enter Details** — Pickup PIN, Delivery PIN, Weight
2.  **Compare Couriers** — Eligible rates & recommendations
3. [PASS]  **Review & Approve** — 1-click verified approval
4.  **Get PDF Label** — Print-ready label with barcode & QR
""")
        st.divider()

        # ── Shipping Provider Mode Selector ──
        st.markdown("** Shipping Provider**")
        provider_mode_options = ["Demo Simulator", "ShipEngine Sandbox API"]
        curr_provider_mode = st.session_state.get("active_provider_mode", "demo")
        sel_idx = 1 if curr_provider_mode in ("shipengine", "shipengine_sandbox") else 0
        provider_choice = st.radio(
            "Select Provider",
            provider_mode_options,
            index=sel_idx,
            key="provider_mode_radio",
            label_visibility="collapsed",
        )
        active_mode = "shipengine" if "ShipEngine" in provider_choice else "demo"
        if active_mode != st.session_state.get("active_provider_mode"):
            st.session_state["active_provider_mode"] = active_mode
            st.session_state["quotes"] = None
            st.session_state["ranking_result"] = None
            st.session_state["selected_quote"] = None
            st.session_state["approval_given"] = False
            st.session_state["approved_record"] = None
            st.session_state["approved_proposal_sig"] = None
            st.session_state["dispatch_result"] = None
            st.rerun()

        if active_mode == "demo":
            st.warning(
                "**DEMO DATA MODE**\n\nAll courier rates shown are "
                "synthetic and produced by the Demo Shipping Adapter. "
                "They are NOT live carrier prices.",
                icon=None,
            )
            st.caption("Provider: Demo Multi-Courier Adapter (Simulated)")
        else:
            from src.providers.shipengine_adapter import (
                get_shipengine_api_key,
                mask_api_key,
                validate_sandbox_key,
                ShipEngineAdapter,
            )
            raw_key = get_shipengine_api_key()
            is_valid_key, key_msg = validate_sandbox_key(raw_key)

            st.info(
                "**SHIPENGINE SANDBOX**\n\nCommunicating with official "
                "ShipEngine Sandbox REST API (`https://api.shipengine.com`).",
                icon=None,
            )
            st.caption(f"Configured Key: `{mask_api_key(raw_key)}`")

            if st.button("Test Connection", key="btn_test_shipengine", use_container_width=True):
                with st.spinner("Pinging ShipEngine Sandbox API (/v1/carriers)..."):
                    se_adapter = ShipEngineAdapter(api_key=raw_key)
                    res = se_adapter.test_connection()
                    st.session_state["shipengine_connection_status"] = res

            conn_res = st.session_state.get("shipengine_connection_status")
            if conn_res:
                if conn_res.get("connected"):
                    st.success(f"[PASS]  Connected (HTTP {conn_res.get('status_code', 200)})")
                    carrier_list = [
                        c.get("friendly_name") or c.get("nickname") or c.get("carrier_code")
                        for c in conn_res.get("carriers", [])
                    ]
                    if carrier_list:
                        st.caption(f"Carriers: {', '.join(carrier_list)}")
                else:
                    st.error(f"[FAIL]  {conn_res.get('message')}")
            elif not is_valid_key:
                st.caption(f"[NOTICE]  {key_msg}")

        st.divider()
        st.markdown("** Evaluator 1-Click Scenarios**")
        scenario_options = [
            "None (Manual Input)",
            "Demo A: Normal Parcel (Standard comparison & booking)",
            "Demo B: Oversized Elongated Item (110cm custom box)",
            "Demo C: Cheapest Option Rejected (Re-choice)",
            "Demo D: Ambiguous Booking & Reconciliation",
            "Demo E: Official TC1 (Same Parcel on 3 Couriers)",
            "Demo F: Official TC2 (Large, Light Box)",
            "Demo G: Official TC3 (Destination PIN Not Serviceable)",
            "Demo H: Official TC4 (Label Created & AWB Verification)",
            "Demo I: Mandatory Measurements & COD Validation",
            "Demo J: No Eligible Couriers, API Failure & Stale Results",
        ]

        def _map_demo_choice_to_id(choice: str) -> Optional[str]:
            if "Demo A" in choice: return "DEMO_A"
            if "Demo B" in choice: return "DEMO_B"
            if "Demo C" in choice: return "DEMO_C"
            if "Demo D" in choice: return "DEMO_D"
            if "Demo E" in choice or "TC1" in choice: return "DEMO_E"
            if "Demo F" in choice or "TC2" in choice: return "DEMO_F"
            if "Demo G" in choice or "TC3" in choice: return "DEMO_G"
            if "Demo H" in choice or "TC4" in choice: return "DEMO_H"
            if "Demo I" in choice: return "DEMO_I"
            if "Demo J" in choice: return "DEMO_J"
            return None

        # Determine index to preserve selection across reruns
        current_active = st.session_state.get("active_demo_scenario", "None (Manual Input)")
        def_idx = 0
        for i, opt in enumerate(scenario_options):
            if current_active and (current_active in opt or opt in current_active):
                def_idx = i
                break

        demo_choice = st.selectbox(
            "Select Judge Demo Scenario",
            scenario_options,
            index=def_idx,
            key="demo_scenario_choice",
        )

        sc_id = _map_demo_choice_to_id(demo_choice)
        if sc_id and sc_id in SCENARIO_METADATA:
            meta = SCENARIO_METADATA[sc_id]
            with st.container(border=True):
                st.markdown(f"** {meta['full_title']}**")
                st.caption(meta["description"])
                st.markdown(f"**Expected:** {meta['expected']}")

                # Check status of this scenario
                sc_results = st.session_state.get("scenario_test_results", {})
                test_res = sc_results.get(sc_id)
                if test_res:
                    if test_res.status == ScenarioStatus.PASSED:
                        st.success(f"[PASS]  **Status: {test_res.status.value}**")
                    elif test_res.status == ScenarioStatus.FAILED:
                        st.error(f"[FAIL]  **Status: {test_res.status.value}**")
                    elif test_res.status == ScenarioStatus.ERROR:
                        st.error(f"[NOTICE]  **Status: {test_res.status.value}**")
                    else:
                        st.info(f"[NOT RUN]  **Status: {test_res.status.value}**")

                    st.caption(f"**Actual:** {test_res.actual_outcome}")
                    with st.expander("Inspect Test Evidence & Subcases", expanded=False):
                        if test_res.subcases:
                            for sc in test_res.subcases:
                                icon = "[PASS] " if sc.passed else "[FAIL] "
                                st.markdown(f"**{icon} {sc.subcase_id} — {sc.title}**")
                                st.caption(f"Actual: {sc.actual}")
                        st.caption(f"Executed in {test_res.execution_time_ms} ms at {test_res.timestamp[:19]}Z")
                else:
                    st.info("[NOT RUN]  **Status: NOT RUN**")

                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button("Load", use_container_width=True, help="Load this scenario inputs into the application form", key=f"btn_load_{sc_id.lower()}"):
                        order_demo = meta["builder"]()
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
                            "active_provider_mode": "demo",
                            "dispatch_result": None,
                            "recon_result": None,
                            "approval_given": False,
                            "approved_record": None,
                            "approved_proposal_sig": None,
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
                            "form_pay_mode": "Cash on Delivery (COD)" if order_demo.is_cod else "Prepaid",
                            "_cod_amount": order_demo.cod_amount if order_demo.is_cod else 1200.0,
                            "form_cod_amount": order_demo.cod_amount if order_demo.is_cod else 1200.0,
                            "_last_seen_measurements": (
                                order_demo.dimensions.length_cm,
                                order_demo.dimensions.width_cm,
                                order_demo.dimensions.height_cm,
                                order_demo.parcel_weight_kg,
                            ),
                        })
                        st.rerun()

                with btn_col2:
                    if st.button("Run Test", use_container_width=True, type="primary", help="Execute the deterministic test", key=f"btn_run_{sc_id.lower()}"):
                        suite = EvaluatorSuite(get_db())
                        res = suite.run_scenario(sc_id)
                        if "scenario_test_results" not in st.session_state:
                            st.session_state["scenario_test_results"] = {}
                        st.session_state["scenario_test_results"][sc_id] = res
                        st.rerun()

                if st.button("Reset Scenario", use_container_width=True, help="Reset evaluator state for this scenario", key=f"btn_reset_{sc_id.lower()}"):
                    if "scenario_test_results" in st.session_state and sc_id in st.session_state["scenario_test_results"]:
                        del st.session_state["scenario_test_results"][sc_id]
                    st.session_state["active_demo_scenario"] = "None (Manual Input)"
                    st.rerun()
        else:
            st.info("Manual Input Mode: Fill in parcel details and find couriers below.")

        st.divider()
        if st.button("Reset Application", use_container_width=True):
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
                st.caption(f":red[[NOTICE]  {err_p_fmt}]")

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
                st.caption(f":red[[NOTICE]  {err_d_fmt}]")

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
        st.markdown("####  Mandatory Parcel Measurements")
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
                        f" **Recommended Packaging: {best_opt.box_name}**  \n"
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
                    st.warning("[NOTICE]  No standard catalog box fits this unpacked item. Outer product dimensions used as fallback.")
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
            st.info("[NOTICE]  Parcel dimensions or weight were changed. Previous quotes and recommendations cleared. Please click 'Find & Compare Couriers' to re-rate.")
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

        st.markdown("---")
        st.markdown("####  Shipping Preferences (Budget & Delivery Target)")
        st.caption("Tell us your budget limit and delivery deadline so our Smart Shipping Advisor can evaluate trade-offs and recommend the best courier.")
        pref_col1, pref_col2 = st.columns(2)
        with pref_col1:
            st.markdown("**Maximum Shipping Budget**")
            is_flex_b = st.checkbox(
                "Flexible budget (find lowest available price)",
                value=st.session_state.get("pref_flexible_budget", True),
                key="chk_flex_budget",
            )
            st.session_state["pref_flexible_budget"] = is_flex_b
            if not is_flex_b:
                b_val = st.number_input(
                    "Maximum preferred shipping cost (₹) *",
                    min_value=10.0,
                    max_value=100000.0,
                    value=float(st.session_state.get("pref_max_budget", 500.0)),
                    step=25.0,
                    format="%.2f",
                    key="input_max_budget",
                    help="Maximum landed shipping cost you are willing to pay for this shipment.",
                )
                st.session_state["pref_max_budget"] = float(b_val)
            else:
                st.caption("ℹ Flexible budget: couriers will be evaluated to find the best available price.")

        with pref_col2:
            st.markdown("**Delivery Deadline**")
            deadline_opts = [
                "Flexible delivery time",
                "Within 1 day",
                "Within 2 days",
                "Within 3 days",
            ]
            curr_deadline = st.session_state.get("pref_delivery_deadline", "Flexible delivery time")
            def_d_idx = deadline_opts.index(curr_deadline) if curr_deadline in deadline_opts else 0
            chosen_deadline = st.selectbox(
                "How soon should the parcel arrive?",
                options=deadline_opts,
                index=def_d_idx,
                key="select_delivery_deadline",
                help="Target maximum transit time from carrier pickup to final delivery.",
            )
            st.session_state["pref_delivery_deadline"] = chosen_deadline
            if chosen_deadline == "Flexible delivery time":
                st.caption("ℹ Flexible delivery: standard and economy transit options are included.")
            else:
                st.caption(f"We'll prioritize couriers delivering {chosen_deadline.lower()}.")

        # ── Address & Reference Details (Sensible Defaults) ──────────────────
        with st.expander("Sender & Recipient Contact Information (Optional)", expanded=False):
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

        submit_btn = st.button("Find & Compare Couriers", type="primary", use_container_width=True)

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
                st.error(f"[FAIL]  {e}")
            st.session_state["quotes"] = None
            st.session_state["selected_quote"] = None
            st.session_state["approval_given"] = False
            return

        provider_mode = st.session_state.get("active_provider_mode", "demo")
        provider = get_provider(provider_mode)
        svc_ok, svc_msg, is_demo = provider.check_route_serviceability(pickup_pin, delivery_pin)
        if not svc_ok:
            st.error(f"[FAIL]  {svc_msg}")
            if provider_mode == "demo":
                st.caption("ℹ Demo Network Data: Route serviceability verified against Demo Shipping Adapter simulated network data (Non-official carrier verification).")
            else:
                st.caption(f"ℹ {provider.provider_name}: Route serviceability check failed.")
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

        with st.spinner(f"Finding eligible couriers and calculating rates via {provider.provider_name}…"):
            try:
                quotes = provider.get_rates(order)
            except Exception as e:
                st.error(f"[FAIL]  Rate fetch error from {provider.provider_name}: {e}")
                st.session_state["quotes"] = None
                st.session_state["selected_quote"] = None
                st.session_state["approval_given"] = False
                return

            eligible = [q for q in quotes if q.is_eligible and q.cost_breakdown]
            if not eligible:
                if provider_mode == "demo":
                    st.error("[FAIL]  Delivery to this PIN code is currently unavailable. Please check the PIN code or try another supported destination.")
                    st.caption("ℹ Demo Network Data: No couriers in the simulated network can service this parcel to destination PIN.")
                else:
                    unsvc_reasons = [q.unserviceable_reason for q in quotes if q.unserviceable_reason]
                    reason_msg = unsvc_reasons[0] if unsvc_reasons else "ShipEngine Sandbox returned no eligible rates for this route and weight."
                    st.error(f"[FAIL]  {reason_msg}")
                    st.caption("ℹ ShipEngine Sandbox API: Zero eligible rates returned by connected carriers. No simulated fallback applied.")
                st.session_state["quotes"] = quotes
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
                    provider.provider_code,
                    {"quote_count": len(quotes), "order_reference": order.order_reference, "provider": provider.provider_name},
                    order_reference=order.order_reference,
                )
            except Exception:
                pass

            # Ranking & Smart Personalized Advisor
            user_prefs = UserShippingPreferences.create(
                max_budget=st.session_state.get("pref_max_budget") if not st.session_state.get("pref_flexible_budget", True) else None,
                is_flexible_budget=st.session_state.get("pref_flexible_budget", True),
                deadline_choice=st.session_state.get("pref_delivery_deadline", "Flexible delivery time"),
            )
            advisor_res = build_advisor_recommendation(quotes, user_prefs)
            st.session_state["advisor_result"] = advisor_res

            prefs = RankingPreferences(mode=RankingMode.LOWEST_COST)
            ranking_result = rank_couriers(quotes, prefs)
            st.session_state["ranking_result"] = ranking_result

            primary_q = advisor_res.primary_recommendation.quote if advisor_res.primary_recommendation else (
                ranking_result.selected_winner.quote if ranking_result.selected_winner else None
            )
            st.session_state["primary_recommended_quote"] = primary_q
            st.session_state["selected_quote"] = primary_q
            st.session_state["user_confirmed_preference_tradeoff"] = False

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

        st.success(f"[PASS]  Found eligible courier options for **PIN {pickup_pin} -> PIN {delivery_pin}** via {provider.provider_name}.")
        if provider_mode == "demo":
            st.caption("ℹ Route serviceability verified against Demo Shipping Adapter simulated network data (Simulated Demo Data — not official carrier confirmation).")
        else:
            st.caption("ℹ Genuine rates and route confirmation retrieved directly from ShipEngine Sandbox REST API.")
        st.session_state["wizard_step"] = 2
        st.rerun()


# ── Step 2: Smart Personalized Courier Recommendations & Trade-Off Advisor ────
def _render_rate_comparison():
    order: Optional[OrderInput] = st.session_state.get("order")
    quotes = st.session_state.get("quotes") or []

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
        st.error("[FAIL]  Valid parcel dimensions and actual gross weight (> 0) are required to compare courier rates.")
        return

    st.divider()
    st.subheader("2. Smart Courier Recommendation & Alternatives")

    # Retrieve or build Smart Advisor Recommendation
    advisor_result: Optional[AdvisorRecommendationResult] = st.session_state.get("advisor_result")
    if not advisor_result or not advisor_result.all_evaluated:
        user_prefs = UserShippingPreferences.create(
            max_budget=st.session_state.get("pref_max_budget") if not st.session_state.get("pref_flexible_budget", True) else None,
            is_flexible_budget=st.session_state.get("pref_flexible_budget", True),
            deadline_choice=st.session_state.get("pref_delivery_deadline", "Flexible delivery time"),
        )
        advisor_result = build_advisor_recommendation(quotes, user_prefs)
        st.session_state["advisor_result"] = advisor_result

    # Determine primary recommended quote and active selected quote
    primary_quote = st.session_state.get("primary_recommended_quote")
    if not primary_quote and advisor_result.primary_recommendation:
        primary_quote = advisor_result.primary_recommendation.quote
        st.session_state["primary_recommended_quote"] = primary_quote

    selected_quote = st.session_state.get("selected_quote")
    if not selected_quote:
        selected_quote = primary_quote
        st.session_state["selected_quote"] = selected_quote

    # ── Preference Indicator & Live Tweak Bar ──
    prefs = advisor_result.preferences
    b_desc = f"Max {fmt_inr(prefs.max_budget)}" if not prefs.is_flexible_budget and prefs.max_budget else "Flexible Budget"
    d_desc = f"Within {prefs.max_delivery_days} day(s)" if not prefs.is_flexible_deadline and prefs.max_delivery_days else "Flexible Delivery Time"

    col_pref_info, _ = st.columns([3, 1])
    with col_pref_info:
        st.caption(f" **Active Shipping Preferences:** {b_desc} | {d_desc}")

    with st.expander("Edit Shipping Preferences (Budget & Speed)", expanded=False):
        ec1, ec2, ec3 = st.columns([2, 2, 1])
        with ec1:
            tweak_flex_b = st.checkbox(
                "Flexible Budget",
                value=st.session_state.get("pref_flexible_budget", True),
                key="tweak_chk_flex_budget",
            )
            tweak_b_val = st.number_input(
                "Max Budget (₹)",
                min_value=10.0,
                max_value=100000.0,
                value=float(st.session_state.get("pref_max_budget", 500.0)),
                step=25.0,
                disabled=tweak_flex_b,
                key="tweak_input_budget",
            )
        with ec2:
            tweak_deadline_opts = [
                "Flexible delivery time",
                "Within 1 day",
                "Within 2 days",
                "Within 3 days",
            ]
            tweak_curr_d = st.session_state.get("pref_delivery_deadline", "Flexible delivery time")
            tweak_d_idx = tweak_deadline_opts.index(tweak_curr_d) if tweak_curr_d in tweak_deadline_opts else 0
            tweak_d_sel = st.selectbox(
                "Delivery Target",
                options=tweak_deadline_opts,
                index=tweak_d_idx,
                key="tweak_select_deadline",
            )
        with ec3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Apply Changes", key="btn_apply_pref_tweaks", use_container_width=True):
                st.session_state["pref_flexible_budget"] = tweak_flex_b
                st.session_state["pref_max_budget"] = float(tweak_b_val)
                st.session_state["pref_delivery_deadline"] = tweak_d_sel
                new_prefs = UserShippingPreferences.create(
                    max_budget=float(tweak_b_val) if not tweak_flex_b else None,
                    is_flexible_budget=tweak_flex_b,
                    deadline_choice=tweak_d_sel,
                )
                new_adv_res = build_advisor_recommendation(quotes, new_prefs)
                st.session_state["advisor_result"] = new_adv_res
                if new_adv_res.primary_recommendation:
                    st.session_state["primary_recommended_quote"] = new_adv_res.primary_recommendation.quote
                    st.session_state["selected_quote"] = new_adv_res.primary_recommendation.quote
                st.session_state["approval_given"] = False
                st.session_state["approved_record"] = None
                st.rerun()

    # ── Section 6: Dedicated "No exact match" experience ──
    if not advisor_result.is_exact_match:
        with st.container(border=True):
            st.warning("### [NOTICE]  No exact match for your preferences")
            st.markdown(
                f"We couldn't find a verified courier that meets both your preferred budget (**{b_desc}**) "
                f"and delivery deadline (**{d_desc}**). Here are the closest available alternatives based on trade-offs:"
            )

            # Quick action adjustment buttons
            act_col1, act_col2, act_col3, act_col4 = st.columns(4)
            with act_col1:
                if st.button("Add ₹50 Budget", use_container_width=True, key="btn_qa_add_50"):
                    curr_b = st.session_state.get("pref_max_budget", 500.0)
                    st.session_state["pref_max_budget"] = curr_b + 50.0
                    st.session_state["pref_flexible_budget"] = False
                    user_prefs = UserShippingPreferences.create(
                        max_budget=st.session_state["pref_max_budget"],
                        is_flexible_budget=False,
                        deadline_choice=st.session_state.get("pref_delivery_deadline", "Flexible delivery time"),
                    )
                    st.session_state["advisor_result"] = build_advisor_recommendation(quotes, user_prefs)
                    st.session_state["approval_given"] = False
                    st.rerun()
            with act_col2:
                if st.button("Add ₹100 Budget", use_container_width=True, key="btn_qa_add_100"):
                    curr_b = st.session_state.get("pref_max_budget", 500.0)
                    st.session_state["pref_max_budget"] = curr_b + 100.0
                    st.session_state["pref_flexible_budget"] = False
                    user_prefs = UserShippingPreferences.create(
                        max_budget=st.session_state["pref_max_budget"],
                        is_flexible_budget=False,
                        deadline_choice=st.session_state.get("pref_delivery_deadline", "Flexible delivery time"),
                    )
                    st.session_state["advisor_result"] = build_advisor_recommendation(quotes, user_prefs)
                    st.session_state["approval_given"] = False
                    st.rerun()
            with act_col3:
                if st.button("Allow 1 More Day", use_container_width=True, key="btn_qa_add_day"):
                    curr_d = DeliveryDeadlineOption.to_days(st.session_state.get("pref_delivery_deadline", "")) or 1
                    new_d = min(curr_d + 1, 3)
                    st.session_state["pref_delivery_deadline"] = DeliveryDeadlineOption.from_days(new_d)
                    user_prefs = UserShippingPreferences.create(
                        max_budget=st.session_state.get("pref_max_budget") if not st.session_state.get("pref_flexible_budget", True) else None,
                        is_flexible_budget=st.session_state.get("pref_flexible_budget", True),
                        deadline_choice=st.session_state["pref_delivery_deadline"],
                    )
                    st.session_state["advisor_result"] = build_advisor_recommendation(quotes, user_prefs)
                    st.session_state["approval_given"] = False
                    st.rerun()
            with act_col4:
                if st.button("Flexible Deadline", use_container_width=True, key="btn_qa_flex_deadline"):
                    st.session_state["pref_delivery_deadline"] = "Flexible delivery time"
                    user_prefs = UserShippingPreferences.create(
                        max_budget=st.session_state.get("pref_max_budget") if not st.session_state.get("pref_flexible_budget", True) else None,
                        is_flexible_budget=st.session_state.get("pref_flexible_budget", True),
                        deadline_choice="Flexible delivery time",
                    )
                    st.session_state["advisor_result"] = build_advisor_recommendation(quotes, user_prefs)
                    st.session_state["approval_given"] = False
                    st.rerun()

            st.markdown("---")
            # 3 Closest Match Cards
            c_cards = []
            if advisor_result.best_compromise:
                c_cards.append(("Best Compromise", advisor_result.best_compromise, "Optimal trade-off for your constraints"))
            if advisor_result.lowest_cost_alternative:
                c_cards.append(("Lowest Cost", advisor_result.lowest_cost_alternative, "Cheapest confirmed option"))
            if advisor_result.fastest_alternative:
                c_cards.append(("Fastest Delivery", advisor_result.fastest_alternative, "Quickest transit SLA"))

            # Deduplicate by courier_code while keeping distinct category badges
            seen_codes = set()
            unique_cards = []
            for item in c_cards:
                if item[1].courier_code not in seen_codes:
                    seen_codes.add(item[1].courier_code)
                    unique_cards.append(item)

            card_cols = st.columns(max(len(unique_cards), 1))
            for idx, (title, comp, tag_sub) in enumerate(unique_cards):
                with card_cols[idx]:
                    with st.container(border=True):
                        st.markdown(f"#### {title}")
                        st.caption(tag_sub)
                        st.markdown(f"**{comp.courier_name}**")
                        st.markdown(f"**Price:** {fmt_inr(comp.total_cost, currency=comp.currency)}")
                        sla_txt = f"{comp.estimated_delivery_days} business days" if comp.estimated_delivery_days else "Standard SLA"
                        st.markdown(f"**Transit:** {sla_txt}")

                        if comp.budget_overrun > 0:
                            st.caption(f":red[[NOTICE]  Exceeds budget by {comp.currency} {comp.budget_overrun:,.2f}]")
                        elif comp.meets_budget:
                            st.caption(f":green[[PASS]  Within budget (saves {comp.currency} {comp.budget_savings:,.2f})]")

                        if comp.delay_days > 0:
                            st.caption(f":orange[[NOTICE]  Misses deadline by {comp.delay_days} day(s)]")
                        elif comp.meets_deadline:
                            st.caption(f":green[[PASS]  Meets deadline]")

                        st.markdown(f"- [LIVE]  **Advantage:** {comp.main_advantage}")
                        st.markdown(f"- [PENDING]  **Disadvantage:** {comp.main_disadvantage}")
                        st.caption(comp.data_verification_text)

                        is_cur_sel = bool(selected_quote and selected_quote.quote_id == comp.quote.quote_id)
                        if is_cur_sel:
                            st.info("[PASS]  **Currently Selected**")
                        else:
                            if st.button("Choose this courier", key=f"btn_choose_cm_{comp.courier_code}_{idx}", use_container_width=True):
                                st.session_state["selected_quote"] = comp.quote
                                st.session_state["approval_given"] = False
                                st.session_state["approved_record"] = None
                                st.rerun()

    # ── "Our Recommendation"Section ──
    rec_comp = None
    if primary_quote:
        rec_comp = next((c for c in advisor_result.all_evaluated if c.courier_code == primary_quote.courier_code), None)

    active_comp = None
    if selected_quote:
        active_comp = next((c for c in advisor_result.all_evaluated if c.courier_code == selected_quote.courier_code), None)

    display_comp = active_comp or rec_comp

    if display_comp and display_comp.quote.cost_breakdown:
        is_user_switched = bool(primary_quote and selected_quote and primary_quote.quote_id != selected_quote.quote_id)

        with st.container(border=True):
            header_prefix = "Primary Recommendation" if not is_user_switched else "Selected Courier"
            st.markdown(f"### {header_prefix}: {display_comp.courier_name}")

            if is_user_switched and primary_quote:
                st.info(
                    f"ℹ **Alternative Selected:** You have selected `{display_comp.courier_name}`. "
                    f"Our primary recommendation based on your preferences was `{primary_quote.courier_name}`.",
                    icon=None,
                )
                if st.button("Revert to Recommended Courier", key="btn_revert_to_primary", use_container_width=True):
                    st.session_state["selected_quote"] = primary_quote
                    st.session_state["approval_given"] = False
                    st.session_state["approved_record"] = None
                    st.rerun()

            rec_col1, rec_col2 = st.columns([3, 1])
            with rec_col1:
                st.markdown(f"**Service:** {display_comp.service_type}")
                st.markdown(f"**Summary:** {advisor_result.summary_explanation if not is_user_switched else display_comp.plain_language_hint}")
                if advisor_result.trade_off_explanation and not is_user_switched:
                    st.caption(f" {advisor_result.trade_off_explanation}")
            with rec_col2:
                st.markdown(f"**Landed Cost:** **{fmt_inr(display_comp.total_cost, currency=display_comp.currency)}**")
                if display_comp.estimated_delivery_days:
                    st.caption(f"Est. Transit: {display_comp.estimated_delivery_days} business days")

            # Volumetric / Chargeable weight callout
            if display_comp.quote.weight_result:
                wb = display_comp.quote.weight_result
                if wb.is_volumetric_penalty:
                    st.warning(
                        f"[NOTICE]  **Volumetric Weight Penalty Applies:** Dimensional weight ({wb.volumetric_weight_kg:.2f} kg) "
                        f"exceeds actual gross weight ({wb.actual_weight_kg:.2f} kg). "
                        f"Billed at **{wb.chargeable_weight_kg:.2f} kg** (Carrier Divisor: {wb.divisor_used:,.0f}).",
                        icon=None,
                    )
                else:
                    st.info(
                        f" **Billed by Actual Gross Weight:** {wb.actual_weight_kg:.2f} kg "
                        f"(Volumetric weight is {wb.volumetric_weight_kg:.2f} kg, divisor {wb.divisor_used:,.0f}).",
                    )

            # Redesigned "Why this recommendation?"Section
            st.markdown("####  Why this courier?")
            st.markdown(f"-  **Selection Reason:** {advisor_result.summary_explanation if not is_user_switched else display_comp.plain_language_hint}")
            st.markdown(f"-  **Budget Analysis:** {display_comp.budget_status_text}")
            st.markdown(f"-  **Delivery Timeline:** {display_comp.deadline_status_text}")
            st.markdown(f"-  **Main Advantage:** {display_comp.main_advantage}")
            st.markdown(f"- [NOTICE]  **Key Limitation / Trade-off:** {display_comp.main_disadvantage}")
            st.markdown(f"-  **Data Verification:** {display_comp.data_verification_text}")

    # ── Compare Alternative Couriers Section ──
    eligible_comps = [c for c in advisor_result.all_evaluated if c.is_serviceable]
    if eligible_comps:
        st.markdown("---")
        st.markdown("####  Compare All Available Couriers")
        st.caption("Review differences in cost, speed, and preference alignment against the recommendation.")

        for comp in eligible_comps:
            is_active = bool(selected_quote and selected_quote.quote_id == comp.quote.quote_id)
            is_primary = bool(primary_quote and primary_quote.quote_id == comp.quote.quote_id)

            with st.container(border=True):
                col_left, col_right = st.columns([3, 1])
                with col_left:
                    # Courier title and badges
                    badge_items = []
                    if is_primary:
                        badge_items.append("Primary Recommendation")
                    if is_active:
                        badge_items.append("[PASS]  Active Selected")
                    for t in comp.tags:
                        if t == "LOWEST_COST":
                            badge_items.append("Lowest Cost")
                        elif t == "FASTEST_DELIVERY":
                            badge_items.append("Fastest Delivery")
                        elif t == "BEST_COMPROMISE":
                            badge_items.append("Best Compromise")

                    badge_str = f" `{'` `'.join(badge_items)}`" if badge_items else ""
                    st.markdown(f"**{comp.courier_name}** ({comp.service_type}){badge_str}")

                    # Plain-language hint
                    if comp.plain_language_hint:
                        st.markdown(f"_{comp.plain_language_hint}_")

                    # Cost & Days difference callout
                    diff_parts = []
                    if comp.cost_diff_vs_recommended is not None and not is_primary:
                        sign = "+" if comp.cost_diff_vs_recommended > 0 else ""
                        diff_parts.append(f"Price diff vs recommendation: **{sign}{fmt_inr(comp.cost_diff_vs_recommended, currency=comp.currency)}** ({sign}{comp.cost_pct_vs_recommended}%)")

                    if comp.days_diff_vs_recommended is not None and not is_primary:
                        if comp.days_diff_vs_recommended < 0:
                            diff_parts.append(f"Transit: **{abs(comp.days_diff_vs_recommended)} day(s) earlier**")
                        elif comp.days_diff_vs_recommended > 0:
                            diff_parts.append(f"Transit: **{comp.days_diff_vs_recommended} day(s) later**")
                        else:
                            diff_parts.append(f"Transit: **Same SLA**")

                    if diff_parts:
                        st.caption(" | ".join(diff_parts))

                    # Compliance pills
                    c_pills = []
                    if comp.meets_budget:
                        c_pills.append(f":green[[PASS]  Fits Budget ({comp.currency} {comp.total_cost:,.2f})]")
                    else:
                        c_pills.append(f":red[[NOTICE]  Exceeds Budget (+{comp.currency} {comp.budget_overrun:,.2f})]")

                    if comp.meets_deadline:
                        c_pills.append(f":green[[PASS]  Meets Deadline ({comp.estimated_delivery_days}d)]")
                    elif comp.delay_days > 0:
                        c_pills.append(f":orange[[NOTICE]  Late (+{comp.delay_days}d)]")
                    else:
                        c_pills.append(":gray[[NOT RUN]  SLA Unverified]")

                    st.markdown(" ".join(c_pills))

                    # Advantage / Disadvantage
                    st.markdown(f"- [LIVE]  **Advantage:** {comp.main_advantage}")
                    st.markdown(f"- [PENDING]  **Disadvantage:** {comp.main_disadvantage}")

                with col_right:
                    st.markdown(f"**{fmt_inr(comp.total_cost, currency=comp.currency)}**")
                    if comp.estimated_delivery_days:
                        st.caption(f"{comp.estimated_delivery_days} business days")
                    else:
                        st.caption("Standard SLA")

                    if is_active:
                        st.info("[PASS]  **Selected**")
                    else:
                        if st.button("Choose this courier", key=f"btn_choose_courier_{comp.courier_code}", use_container_width=True):
                            st.session_state["selected_quote"] = comp.quote
                            st.session_state["approval_given"] = False
                            st.session_state["approved_record"] = None
                            st.session_state["user_confirmed_preference_tradeoff"] = False
                            st.rerun()

    # Ineligible Couriers (Cleanly Hidden in Expander)
    ineligible = advisor_result.unserviceable
    if ineligible:
        with st.expander(f"[FAIL]  Unavailable Couriers ({len(ineligible)})", expanded=False):
            st.caption("These couriers cannot service this parcel due to regional limits or carrier constraints.")
            for q in ineligible:
                reason = q.unserviceable_reason or "Route or size restriction not supported."
                st.markdown(f"- **{q.courier_name}**: :red[Not available for this parcel] — _{reason}_")

    # ── Technical Details & Audit Expander ──
    with st.expander("Technical Details & Calculations", expanded=False):
        st.caption("Detailed engineering specifications, volumetric weights, and audit decision steps.")
        girth_val = compute_girth(order.dimensions)
        l_plus_g = compute_length_plus_girth(order.dimensions)
        st.markdown(
            f"**Parcel Metrics:** {order.dimensions.length_cm:.1f} × {order.dimensions.width_cm:.1f} × {order.dimensions.height_cm:.1f} cm | "
            f"Dead Weight: {order.parcel_weight_kg:.2f} kg | Volume: {order.dimensions.volume_cm3:,.0f} cm³ | "
            f"Girth: {girth_val:.1f} cm | Length + Girth: {l_plus_g:.1f} cm"
        )
        if selected_quote and selected_quote.weight_result:
            wb = format_weight_breakdown(selected_quote.weight_result)
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
            st.markdown(f"**Route:** PIN `{order.sender.pin_code}` -> PIN `{order.recipient.pin_code}`")
            st.markdown(f"**Recipient:** {order.recipient.name}, {order.recipient.city} ({order.recipient.state})")
            st.markdown(f"**Parcel Weight:** {order.parcel_weight_kg:.2f} kg ({order.dimensions.length_cm:.1f}×{order.dimensions.width_cm:.1f}×{order.dimensions.height_cm:.1f} cm)")
        with rc2:
            st.markdown(f"**Selected Courier:** **{selected_quote.courier_name}** ({selected_quote.service_type})")
            curr_str = getattr(selected_quote.cost_breakdown, "currency", "INR") if selected_quote.cost_breakdown else "INR"
            rate_mode_badge = "*(ShipEngine Sandbox)*" if not getattr(selected_quote, "is_demo_data", True) else "*(Simulated Demo Rate)*"
            st.markdown(f"**Total Shipping Cost:** **{fmt_inr(cost_val, currency=curr_str)}** {rate_mode_badge}")
            if order.is_cod:
                st.markdown(f"**Payment:** :red[**Cash on Delivery (Collect ₹ {order.cod_amount:,.2f})**]")
            else:
                st.markdown("**Payment:** :green[**Prepaid — Do Not Collect Cash**]")

    # Freshness check
    freshness = check_quote_freshness(selected_quote, max_age_seconds=900)
    if not freshness.is_fresh:
        st.error(f"Quote expired ({freshness.explanation}). Please click 'Find & Compare Couriers' above to refresh rates.")
        return

    # Mandatory measurements validation guard before approval
    ok_m_gate, _, errs_m_gate = validate_shipment_measurements(
        order.dimensions.length_cm if order.dimensions else None,
        order.dimensions.width_cm if order.dimensions else None,
        order.dimensions.height_cm if order.dimensions else None,
        order.parcel_weight_kg,
    )
    if not ok_m_gate:
        st.error(f"Mandatory measurements missing or invalid: {errs_m_gate[0] if errs_m_gate else 'Invalid measurements'}. Approval blocked.")
        return

    # Route validation and serviceability gating before approval
    ok_p_gate, err_p_gate = validate_pin_code(order.sender.pin_code, pin_type="pickup")
    ok_d_gate, err_d_gate = validate_pin_code(order.recipient.pin_code, pin_type="delivery")
    provider_mode = st.session_state.get("active_provider_mode", "demo")
    provider_gate = get_provider(provider_mode)
    svc_ok_gate, svc_msg_gate, _ = provider_gate.check_route_serviceability(order.sender.pin_code, order.recipient.pin_code)

    if not ok_p_gate or not ok_d_gate or not svc_ok_gate:
        st.error(f"Route validation error: {err_p_gate or err_d_gate or svc_msg_gate}. Approval and shipment creation blocked.")
        return

    # Preference Trade-off Warning & Required Confirmation Gate
    advisor_result = st.session_state.get("advisor_result")
    pref_violation_reasons = []
    if advisor_result and selected_quote:
        comp = next((c for c in advisor_result.all_evaluated if c.courier_code == selected_quote.courier_code), None)
        if comp and not comp.meets_both:
            if not comp.meets_budget:
                pref_violation_reasons.append(
                    f"Exceeds preferred budget limit by {comp.currency} {comp.budget_overrun:,.2f} (budget: {comp.currency} {advisor_result.preferences.max_budget:,.2f})"
                )
            if not comp.meets_deadline:
                if comp.has_sla_estimate:
                    pref_violation_reasons.append(
                        f"Misses delivery deadline by {comp.delay_days} day(s) (estimated {comp.estimated_delivery_days} days vs target of {advisor_result.preferences.max_delivery_days} days)"
                    )
                else:
                    pref_violation_reasons.append("Delivery estimate is unverified and cannot guarantee target deadline")

    tradeoff_confirmed = True
    if pref_violation_reasons:
        with st.container(border=True):
            st.warning("[NOTICE]  **Notice: Selected Courier Does Not Meet All Your Shipping Preferences**")
            for r in pref_violation_reasons:
                st.markdown(f"- [NOTICE]  {r}")
            st.caption("You may still proceed with booking this courier, but explicit confirmation is required.")
            tradeoff_confirmed = st.checkbox(
                "I confirm and accept these budget / delivery trade-offs for my shipment",
                value=st.session_state.get("user_confirmed_preference_tradeoff", False),
                key="chk_confirm_tradeoff",
            )
            st.session_state["user_confirmed_preference_tradeoff"] = tradeoff_confirmed

    # One explicit action
    if st.button("Approve and Create Shipment", type="primary", use_container_width=True, key="btn_approve_and_dispatch"):
        if pref_violation_reasons and not tradeoff_confirmed:
            st.error("Please confirm that you accept the budget/delivery trade-offs above before proceeding to shipment approval.")
            return

        if not ok_m_gate or not ok_p_gate or not ok_d_gate or not svc_ok_gate:
            st.error(f"Cannot dispatch: {errs_m_gate[0] if not ok_m_gate and errs_m_gate else (err_p_gate or err_d_gate or svc_msg_gate)}")
            return

        db, audit, idem, fsm, dispatch_service = get_services()
        provider = get_provider(provider_mode)

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
            is_demo_flag = getattr(selected_quote, "is_demo_data", True)
            ship_res = ShipmentResult(
                success=True,
                shipment_id=dispatch_result.shipment_id,
                tracking_number=dispatch_result.tracking_number,
                label_status=dispatch_result.label_status,
                label_url_or_data=dispatch_result.label_data or (provider.generate_label(dispatch_result.shipment_id, tracking_number=dispatch_result.tracking_number, order=order, quote=selected_quote) if hasattr(provider, "generate_label") else None),
                order_reference=order.order_reference,
                courier_code=selected_quote.courier_code,
                is_demo_data=is_demo_flag,
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
if hasattr(st, "dialog"):
    @st.dialog("Full-Size Shipping Label Preview", width="large")
    def _render_full_label_dialog(preview_html_str: str, pdf_data: Optional[bytes], clean_awb: str, page_format_val: str):
        st.caption(f"Unscaled High-Resolution Preview • Paper Format: {page_format_val} • AWB: {clean_awb.replace('.pdf', '')}")
        st.markdown(
            f"""
            <div style="display: flex; justify-content: center; align-items: center; padding: 12px; background: #f8fafc; border-radius: 8px; border: 1.5px solid #e2e8f0; margin-bottom: 12px; overflow-x: auto;">
                <div style="max-width: 520px; width: 100%;">
                    {preview_html_str}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if pdf_data and is_valid_pdf(pdf_data):
            st.download_button(
                label=f"Download Original PDF Shipping Label ({page_format_val})",
                data=pdf_data,
                file_name=clean_awb,
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                key="btn_dialog_download_pdf",
            )
else:
    _render_full_label_dialog = None


def _render_shipment_ready_section(order: OrderInput, selected_quote: CourierQuote, dispatch_result: Any):
    st.success(" **Your shipment is ready**")

    is_demo = getattr(selected_quote, "is_demo_data", True)
    awb_label = "AWB / Tracking (DEMO)" if is_demo else "AWB / Tracking (ShipEngine Sandbox)"

    dead_wt = getattr(order, "parcel_weight_kg", 1.0)
    billed_wt = None
    vol_wt = None
    if selected_quote and getattr(selected_quote, "weight_result", None):
        wr = selected_quote.weight_result
        dead_wt = getattr(wr, "actual_weight_kg", dead_wt)
        vol_wt = getattr(wr, "volumetric_weight_kg", None)
        billed_wt = getattr(wr, "chargeable_weight_kg", None)
    elif order.dimensions:
        rule = OFFICIAL_DOCUMENTED_CAPABILITIES.get(getattr(selected_quote, "courier_code", ""))
        if rule and rule.volumetric_rule and rule.volumetric_rule.divisor > 0:
            vol_wt = (order.dimensions.length_cm * order.dimensions.width_cm * order.dimensions.height_cm) / rule.volumetric_rule.divisor
            billed_wt = max(dead_wt, vol_wt)
    if billed_wt is None:
        billed_wt = dead_wt

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(awb_label, dispatch_result.tracking_number or "N/A")
    m2.metric("Courier & Service", f"{selected_quote.courier_name}")
    wt_delta = f"Actual: {dead_wt:.2f} kg" if vol_wt and vol_wt > dead_wt else None
    m3.metric("Billed Weight", f"{billed_wt:.2f} kg", delta=wt_delta, delta_color="inverse")
    report = st.session_state.get("verification_report")
    ver_status_val = "[PASS]  VERIFIED"
    if report:
        st_val = getattr(report.overall_status, "value", str(report.overall_status)).upper()
        is_demo_lbl = bool(
            getattr(report, "label_details", None)
            and getattr(report.label_details, "is_demo_label", False)
        )
        if st_val in ("VERIFIED", "PASS"):
            ver_status_val = "DEMO VERIFIED" if is_demo_lbl else "[PASS]  VERIFIED"
        elif st_val in ("FAILED", "FAIL"):
            ver_status_val = "[FAIL]  FAILED"
        elif st_val == "PENDING":
            ver_status_val = "PENDING"
        elif st_val in ("UNSUPPORTED",):
            ver_status_val = "[NOT RUN]  UNSUPPORTED"
        elif st_val in ("DEMO ONLY", "DEMO_ONLY"):
            ver_status_val = "DEMO ONLY"
        elif st_val in ("NOT VERIFIED", "NOT_VERIFIED", "UNVERIFIED"):
            ver_status_val = "[NOTICE]  UNVERIFIED"
        else:
            ver_status_val = f"[NOTICE]  {st_val}"
    m4.metric("Verification Status", ver_status_val)

    # Carrier Pickup Schedule
    pickup_res = st.session_state.get("pickup_result")
    if pickup_res:
        with st.container(border=True):
            st.markdown(f" **Carrier Pickup Scheduled:** Date `{pickup_res.scheduled_date}` | Window `{pickup_res.time_window}` | Ref `{pickup_res.pickup_reference}`")

    # 5-Point Verification Badges & Label Provenance
    if report:
        if getattr(report, "label_details", None) and getattr(report.label_details, "is_demo_label", False):
            st.info(f"ℹ **Label Verification Notice:** {report.label_details.summary_message}")

        status_str = getattr(report.overall_status, "value", str(report.overall_status)).upper()
        all_ok = (status_str in ("VERIFIED", "PASS"))
        expander_title = (
            "5-Point Independent Verification Checks (All Checkpoints Verified)"
            if all_ok
            else f"5-Point Independent Verification Checks ({status_str})"
        )
        with st.expander(expander_title, expanded=False):
            for c in report.checks:
                c_status_str = getattr(c.status, "value", str(c.status)).upper()
                if c.passed:
                    check_icon = "" if c_status_str in ("DEMO ONLY", "DEMO_ONLY") else "[PASS] "
                else:
                    if c_status_str in ("UNSUPPORTED",):
                        check_icon = "[NOT RUN] "
                    elif c_status_str in ("NOT VERIFIED", "NOT_VERIFIED", "UNVERIFIED"):
                        check_icon = "[PENDING] "
                    else:
                        check_icon = "[FAIL] "

                st.markdown(f"**{check_icon} {c.criterion_name}** (`{c_status_str}`)")
                st.caption(f"{c.details} (Observed: `{c.observed_value}`)")

                # If this check has detailed subchecks, render honest separated indicators
                if getattr(c, "subchecks", None):
                    with st.container(border=True):
                        st.markdown("#####  Label Verification Breakdown & Provenance")
                        sub_cols = st.columns(2)
                        for sc_idx, sc in enumerate(c.subchecks):
                            with sub_cols[sc_idx % 2]:
                                sc_st = str(sc.get("status", "NOT VERIFIED")).upper()
                                badge_icon = (
                                    "[LIVE]  PASS" if sc_st in ("PASS", "VERIFIED")
                                    else "[DEMO]  DEMO ONLY" if sc_st in ("DEMO ONLY", "DEMO_ONLY")
                                    else "[NOT RUN]  UNSUPPORTED" if sc_st == "UNSUPPORTED"
                                    else "[PENDING]  NOT VERIFIED" if sc_st in ("NOT VERIFIED", "NOT_VERIFIED", "UNVERIFIED")
                                    else "[ERROR]  FAIL"
                                )
                                st.markdown(f"**{badge_icon}** — {sc.get('name')}")
                                st.caption(sc.get("details", ""))
                st.write("")

    # ── PDF Label Generation & Download ──
    st.divider()
    st.subheader("Shipping Label")
    st.caption("Print-ready carrier shipping label formatted with verified routing, specifications, vector Code128 barcode, and 2D QR code.")

    col_fmt, col_btn = st.columns([1, 2])
    with col_fmt:
        label_fmt_label = st.radio(
            "Paper Size / Format",
            options=["A6 Compact (105 × 148 mm)", "4 × 6 inches (102 × 152 mm) Thermal"],
            index=0,
            horizontal=False,
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
        st.error(f"[NOTICE]  Error generating PDF label: {e}")

    clean_awb = sanitize_label_filename(dispatch_result.tracking_number, extension="pdf")

    # Safe generation of HTML/SVG preview
    preview_html = ""
    try:
        preview_html = generate_label_preview_from_shipment_context(
            order=order,
            quote=selected_quote,
            shipment_result=dispatch_result,
            pickup_result=pickup_res,
        )
    except Exception as ex:
        preview_html = f"<div style='padding: 20px; text-align: center; color: #64748b;'>Preview unavailable: {html.escape(str(ex))}. Please use the download button above to retrieve the official PDF label.</div>"

    with col_btn:
        st.write("")
        act_col1, act_col2 = st.columns([1.1, 0.9])
        with act_col1:
            if pdf_bytes and is_valid_pdf(pdf_bytes):
                st.download_button(
                    label=f"Download PDF Label ({page_format_val})",
                    data=pdf_bytes,
                    file_name=clean_awb,
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                    key="btn_download_pdf_ready",
                )
        with act_col2:
            if st.button("Expand Preview", use_container_width=True, key="btn_expand_label_preview"):
                if _render_full_label_dialog:
                    _render_full_label_dialog(preview_html, pdf_bytes, clean_awb, page_format_val)
                else:
                    st.session_state["inline_preview_expanded"] = not st.session_state.get("inline_preview_expanded", False)

        if dispatch_result.label_data and str(dispatch_result.label_data).startswith("http"):
            st.link_button("Open Official Carrier Label (PDF)", str(dispatch_result.label_data), use_container_width=True)

    # ── Centered, Compact, Scroll-Friendly Preview Container ──
    # Bounded to ~480px max-height to eliminate excessive vertical scrolling
    compact_preview_html = f"""
    <div style="display: flex; justify-content: center; align-items: flex-start; width: 100%; margin: 8px 0 10px 0;">
      <div style="
          max-height: 480px;
          max-width: 480px;
          width: 100%;
          overflow-y: auto;
          overflow-x: hidden;
          border: 1.5px solid #cbd5e1;
          border-radius: 8px;
          background: #f8fafc;
          box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.06), 0 2px 4px -2px rgba(0, 0, 0, 0.06);
          padding: 6px;
          box-sizing: border-box;
          scrollbar-width: thin;
      ">
        <div style="transform-origin: top center; transform: scale(0.88); margin-bottom: -11%;">
          {preview_html}
        </div>
      </div>
    </div>
    """
    st.markdown(compact_preview_html, unsafe_allow_html=True)

    # Collapsible full-size preview expander (collapsed by default to prevent vertical stretching)
    with st.expander("Full-Size Unscaled Label (100% Zoom)", expanded=st.session_state.get("inline_preview_expanded", False)):
        st.markdown(
            f"""
            <div style="display: flex; justify-content: center; align-items: center; width: 100%; padding: 8px; overflow-x: auto;">
                <div style="max-width: 520px; width: 100%;">
                    {preview_html}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()
    if st.button("Ship Another Parcel", use_container_width=True):
        for k in ["order", "quotes", "selected_quote", "dispatch_result", "verification_report", "pickup_result", "decision_trace", "active_demo_scenario", "approved_record"]:
            st.session_state.pop(k, None)
        st.session_state["approval_given"] = False
        st.rerun()


# ── Unified Guided Workflow Orchestrator ─────────────────────────────────────
def render_shipping_workflow():
    if "wizard_step" not in st.session_state:
        st.session_state["wizard_step"] = 1

    order = st.session_state.get("order")
    quotes = st.session_state.get("quotes") or []
    selected_quote = st.session_state.get("selected_quote")
    dispatch_result = st.session_state.get("dispatch_result")

    # If dispatched successfully, automatically advance to Step 4
    if dispatch_result and getattr(dispatch_result, "status", None) == ShipmentStatus.CREATED and order and selected_quote:
        st.session_state["wizard_step"] = 4

    current_step = st.session_state.get("wizard_step", 1)

    # ── Wizard Stepper Navigation ──
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("Step 1: Dimensions", use_container_width=True, type="primary" if current_step == 1 else "secondary", key="wz_btn_1"):
            st.session_state["wizard_step"] = 1
            st.rerun()
    with c2:
        btn_type = "primary" if current_step == 2 else "secondary"
        has_rates = bool(quotes and order)
        if st.button("Step 2: Carrier Rates", use_container_width=True, type=btn_type, disabled=not has_rates, key="wz_btn_2"):
            st.session_state["wizard_step"] = 2
            st.rerun()
    with c3:
        btn_type = "primary" if current_step == 3 else "secondary"
        has_quote = bool(quotes and order and selected_quote)
        if st.button("Step 3: Approval Gate", use_container_width=True, type=btn_type, disabled=not has_quote, key="wz_btn_3"):
            st.session_state["wizard_step"] = 3
            st.rerun()
    with c4:
        btn_type = "primary" if current_step == 4 else "secondary"
        has_dispatched = bool(dispatch_result and getattr(dispatch_result, "status", None) == ShipmentStatus.CREATED)
        if st.button("Step 4: Label Ready", use_container_width=True, type=btn_type, disabled=not has_dispatched, key="wz_btn_4"):
            st.session_state["wizard_step"] = 4
            st.rerun()

    st.markdown("<hr style='margin-top:2px;margin-bottom:18px;border-color:#CBD5E1;'>", unsafe_allow_html=True)

    # ── Render Only Current Window Panel ──
    if current_step == 1:
        _render_ship_a_parcel_form()
    elif current_step == 2:
        _render_rate_comparison()
        nav_c1, nav_c2 = st.columns([1, 1])
        with nav_c1:
            if st.button("Back to Dimensions", use_container_width=True, key="btn_wz_back_1"):
                st.session_state["wizard_step"] = 1
                st.rerun()
        with nav_c2:
            if st.button("Proceed to Approval Gate", type="primary", use_container_width=True, disabled=selected_quote is None, key="btn_wz_next_3"):
                st.session_state["wizard_step"] = 3
                st.rerun()
    elif current_step == 3:
        _render_review_and_approve_section()
        if st.button("Back to Rates", use_container_width=True, key="btn_wz_back_2"):
            st.session_state["wizard_step"] = 2
            st.rerun()
    elif current_step == 4:
        if order and selected_quote and dispatch_result:
            _render_shipment_ready_section(order, selected_quote, dispatch_result)
        else:
            st.session_state["wizard_step"] = 1
            st.rerun()


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
        st.info("ℹ Enter shipment details in 'Ship a Parcel' to generate and approve shipping quotes.")


# ── Advanced Tool 1: Packaging Optimizer ─────────────────────────────────────
def render_packaging_optimizer():
    st.subheader("Comparative Packaging Analyzer & Custom Box Builder")
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

    with st.expander("Add Custom-Size Packaging", expanded=False):
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

            save_btn = st.form_submit_button("Save Custom Packaging", type="primary", use_container_width=True)

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
                st.success(f"[PASS]  Saved custom packaging **{cp_name.strip()}** (`{pkg_id}`)!")
                st.rerun()
            except Exception as exc:
                st.error(f"[FAIL]  Error saving packaging: {exc}")

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

    if st.button("Evaluate Packaging Alternatives Across All Couriers", type="primary", use_container_width=True):
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
            st.success(f" **Best Packaging Option:** {best.box_name} ({'Custom' if best.is_custom else 'Demo'}) — saves {fmt_inr(best.estimated_freight_savings_inr)} in estimated freight.")

            for r in feasible:
                with st.expander(f"[PASS]  {r.box_name} — Billable {r.new_billable_slab_kg:.2f} kg (Saves {fmt_inr(r.estimated_freight_savings_inr)})"):
                    st.markdown(f"**Outer Dimensions:** {r.outer_dimensions.length_cm} × {r.outer_dimensions.width_cm} × {r.outer_dimensions.height_cm} cm")
                    gross_wt = getattr(r, "new_gross_weight_kg", 0.0) or (prod_weight + r.tare_weight_kg)
                    st.markdown(f"**Tare Weight:** {r.tare_weight_kg:.2f} kg | **Gross Weight:** {gross_wt:.2f} kg")
                    if r.courier_reports:
                        st.markdown("**Courier Capability & Divisor Compatibility:**")
                        for c_code, c_rep in r.courier_reports.items():
                            badge = "[LIVE]  Eligible" if c_rep.is_eligible else "[ERROR]  Ineligible"
                            st.caption(f"- **{c_code}**: {badge} (Divisor {c_rep.volumetric_divisor:.0f})")

        if infeasible:
            with st.expander(f"[FAIL]  Infeasible Packaging Options ({len(infeasible)})", expanded=False):
                for r in infeasible:
                    st.caption(f"[FAIL]  **{r.box_name}**: {r.rejection_reason}")


# ── Advanced Tool 2: Stress Testing Lab ──────────────────────────────────────
def render_stress_testing():
    st.subheader("Shipping Decision Stress Testing Lab")
    st.caption("Evaluate how courier recommendations hold up under input variations (weight, dimensions, surcharges).")

    order: Optional[OrderInput] = st.session_state.get("order")
    selected_quote: Optional[CourierQuote] = st.session_state.get("selected_quote")

    if order is None or selected_quote is None:
        st.info("ℹ Enter shipment details in 'Ship a Parcel' to run automated stress tests on the selected quote.")
        return

    st.markdown(f"**Active Order:** `{order.order_reference}` | **Selected Courier:** **{selected_quote.courier_name}**")

    if st.button("Run Decision Stress Test Suite", type="primary", use_container_width=True):
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
        st.markdown(f"**Fragility Verdict:** {'[ERROR]  Sensitive' if frag.is_fragile else '[LIVE]  Robust'} — {frag.fragility_verdict}")
        st.markdown(f"**Most Sensitive Input:** `{frag.most_sensitive_input}`")


# ── Advanced Tool 3: Courier Capability Registry ─────────────────────────────
def render_capability_registry():
    st.subheader("Official Courier Capability & Provenance Registry")
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
    st.header("My Shipments & Audit Records")

    db, audit, _, _, _ = get_services()
    tab_shipments, tab_audit = st.tabs(["Dispatched Shipments", "Audit Trail"])

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
                                        f"Download Label (PDF) for {tracking}",
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


# ── Official Test Cases Dashboard ─────────────────────────────────────────────
def render_official_test_cases_dashboard():
    """
    Visible Official Test Cases Dashboard displaying TC1 – TC4.
    Supports single test execution, batch execution, result resets,
    compact cards, metrics, and evidence inspection.
    """
    st.subheader("Official Hackathon Test Cases Dashboard (TC1 – TC4)")
    st.markdown(
        "*Dedicated verification dashboard for official Hackathon evaluation criteria. "
        "Each test executes real domain logic against deterministic mock courier configurations.*"
    )
    st.warning(
        "[DEMO]  **DEMO DATA NOTICE:** All carrier rates, tracking numbers, and dispatches are synthetic "
        "demonstration outputs produced by the Demo Shipping Adapter.",
        icon=None,
    )

    db = get_db()
    runner = OfficialScenariosRunner(db)

    # Action bar
    act_col1, act_col2 = st.columns([2, 1])
    with act_col1:
        if st.button("▶ Run All Official Tests (TC1 – TC4)", type="primary", use_container_width=True, key="btn_run_all_tc"):
            all_res = runner.run_all()
            if "official_test_results" not in st.session_state:
                st.session_state["official_test_results"] = {}
            for r in all_res:
                st.session_state["official_test_results"][r.test_case_id] = {
                    "test_case_id": r.test_case_id,
                    "title": r.test_case_name,
                    "status": r.status,
                    "expected": r.expected_result,
                    "actual": r.actual_result,
                    "explanation": r.explanation,
                    "input_record": r.input_record,
                    "evidence": r.evidence_references,
                    "timestamp": r.timestamp,
                }
            st.rerun()

    with act_col2:
        if st.button("Reset Official Test Results", use_container_width=True, key="btn_reset_all_tc"):
            st.session_state["official_test_results"] = {}
            try:
                db.clear_official_test_runs()
            except Exception:
                pass
            st.rerun()

    # Metrics calculation
    tc_keys = ["TC1", "TC2", "TC3", "TC4"]
    ot_results = st.session_state.get("official_test_results", {})
    if not ot_results:
        try:
            db_runs = db.get_official_test_runs()
            for run in db_runs:
                tid = run["test_case_id"]
                if tid in tc_keys and tid not in ot_results:
                    ot_results[tid] = {
                        "test_case_id": tid,
                        "title": run["test_case_name"],
                        "status": run["status"],
                        "expected": run["expected_result"],
                        "actual": run["actual_result"],
                        "explanation": run["explanation"],
                        "input_record": json.loads(run["input_record_json"]) if isinstance(run["input_record_json"], str) else run["input_record_json"],
                        "evidence": json.loads(run["evidence_json"]) if isinstance(run["evidence_json"], str) else run["evidence_json"],
                        "timestamp": run["timestamp"],
                    }
            st.session_state["official_test_results"] = ot_results
        except Exception:
            pass

    passed_cnt = sum(1 for k in tc_keys if ot_results.get(k, {}).get("status") == "PASSED")
    failed_cnt = sum(1 for k in tc_keys if ot_results.get(k, {}).get("status") == "FAILED")
    error_cnt = sum(1 for k in tc_keys if ot_results.get(k, {}).get("status") == "ERROR")
    not_run_cnt = 4 - (passed_cnt + failed_cnt + error_cnt)

    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Passed", f"{passed_cnt} / 4")
    m2.metric("Failed", str(failed_cnt))
    m3.metric("Errors", str(error_cnt))
    m4.metric("Not Run", str(not_run_cnt))

    if passed_cnt == 4:
        st.success(" **All 4 Official Test Cases Passed Successfully!** All evaluation criteria satisfied.")
    elif failed_cnt > 0 or error_cnt > 0:
        st.error(f"[NOTICE]  **{failed_cnt} Test Case(s) Failed, {error_cnt} Error(s).** Review failure explanations below.")
    elif not_run_cnt == 4:
        st.info("ℹ Official test cases are currently NOT RUN. Click **'Run All Official Tests'** or run individual tests below.")

    st.markdown("---")

    tc_specs = [
        {
            "id": "TC1",
            "title": "TC1: Same Parcel on Three Couriers",
            "subtitle": "Evaluates the same shipment across 3 couriers; ranks by lowest applicable cost with transparent breakdowns.",
            "expected": "All 3 couriers compared, fee breakdowns transparently calculated, ranked by lowest cost.",
        },
        {
            "id": "TC2",
            "title": "TC2: Large, Light Box (Volumetric Penalty)",
            "subtitle": "60x50x40 cm (120,000 cm³) with 0.5 kg actual weight. Volumetric divisors (5000, 4500, 4000) applied.",
            "expected": "Volumetric weight exceeds dead weight; each courier applies its distinct divisor (5000, 4500, 4000).",
        },
        {
            "id": "TC3",
            "title": "TC3: Destination PIN Not Serviceable",
            "subtitle": "Remote destination PIN 791111 (Arunachal Pradesh). Excludes unserviceable Air Express; cost is never zero.",
            "expected": "Air Express excluded with explicit reason; unserviceable cost is None (not zero); ranking selects only eligible couriers.",
        },
        {
            "id": "TC4",
            "title": "TC4: Label Created, AWB Matched & Pickup Verified",
            "subtitle": "Approved shipment dispatched, compliant shipping label generated, AWB verified, 5/5 checkpoints passed.",
            "expected": "Shipment created, AWB returned, label corresponds to AWB/order, pickup schedule confirmed, 5-point verification passes.",
        },
    ]

    # Render in 2x2 grid
    row1_c1, row1_c2 = st.columns(2)
    row2_c1, row2_c2 = st.columns(2)
    grid_cols = [row1_c1, row1_c2, row2_c1, row2_c2]

    for i, spec in enumerate(tc_specs):
        with grid_cols[i]:
            with st.container(border=True):
                st.markdown(f"####  {spec['title']}")
                st.caption(spec["subtitle"])
                st.markdown(f"**Expected Outcome:** {spec['expected']}")

                # Status badge
                tc_data = ot_results.get(spec["id"])
                if tc_data:
                    st_val = tc_data.get("status", "NOT RUN")
                    if st_val == "PASSED":
                        st.success(f"[PASS]  **Status: {st_val}**")
                    elif st_val == "FAILED":
                        st.error(f"[FAIL]  **Status: {st_val}**")
                    elif st_val == "ERROR":
                        st.error(f"[NOTICE]  **Status: {st_val}**")
                    else:
                        st.info(f"[NOT RUN]  **Status: {st_val}**")

                    st.markdown(f"**Actual Result:** {tc_data.get('actual', 'None')}")
                    if st_val in ("FAILED", "ERROR") and tc_data.get("explanation"):
                        st.warning(f"**Failure Details:** {tc_data.get('explanation')}")

                    with st.expander("Inspect Inputs & Evidence (JSON)", expanded=False):
                        st.caption(f"Executed at {tc_data.get('timestamp', '')[:19]}Z")
                        st.markdown("**Input Record Snapshot:**")
                        st.json(tc_data.get("input_record", {}))
                        st.markdown("**Evidence References:**")
                        st.json(tc_data.get("evidence", {}))
                else:
                    st.info("[NOT RUN]  **Status: NOT RUN**")
                    st.caption("Click 'Run Test' below to execute this scenario.")

                if st.button(f"▶ Run {spec['id']} Test", key=f"btn_run_{spec['id'].lower()}", use_container_width=True):
                    res = runner.run_single(spec["id"])
                    if "official_test_results" not in st.session_state:
                        st.session_state["official_test_results"] = {}
                    st.session_state["official_test_results"][spec["id"]] = {
                        "test_case_id": res.test_case_id,
                        "title": res.test_case_name,
                        "status": res.status,
                        "expected": res.expected_result,
                        "actual": res.actual_result,
                        "explanation": res.explanation,
                        "input_record": res.input_record,
                        "evidence": res.evidence_references,
                        "timestamp": res.timestamp,
                    }
                    st.rerun()


# ── Main Application ──────────────────────────────────────────────────────────
def main():
    render_sidebar()

    st.title("Adaptive Shipping Intelligence Agent")
    st.markdown("*Find a suitable courier, compare shipping costs, and prepare your shipment in a few simple steps.*")
    st.caption(
        "[DEMO]  **DEMO DATA NOTICE:** All courier rates, tracking references, and simulated dispatches "
        "are produced by the Demo Shipping Adapter and are NOT live carrier prices or confirmed bookings."
    )

    tab_ship, tab_official, tab_history, tab_advanced = st.tabs([
        "Ship a Parcel",
        "Official Test Cases",
        "My Shipments",
        "Advanced Tools",
    ])

    with tab_ship:
        render_shipping_workflow()
    with tab_official:
        render_official_test_cases_dashboard()
    with tab_history:
        render_history()
    with tab_advanced:
        adv1, adv2, adv3 = st.tabs([
            "Packaging Analyzer",
            "Stress Testing Lab",
            "Courier Capability Registry",
        ])
        with adv1:
            render_packaging_optimizer()
        with adv2:
            render_stress_testing()
        with adv3:
            render_capability_registry()


if __name__ == "__main__":
    main()
