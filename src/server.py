"""
Adaptive Shipping Intelligence Agent — FastAPI Backend Server.
Provides complete REST APIs for Rate Shopping, 10-Scenario Evaluator,
Packaging Optimizer, Shipment Dispatch, and Label Generation.
Serves modern HTML/CSS/JS frontend from static directory.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.domain.advisor import (
    AdvisorRecommendationResult,
    UserShippingPreferences,
    build_advisor_recommendation,
)
from src.domain.dimension_engine import (
    CourierCapabilityRule,
    compute_volumetric_weight,
    compute_chargeable_weight,
    compute_girth,
    compute_length_plus_girth,
)
from src.domain.evaluator import (
    SCENARIO_METADATA,
    EvaluatorSuite,
    ScenarioResult,
)
from src.domain.official_scenarios import (
    OfficialScenariosRunner,
    OfficialTestCaseResult,
)
from src.domain.freshness import check_quote_freshness
from src.domain.label_generator import (
    generate_label_preview_html,
    generate_shipping_label_pdf,
    generate_label_from_shipment_context,
    generate_label_preview_from_shipment_context,
)
from src.domain.verification import ShipmentVerificationService, VerificationStatus
from src.domain.models import (
    Address,
    CostBreakdown,
    CourierQuote,
    Dimensions,
    OrderInput,
    PackageType,
    ShipmentStatus,
    WeightResult,
)
from src.domain.packaging import CustomPackaging, evaluate_packaging_alternatives
from src.domain.pricing import calculate_landed_cost
from src.domain.ranking import (
    RankedCourierQuote,
    RankingMode,
    RankingPreferences,
    rank_couriers,
)
from src.domain.reconciliation import (
    ApprovalInvalidatedError,
    DispatchAttemptResult,
    ShipmentDispatchService,
    StaleQuoteError,
)
from src.persistence.audit import AuditEventType, AuditLogger
from src.persistence.database import DEFAULT_DB_PATH, DatabaseManager
from src.persistence.idempotency import (
    DuplicateDispatchError,
    IdempotencyManager,
    ReconciliationRequiredError,
    ShipmentAlreadyCreatedError,
)
from src.persistence.state_machine import ApprovalRecord, ShipmentStateMachine
from src.providers.base import CourierProvider, ShipmentResult
from src.providers.demo_adapter import ALL_DEMO_COURIERS, DemoShippingAdapter
from src.providers.registry import (
    get_provider,
    get_active_provider_mode,
    set_active_provider_mode,
    get_provider_status,
)
from src.schemas.price_prediction import (
    PricePredictionApiRequest,
    PricePredictionApiResponse,
    QuoteComparisonDto,
)
from src.ml.feature_schema import (
    DomesticCourierFeatures,
    PredictionStatus,
    ReferenceSculptureFeatures,
)
from src.ml.price_predictor import predict_shipment_price
from src.services.price_prediction_service import (
    PricePredictionServiceResponse,
    ShipmentPricePredictionService,
)
from src.domain.agent_tools import (
    execute_check_pin_serviceability_tool,
    execute_rank_and_explain_recommendations_tool,
)
from src.ui.helpers import (
    validate_pin_code,
    validate_positive_float,
    validate_shipment_measurements,
)

# -----------------------------------------------------------------------------
# App & Database Initialization
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Adaptive Shipping Intelligence Agent",
    description="Intelligent Rate Comparer, Packaging Optimizer & Dispatch API",
    version="2.0.0",
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = _PROJECT_ROOT / "static"
try:
    _STATIC_DIR.mkdir(exist_ok=True)
    (_STATIC_DIR / "css").mkdir(exist_ok=True)
    (_STATIC_DIR / "js").mkdir(exist_ok=True)
except OSError:
    pass

db_manager = DatabaseManager(DEFAULT_DB_PATH)
audit_logger = AuditLogger(db_manager)
idempotency_mgr = IdempotencyManager(db_manager)
fsm = ShipmentStateMachine()
dispatch_service = ShipmentDispatchService(db_manager, audit_logger, idempotency_mgr, fsm)
verification_service = ShipmentVerificationService(audit_logger)

# In-memory session cache for latest generated PDF labels by shipment_id
_LABEL_PDF_CACHE: dict[str, bytes] = {}
_ACTIVE_ORDERS_CACHE: dict[str, OrderInput] = {}
_ACTIVE_QUOTES_CACHE: dict[str, list[CourierQuote]] = {}


# -----------------------------------------------------------------------------
# Request & Response Schemas
# -----------------------------------------------------------------------------

class RateRequest(BaseModel):
    sender_name: str = "Warehouse Logistics"
    sender_pin: str = "110001"
    sender_city: str = "New Delhi"
    sender_state: str = "Delhi"
    sender_phone: str = "9876543210"

    recipient_name: str = "Priya Sharma"
    recipient_pin: str = "400001"
    recipient_city: str = "Mumbai"
    recipient_state: str = "Maharashtra"
    recipient_phone: str = "9123456789"

    length_cm: float
    width_cm: float
    height_cm: float
    parcel_weight_kg: float

    package_type: str = "box"
    is_cod: bool = False
    cod_amount: float = 0.0

    ranking_mode: str = "lowest_cost"  # lowest_cost, fastest, balanced
    provider_mode: Optional[str] = None  # None defaults to active server provider_mode
    excluded_couriers: list[str] = Field(default_factory=list)

    max_budget: Optional[float] = None
    max_delivery_days: Optional[int] = None
    priority: str = "balanced"


class ApprovalDispatchPayload(BaseModel):
    order_reference: str
    quote_id: str
    courier_code: str
    approved_by: str = "Shipping Operator"
    total_cost: float
    provider_mode: Optional[str] = None


class CustomPackagingPayload(BaseModel):
    name: str
    package_type: str = "box"
    outer_length_cm: float
    outer_width_cm: float
    outer_height_cm: float
    wall_thickness_cm: float = 0.5
    padding_cm: float = 0.5
    tare_weight_kg: float = 0.15


# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/health")
def health_check() -> dict[str, Any]:
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": "connected",
        "active_provider_mode": get_active_provider_mode(),
        "provider_modes": ["demo", "sandbox", "manual_sandbox"],
    }


class ProviderModePayload(BaseModel):
    mode: str


@app.get("/api/provider/status")
def get_provider_status_api() -> dict[str, Any]:
    """Retrieve connection and credential configuration status."""
    return get_provider_status()


@app.post("/api/provider/mode")
def set_provider_mode_api(payload: ProviderModePayload) -> dict[str, Any]:
    """Switch active provider mode between 'demo', 'sandbox', and 'manual_sandbox'."""
    try:
        new_mode = set_active_provider_mode(payload.mode)
        return {"status": "ok", "active_mode": new_mode}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class ManualSandboxQuotePayload(BaseModel):
    order_reference: str
    courier_name: str
    service_type: str = "Standard Ground"
    base_freight: float
    fuel_surcharge: float = 0.0
    tax_amount: float = 0.0
    total_cost: float
    estimated_delivery_days: int = 3
    notes: Optional[str] = None
    dashboard_reference: Optional[str] = None
    source_identifier: Optional[str] = None
    source_url: Optional[str] = None
    retrieval_timestamp: Optional[str] = None
    retrieval_method: Optional[str] = "MANUAL_DASHBOARD_TRANSCRIPTION"
    raw_data: Optional[dict[str, Any]] = None


class DashboardExportPayload(BaseModel):
    order_reference: str
    source_identifier: str = "courier-dashboard-export"
    source_url: Optional[str] = None
    retrieval_timestamp: Optional[str] = None
    quotes: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/manual-sandbox/quote")
def create_manual_sandbox_quote(payload: ManualSandboxQuotePayload) -> dict[str, Any]:
    """
    Record a manually verified quote copied by the operator from a carrier sandbox dashboard.
    Stores source IDs, dashboard URLs, retrieval timestamps, and field-level provenance.
    """
    order = _ACTIVE_ORDERS_CACHE.get(payload.order_reference)
    if not order:
        order_row = db_manager.get_order(payload.order_reference)
        if not order_row:
            raise HTTPException(status_code=404, detail=f"Order '{payload.order_reference}' not found.")
        sender = Address.model_validate_json(order_row["sender_json"])
        recipient = Address.model_validate_json(order_row["recipient_json"])
        order = OrderInput(
            order_reference=order_row["order_reference"],
            sender=sender,
            recipient=recipient,
            parcel_weight_kg=order_row["parcel_weight_kg"],
            dimensions=Dimensions(
                length_cm=order_row["length_cm"],
                width_cm=order_row["width_cm"],
                height_cm=order_row["height_cm"],
            ),
            package_type=PackageType(order_row["package_type"]),
            is_cod=bool(order_row["is_cod"]),
            cod_amount=order_row["cod_amount"] or 0.0,
        )
        _ACTIVE_ORDERS_CACHE[order.order_reference] = order

    courier_code_clean = re.sub(r'[^A-Z0-9_]', '_', payload.courier_name.upper().strip())
    quote_id = f"MANUAL-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    retrieval_ts = payload.retrieval_timestamp or now_iso
    source_id = payload.source_identifier or "operator-dashboard-manual"
    source_url = payload.source_url or payload.dashboard_reference or None
    retrieval_method = payload.retrieval_method or "MANUAL_DASHBOARD_TRANSCRIPTION"

    input_hash = hashlib.sha256(
        f"{order.order_reference}:{order.sender.pin_code}:{order.recipient.pin_code}:{order.parcel_weight_kg}".encode()
    ).hexdigest()[:12]

    subtotal = payload.base_freight + payload.fuel_surcharge
    fuel_rate_pct = round((payload.fuel_surcharge / payload.base_freight * 100.0), 2) if payload.base_freight > 0 else 0.0
    tax_rate_pct = round((payload.tax_amount / subtotal * 100.0), 2) if subtotal > 0 else 18.0

    breakdown = CostBreakdown(
        base_freight=payload.base_freight,
        fuel_surcharge=payload.fuel_surcharge,
        fuel_surcharge_rate_pct=fuel_rate_pct,
        cod_fee=0.0,
        handling_fee=0.0,
        subtotal_before_tax=round(subtotal, 2),
        tax_amount=payload.tax_amount,
        tax_rate_pct=tax_rate_pct,
        total_cost=payload.total_cost,
        currency="INR",
    )

    weight_res = WeightResult(
        actual_weight_kg=order.parcel_weight_kg,
        volumetric_weight_kg=round(order.dimensions.volume_cm3 / 5000.0, 3),
        chargeable_weight_kg=order.parcel_weight_kg,
        billing_slab_kg=order.parcel_weight_kg,
        divisor_used=5000.0,
        is_volumetric_penalty=False,
    )

    raw_snapshot = payload.raw_data or {
        "courier_name": payload.courier_name,
        "service_type": payload.service_type,
        "base_freight": payload.base_freight,
        "fuel_surcharge": payload.fuel_surcharge,
        "tax_amount": payload.tax_amount,
        "total_cost": payload.total_cost,
        "estimated_delivery_days": payload.estimated_delivery_days,
        "dashboard_reference": payload.dashboard_reference,
        "source_url": source_url,
        "source_id": source_id,
        "notes": payload.notes,
        "input_timestamp": retrieval_ts,
    }

    quote = CourierQuote(
        quote_id=quote_id,
        courier_code=f"MANUAL_{courier_code_clean}",
        courier_name=f"{payload.courier_name} (Manual Entry — Not API Verified)",
        service_type=payload.service_type,
        estimated_delivery_days=payload.estimated_delivery_days,
        is_serviceable=True,
        is_eligible=True,
        eligibility_status="ELIGIBLE",
        evidence_source="Manual Sandbox Dashboard Entry (Not API Verified)",
        evidence_url=source_url,
        weight_result=weight_res,
        cost_breakdown=breakdown,
        retrieval_timestamp=retrieval_ts,
        source_type="MANUAL_SANDBOX",
        source_id=source_id,
        retrieval_method=retrieval_method,
        raw_response_snapshot=raw_snapshot,
        input_hash=input_hash,
        decision_explanation=f"Manual entry from external dashboard. Ref: {payload.dashboard_reference or 'N/A'}. Source: {source_id}. URL: {source_url or 'N/A'}. Not API verified. Notes: {payload.notes or 'None'}",
        is_demo_data=False,
        missing_evidence=[
            "Manual Entry — Not API Verified: Rate and SLA transcribed from external dashboard without direct API verification",
            "Operator entered manually; not retrieved via direct API call",
        ],
        disclaimer="MANUAL ENTRY — NOT API VERIFIED: Entered manually by operator from external sandbox dashboard.",
    )
    quote.populate_standard_field_provenance(order.order_reference)

    db_manager.save_quote_model(quote, order.order_reference)
    existing_quotes = _ACTIVE_QUOTES_CACHE.get(order.order_reference, [])
    updated_quotes = [q for q in existing_quotes if q.quote_id != quote.quote_id] + [quote]
    _ACTIVE_QUOTES_CACHE[order.order_reference] = updated_quotes

    ranking = rank_couriers(updated_quotes, RankingPreferences(mode=RankingMode.LOWEST_COST))

    return {
        "status": "created",
        "quote": quote.model_dump(),
        "quotes_count": len(updated_quotes),
        "selected_winner": ranking.selected_winner.quote.model_dump() if ranking and ranking.selected_winner else None,
    }


@app.post("/api/manual-sandbox/dashboard-export")
def import_dashboard_export(payload: DashboardExportPayload) -> dict[str, Any]:
    """
    Import and parse exported or copied dashboard rate payloads from a courier aggregator.
    Extracts quote fields and attaches complete provenance (source ID, source URL, timestamp).
    """
    order = _ACTIVE_ORDERS_CACHE.get(payload.order_reference)
    if not order:
        order_row = db_manager.get_order(payload.order_reference)
        if not order_row:
            raise HTTPException(status_code=404, detail=f"Order '{payload.order_reference}' not found.")
        sender = Address.model_validate_json(order_row["sender_json"])
        recipient = Address.model_validate_json(order_row["recipient_json"])
        order = OrderInput(
            order_reference=order_row["order_reference"],
            sender=sender,
            recipient=recipient,
            parcel_weight_kg=order_row["parcel_weight_kg"],
            dimensions=Dimensions(
                length_cm=order_row["length_cm"],
                width_cm=order_row["width_cm"],
                height_cm=order_row["height_cm"],
            ),
            package_type=PackageType(order_row["package_type"]),
            is_cod=bool(order_row["is_cod"]),
            cod_amount=order_row["cod_amount"] or 0.0,
        )
        _ACTIVE_ORDERS_CACHE[order.order_reference] = order

    now_iso = datetime.now(timezone.utc).isoformat()
    retrieval_ts = payload.retrieval_timestamp or now_iso
    source_id = payload.source_identifier or "courier-dashboard-export"
    source_url = payload.source_url or "https://dashboard.courier-aggregator.mock"
    input_hash = hashlib.sha256(
        f"{order.order_reference}:{order.sender.pin_code}:{order.recipient.pin_code}:{order.parcel_weight_kg}".encode()
    ).hexdigest()[:12]

    imported_quotes: list[CourierQuote] = []
    for item in payload.quotes:
        c_name = item.get("courier_name") or item.get("name") or "Aggregator Courier"
        c_code = re.sub(r'[^A-Z0-9_]', '_', str(c_name).upper().strip())
        s_type = item.get("service_type") or item.get("tier") or "Standard Express"
        base_f = float(item.get("base_freight") or item.get("freight") or item.get("rate") or 0.0)
        fuel = float(item.get("fuel_surcharge") or item.get("fuel") or 0.0)
        tax = float(item.get("tax_amount") or item.get("tax") or item.get("gst") or 0.0)
        total_c = float(item.get("total_cost") or item.get("total") or (base_f + fuel + tax))
        est_days = int(item.get("estimated_delivery_days") or item.get("sla_days") or item.get("etd_days") or 3)

        subtotal = base_f + fuel
        fuel_pct = round((fuel / base_f * 100.0), 2) if base_f > 0 else 0.0
        tax_pct = round((tax / subtotal * 100.0), 2) if subtotal > 0 else 18.0

        breakdown = CostBreakdown(
            base_freight=base_f,
            fuel_surcharge=fuel,
            fuel_surcharge_rate_pct=fuel_pct,
            cod_fee=0.0,
            handling_fee=0.0,
            subtotal_before_tax=round(subtotal, 2),
            tax_amount=tax,
            tax_rate_pct=tax_pct,
            total_cost=total_c,
            currency="INR",
        )
        weight_res = WeightResult(
            actual_weight_kg=order.parcel_weight_kg,
            volumetric_weight_kg=round(order.dimensions.volume_cm3 / 5000.0, 3),
            chargeable_weight_kg=order.parcel_weight_kg,
            billing_slab_kg=order.parcel_weight_kg,
            divisor_used=5000.0,
            is_volumetric_penalty=False,
        )

        quote_id = f"EXPORT-{uuid.uuid4().hex[:8]}"
        q = CourierQuote(
            quote_id=quote_id,
            courier_code=f"EXP_{c_code}",
            courier_name=f"{c_name} (Dashboard Export)",
            service_type=s_type,
            estimated_delivery_days=est_days,
            is_serviceable=True,
            is_eligible=True,
            eligibility_status="ELIGIBLE",
            evidence_source=f"Aggregator Dashboard Export ({source_id})",
            evidence_url=source_url,
            weight_result=weight_res,
            cost_breakdown=breakdown,
            retrieval_timestamp=retrieval_ts,
            source_type="MANUAL_SANDBOX",
            source_id=source_id,
            retrieval_method="DASHBOARD_EXPORT_PARSER",
            raw_response_snapshot=item,
            input_hash=input_hash,
            decision_explanation=f"Parsed from aggregator dashboard export payload. Source: {source_id}. URL: {source_url}.",
            is_demo_data=False,
            missing_evidence=[
                "Transcribed from aggregator dashboard export snapshot",
                "Direct real-time carrier API connection not used",
            ],
            disclaimer="DASHBOARD EXPORT: Parsed from courier dashboard export payload.",
        )
        q.populate_standard_field_provenance(order.order_reference)
        db_manager.save_quote_model(q, order.order_reference)
        imported_quotes.append(q)

    existing = _ACTIVE_QUOTES_CACHE.get(order.order_reference, [])
    updated = existing + imported_quotes
    _ACTIVE_QUOTES_CACHE[order.order_reference] = updated
    ranking = rank_couriers(updated, RankingPreferences(mode=RankingMode.LOWEST_COST))

    return {
        "status": "imported",
        "imported_count": len(imported_quotes),
        "total_quotes": len(updated),
        "quotes": [q.model_dump() for q in imported_quotes],
        "selected_winner": ranking.selected_winner.quote.model_dump() if ranking and ranking.selected_winner else None,
    }


@app.get("/api/quotes/{quote_id}/provenance")
def get_quote_provenance(quote_id: str) -> dict[str, Any]:
    """Retrieve full field-level provenance audit trail for a quote."""
    prov_records = db_manager.get_field_provenance_for_quote(quote_id)
    quote_row = db_manager.get_quote(quote_id)
    return {
        "quote_id": quote_id,
        "exists": bool(quote_row or prov_records),
        "quote_summary": quote_row,
        "provenance_count": len(prov_records),
        "field_provenance": prov_records,
    }


@app.get("/api/orders/{order_reference}/provenance")
def get_order_provenance(order_reference: str) -> dict[str, Any]:
    """Retrieve all field-level provenance audit records for an order."""
    prov_records = db_manager.get_field_provenance_for_order(order_reference)
    return {
        "order_reference": order_reference,
        "provenance_count": len(prov_records),
        "provenance_records": prov_records,
    }


# -----------------------------------------------------------------------------
# Official Competition Test Cases (TC1 – TC4) Metadata & Endpoints
# -----------------------------------------------------------------------------

OFFICIAL_TC_METADATA: dict[str, dict[str, Any]] = {
    "TC1": {
        "id": "TC1",
        "title": "TC1: Same Parcel on Three Couriers",
        "subtitle": "Evaluates the same shipment across 3 couriers; ranks by lowest applicable cost with transparent breakdowns.",
        "description": "One parcel with three courier quotes. All three compared and ranked by total cost, with base freight, fuel surcharge, and tax fee breakdowns.",
        "expected": "All 3 couriers compared, fee breakdowns transparently calculated, ranked by lowest cost.",
        "route": "560103 -> 411001",
        "dimensions": "25.0 x 20.0 x 15.0 cm",
        "weight_kg": 2.0,
        "builder": OfficialScenariosRunner.build_order_tc1,
    },
    "TC2": {
        "id": "TC2",
        "title": "TC2: Large, Light Box (Volumetric Penalty)",
        "subtitle": "60x50x40 cm (120,000 cm3) with 0.5 kg actual weight. Volumetric divisors (5000, 4500, 4000) applied.",
        "description": "Light parcel with large dimensions. Volumetric weight exceeds dead weight; each courier applies its distinct divisor.",
        "expected": "Volumetric weight exceeds dead weight; each courier applies its distinct divisor (5000, 4500, 4000).",
        "route": "122001 -> 700016",
        "dimensions": "60.0 x 50.0 x 40.0 cm",
        "weight_kg": 0.5,
        "builder": OfficialScenariosRunner.build_order_tc2,
    },
    "TC3": {
        "id": "TC3",
        "title": "TC3: Destination PIN Not Serviceable",
        "subtitle": "Remote destination PIN 791111 (Arunachal Pradesh). Excludes unserviceable Air Express; cost is never silently zero.",
        "description": "Destination marked unserviceable for Air Express (remote prefix '79'). Unserviceable courier excluded with explicit audit reason; cost is None.",
        "expected": "Air Express excluded with explicit reason; unserviceable cost is None (not zero); ranking selects only eligible couriers.",
        "route": "110020 -> 791111",
        "dimensions": "25.0 x 20.0 x 15.0 cm",
        "weight_kg": 2.0,
        "builder": OfficialScenariosRunner.build_order_tc3,
    },
    "TC4": {
        "id": "TC4",
        "title": "TC4: Label Created, AWB Matched & Pickup Verified",
        "subtitle": "Approved order dispatch, compliant shipping label creation, AWB matching, and 5-point verification check.",
        "description": "Approved order with eligible courier. Label created, tracking AWB returned, label corresponds to AWB/order, pickup schedule confirmed, 5-point independent verification passes.",
        "expected": "Shipment created, AWB returned, label corresponds to AWB/order, pickup schedule confirmed, 5-point verification passes.",
        "route": "201309 -> 600002",
        "dimensions": "30.0 x 20.0 x 10.0 cm",
        "weight_kg": 1.5,
        "builder": OfficialScenariosRunner.build_order_tc4,
    },
}


@app.get("/api/official-scenarios")
def list_official_scenarios() -> list[dict[str, Any]]:
    """List the 4 Official Competition Test Cases with latest execution results."""
    items = []
    for tc_id, meta in OFFICIAL_TC_METADATA.items():
        runs = db_manager.get_official_test_runs(tc_id)
        latest_run = runs[0] if runs else None
        
        status = latest_run["status"] if latest_run else "NOT RUN"
        actual_result = latest_run["actual_result"] if latest_run else None
        explanation = latest_run["explanation"] if latest_run else None
        evidence = None
        if latest_run and latest_run.get("evidence_json"):
            try:
                evidence = json.loads(latest_run["evidence_json"])
            except Exception:
                evidence = latest_run["evidence_json"]
        timestamp = latest_run["timestamp"] if latest_run else None

        items.append({
            "id": tc_id,
            "title": meta["title"],
            "subtitle": meta["subtitle"],
            "description": meta["description"],
            "expected": meta["expected"],
            "route": meta["route"],
            "dimensions": meta["dimensions"],
            "weight_kg": meta["weight_kg"],
            "status": status,
            "actual_outcome": actual_result,
            "explanation": explanation,
            "evidence": evidence,
            "timestamp": timestamp,
        })
    return items


@app.post("/api/official-scenarios/{test_case_id}/run")
def run_official_scenario_test(test_case_id: str) -> dict[str, Any]:
    """Execute official deterministic test run for TC1-TC4."""
    tc_id = test_case_id.upper().strip()
    if tc_id not in OFFICIAL_TC_METADATA:
        raise HTTPException(status_code=404, detail=f"Official test case '{test_case_id}' not found.")
    runner = OfficialScenariosRunner(db_manager)
    res = runner.run_single(tc_id)
    return res.model_dump()


@app.post("/api/official-scenarios/run")
def run_all_official_scenarios() -> list[dict[str, Any]]:
    """Execute all 4 official test cases in sequence."""
    runner = OfficialScenariosRunner(db_manager)
    results = runner.run_all()
    return [r.model_dump() for r in results]


@app.post("/api/official-scenarios/{test_case_id}/load")
def load_official_scenario(test_case_id: str) -> dict[str, Any]:
    """Build and evaluate official test case inputs in 1 click."""
    tc_id = test_case_id.upper().strip()
    if tc_id not in OFFICIAL_TC_METADATA:
        raise HTTPException(status_code=404, detail=f"Official test case '{test_case_id}' not found.")
    
    meta = OFFICIAL_TC_METADATA[tc_id]
    builder = meta["builder"]
    order = builder()

    provider = DemoShippingAdapter()
    quotes = provider.get_rates(order)

    # Persist in DB
    db_manager.save_order_model(order)
    for q in quotes:
        db_manager.save_quote_model(q, order.order_reference)

    _ACTIVE_ORDERS_CACHE[order.order_reference] = order
    _ACTIVE_QUOTES_CACHE[order.order_reference] = quotes

    # Ranking
    rank_pref = RankingPreferences(mode=RankingMode.LOWEST_COST)
    ranking = rank_couriers(quotes, rank_pref)

    adv_prefs = UserShippingPreferences(max_budget=300.0, max_delivery_days=3)
    advisor = build_advisor_recommendation(quotes, adv_prefs)

    has_eligible = bool(ranking and ranking.selected_winner)
    return {
        "test_case_id": tc_id,
        "title": meta["title"],
        "order": order.model_dump(),
        "quotes": [q.model_dump() for q in quotes],
        "ranking": ranking.model_dump() if ranking else None,
        "advisor": advisor.model_dump() if advisor else None,
        "selected_quote_id": ranking.selected_winner.quote.quote_id if has_eligible else None,
        "has_eligible_couriers": has_eligible,
        "comparison_table": getattr(ranking, "comparison_table", []),
        "winner_reasons": getattr(ranking, "winner_reasons", []),
        "trade_off_analysis": getattr(ranking, "trade_off_analysis", ""),
        "loss_reasons": getattr(ranking, "loss_reasons", {}),
        "decision_provenance": getattr(ranking, "decision_provenance", {}),
    }


# -----------------------------------------------------------------------------
# 10 Evaluator Edge-Case Scenarios (Demo A through Demo J)
# -----------------------------------------------------------------------------

@app.get("/api/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """List all 10 Evaluator Scenarios with metadata and latest outcomes."""
    scenarios = []
    for sc_id, meta in SCENARIO_METADATA.items():
        runs = db_manager.get_official_test_runs(sc_id)
        latest_run = runs[0] if runs else None
        
        status = latest_run["status"] if latest_run else "NOT RUN"
        actual_outcome = latest_run["actual_result"] if latest_run else None

        scenarios.append({
            "id": sc_id,
            "title": meta.get("title", sc_id),
            "full_title": meta.get("full_title", sc_id),
            "description": meta.get("description", ""),
            "expected": meta.get("expected", ""),
            "status": status,
            "actual_outcome": actual_outcome,
        })
    return scenarios


@app.post("/api/scenarios/{scenario_id}/load")
def load_scenario(scenario_id: str) -> dict[str, Any]:
    """Build and evaluate scenario inputs in 1 click."""
    sc_id = scenario_id.upper()
    if sc_id not in SCENARIO_METADATA:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    meta = SCENARIO_METADATA[sc_id]
    builder = meta["builder"]
    order = builder()

    couriers = CORE_DEMO_COURIERS if sc_id in ("DEMO_E", "DEMO_F", "DEMO_G", "DEMO_H") else ALL_DEMO_COURIERS
    provider = DemoShippingAdapter(couriers=couriers)
    quotes = provider.get_rates(order)

    # Persist in DB
    db_manager.save_order_model(order)
    for q in quotes:
        db_manager.save_quote_model(q, order.order_reference)

    _ACTIVE_ORDERS_CACHE[order.order_reference] = order
    _ACTIVE_QUOTES_CACHE[order.order_reference] = quotes

    # Ranking
    rank_pref = RankingPreferences(mode=RankingMode.LOWEST_COST)
    ranking = rank_couriers(quotes, rank_pref)

    # Advisor
    adv_prefs = UserShippingPreferences(
        max_budget=300.0,
        max_delivery_days=3,
    )
    advisor = build_advisor_recommendation(quotes, adv_prefs)

    has_eligible = bool(ranking and ranking.selected_winner)
    return {
        "scenario_id": sc_id,
        "title": meta["title"],
        "order": order.model_dump(),
        "quotes": [q.model_dump() for q in quotes],
        "ranking": ranking.model_dump() if ranking else None,
        "advisor": advisor.model_dump() if advisor else None,
        "selected_quote_id": ranking.selected_winner.quote.quote_id if has_eligible else None,
        "has_eligible_couriers": has_eligible,
        "comparison_table": getattr(ranking, "comparison_table", []),
        "winner_reasons": getattr(ranking, "winner_reasons", []),
        "trade_off_analysis": getattr(ranking, "trade_off_analysis", ""),
        "loss_reasons": getattr(ranking, "loss_reasons", {}),
        "decision_provenance": getattr(ranking, "decision_provenance", {}),
    }


@app.post("/api/scenarios/{scenario_id}/run")
def run_scenario_test(scenario_id: str) -> dict[str, Any]:
    """Execute official deterministic test run for the scenario and persist outcome."""
    sc_id = scenario_id.upper()
    if sc_id not in SCENARIO_METADATA:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    suite = EvaluatorSuite(db_manager)
    res: ScenarioResult = suite.run_scenario(sc_id)

    # Persist scenario result into SQLite so history, metrics, and cards reflect actual outcomes
    try:
        db_manager.insert_official_test_run({
            "test_case_id": res.scenario_id,
            "test_case_name": res.title,
            "status": res.status.value if hasattr(res.status, "value") else str(res.status),
            "input_record_json": json.dumps(res.input_summary),
            "expected_result": res.expected_outcome,
            "actual_result": res.actual_outcome,
            "evidence_json": json.dumps(res.evidence or {}),
            "explanation": res.explanation,
            "timestamp": res.timestamp,
        })
    except Exception:
        pass

    return res.model_dump()


@app.post("/api/scenarios/run")
def run_all_scenarios() -> list[dict[str, Any]]:
    """Execute all 10 demo scenarios in sequence."""
    suite = EvaluatorSuite(db_manager)
    results = suite.run_all()
    for res in results:
        try:
            db_manager.insert_official_test_run({
                "test_case_id": res.scenario_id,
                "test_case_name": res.title,
                "status": res.status.value if hasattr(res.status, "value") else str(res.status),
                "input_record_json": json.dumps(res.input_summary),
                "expected_result": res.expected_outcome,
                "actual_result": res.actual_outcome,
                "evidence_json": json.dumps(res.evidence or {}),
                "explanation": res.explanation,
                "timestamp": res.timestamp,
            })
        except Exception:
            pass
    return [r.model_dump() for r in results]


@app.get("/api/scenarios/test-runs")
def get_test_runs() -> list[dict[str, Any]]:
    """Retrieve recorded test runs."""
    return db_manager.get_official_test_runs()


@app.post("/api/scenarios/test-runs/clear")
def clear_test_runs() -> dict[str, str]:
    db_manager.clear_official_test_runs()
    return {"status": "cleared"}



# -----------------------------------------------------------------------------
# ML Shipment Price Prediction Endpoints
# -----------------------------------------------------------------------------

@app.post("/api/v1/predictions/shipment-price", response_model=PricePredictionApiResponse)
def predict_shipment_price_endpoint(req: PricePredictionApiRequest) -> PricePredictionApiResponse:
    """
    ML Shipment Price Prediction & Rate Benchmark API.
    Provides non-binding statistical price forecasts and variance comparison
    against active provider quotes. Strictly preserves provider quote authority.
    """
    try:
        if req.model_type == "reference_sculpture":
            ref_dict = req.reference_features or {
                "Artist Reputation": 0.5,
                "Height": req.height_cm,
                "Width": req.width_cm,
                "Weight": req.parcel_weight_kg * 1000.0,
                "Material": "Brass",
                "Price Of Sculpture": 500.0,
                "Base Shipping Price": 20.0,
                "International": "No",
                "Express Shipment": "Yes" if "EXPRESS" in req.courier_code.upper() else "No",
                "Installation Included": "No",
                "Transport": "Airways",
                "Fragile": "Yes",
                "Customer Information": "Working Class",
                "Remote": "No",
                "Scheduled Date": "2026-10-23",
                "Delivery Date": "2026-10-26",
            }
            ref_feat = ReferenceSculptureFeatures(**ref_dict)
            pred = predict_shipment_price(ref_feat)
            return PricePredictionApiResponse(
                predicted_price=pred.predicted_price,
                currency=pred.currency,
                model_name=pred.model_name,
                model_version=pred.model_version,
                feature_schema_version=pred.feature_schema_version,
                status=pred.status,
                is_provider_quote=False,
                confidence_interval=pred.confidence_interval,
                input_summary=pred.input_summary,
                limitations=pred.limitations,
                explanation=pred.explanation,
                quote_comparison=None,
                agent_advice="Reference sculpture model (USD) evaluated. For Indian domestic parcel rates in INR, select domestic_courier model.",
                recommended_action="Use domestic_courier model for domestic PIN routing and volumetric weight calculations.",
                error_message=pred.error_message,
            )

        if req.model_type in ("model2_catboost", "courier_intelligence", "model2"):
            from src.domain.model2_adapter import model2_adapter
            raw_input = {
                "origin_pin": req.origin_pin,
                "dest_pin": req.destination_pin,
                "length_cm": req.length_cm,
                "width_cm": req.width_cm,
                "height_cm": req.height_cm,
                "parcel_weight_kg": req.parcel_weight_kg,
                "courier_code": req.courier_code,
            }
            p_pred = model2_adapter.predict_price(raw_input)
            d_pred = model2_adapter.predict_delivery_days(raw_input)
            cov = model2_adapter.check_historical_pin_coverage(req.origin_pin, req.destination_pin, req.courier_code)

            status_val = PredictionStatus.SUCCESS if p_pred.status == "SUCCESS" else PredictionStatus.MODEL_UNAVAILABLE

            qc_dto = None
            if req.quote_cost is not None and p_pred.predicted_price_inr is not None:
                diff = round(req.quote_cost - p_pred.predicted_price_inr, 2)
                pct = round((diff / p_pred.predicted_price_inr) * 100.0, 1) if p_pred.predicted_price_inr > 0 else 0.0
                verdict = "FAVORABLE" if diff < 0 else "ACCEPTABLE" if abs(pct) <= 20 else "PREMIUM"
                qc_dto = QuoteComparisonDto(
                    courier_code=req.courier_code,
                    courier_name=req.quote_courier_name or req.courier_code,
                    provider_quote_cost=round(req.quote_cost, 2),
                    model_predicted_price=p_pred.predicted_price_inr,
                    currency="INR",
                    difference_amount=diff,
                    percentage_difference=pct,
                    verdict=verdict,
                    analysis=f"Quote vs Model 2 CatBoost historical rate (variance: {pct}%). Historical SLA estimate: {d_pred.rounded_days or 'N/A'} days.",
                )

            ci = (
                (round(p_pred.predicted_price_inr - 43.34, 2), round(p_pred.predicted_price_inr + 43.34, 2))
                if p_pred.predicted_price_inr is not None
                else None
            )
            sla_text = f" Estimated SLA: {d_pred.rounded_days} days." if d_pred.rounded_days else ""
            cov_text = f" Historical synthetic PIN coverage: {cov.status}."

            return PricePredictionApiResponse(
                predicted_price=p_pred.predicted_price_inr,
                currency="INR",
                model_name=p_pred.model_name,
                model_version=p_pred.model_version,
                feature_schema_version="2.0.0",
                status=status_val,
                is_provider_quote=False,
                confidence_interval=ci,
                input_summary={
                    "pickup_pin": req.origin_pin,
                    "delivery_pin": req.destination_pin,
                    "weight_kg": req.parcel_weight_kg,
                    "length_cm": req.length_cm,
                    "width_cm": req.width_cm,
                    "height_cm": req.height_cm,
                    "courier_code": req.courier_code,
                },
                limitations=[
                    "CatBoost regression trained on 14 million synthetic Indian courier shipment rows.",
                    "SLA turnaround estimate trained on 2 million records.",
                    "Carrier binding quotes strictly govern actual booking and billing.",
                ],
                explanation=f"Model 2 CatBoost prediction based on route ({req.origin_pin} -> {req.destination_pin}) and dimensional slab.{sla_text}{cov_text}",
                quote_comparison=qc_dto,
                agent_advice=f"Model 2 historical pricing benchmark evaluated.{sla_text} Carrier quote governs booking.",
                recommended_action="Validate binding rate against Model 2 benchmark before dispatch approval.",
                error_message=p_pred.error_message,
            )

        # Domestic courier model (default)
        features = DomesticCourierFeatures(
            origin_pin=req.origin_pin,
            destination_pin=req.destination_pin,
            length_cm=req.length_cm,
            width_cm=req.width_cm,
            height_cm=req.height_cm,
            parcel_weight_kg=req.parcel_weight_kg,
            package_type=req.package_type,
            courier_code=req.courier_code,
            is_cod=req.is_cod,
            cod_amount=req.cod_amount,
        )

        active_quote = None
        if req.quote_cost is not None:
            active_quote = {
                "cost": req.quote_cost,
                "courier_code": req.courier_code,
                "courier_name": req.quote_courier_name or req.courier_code,
            }

        service = ShipmentPricePredictionService()
        svc_resp: PricePredictionServiceResponse = service.predict_and_compare(features, active_quote=active_quote)

        qc_dto = None
        if svc_resp.quote_comparison:
            qc = svc_resp.quote_comparison
            qc_dto = QuoteComparisonDto(
                courier_code=qc.courier_code,
                courier_name=qc.courier_name,
                provider_quote_cost=qc.provider_quote_cost,
                model_predicted_price=qc.model_predicted_price,
                currency=qc.currency,
                difference_amount=qc.difference_amount,
                percentage_difference=qc.percentage_difference,
                verdict=qc.verdict,
                analysis=qc.analysis,
            )

        return PricePredictionApiResponse(
            predicted_price=svc_resp.prediction.predicted_price,
            currency=svc_resp.prediction.currency,
            model_name=svc_resp.prediction.model_name,
            model_version=svc_resp.prediction.model_version,
            feature_schema_version=svc_resp.prediction.feature_schema_version,
            status=svc_resp.prediction.status,
            is_provider_quote=False,
            confidence_interval=svc_resp.prediction.confidence_interval,
            input_summary=svc_resp.prediction.input_summary,
            limitations=svc_resp.prediction.limitations,
            explanation=svc_resp.prediction.explanation,
            quote_comparison=qc_dto,
            agent_advice=svc_resp.agent_advice,
            recommended_action=svc_resp.recommended_action,
            error_message=svc_resp.prediction.error_message,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Price prediction error: {str(exc)}")


@app.post("/api/v1/model2/predict")
def model2_predict_endpoint(req: PricePredictionApiRequest) -> dict[str, Any]:
    """
    Dedicated Model 2 Courier Intelligence Endpoint.
    Returns CatBoost rate estimate, SLA transit turnaround estimate, and PIN coverage evidence.
    """
    from src.domain.model2_adapter import model2_adapter

    raw_input = {
        "origin_pin": req.origin_pin,
        "dest_pin": req.destination_pin,
        "length_cm": req.length_cm,
        "width_cm": req.width_cm,
        "height_cm": req.height_cm,
        "parcel_weight_kg": req.parcel_weight_kg,
        "courier_code": req.courier_code,
    }
    p_pred = model2_adapter.predict_price(raw_input)
    d_pred = model2_adapter.predict_delivery_days(raw_input)
    cov = model2_adapter.check_historical_pin_coverage(req.origin_pin, req.destination_pin, req.courier_code)

    return {
        "status": p_pred.status,
        "price_prediction": p_pred.model_dump(),
        "rate_prediction": p_pred.model_dump(),
        "delivery_prediction": d_pred.model_dump(),
        "pin_coverage": cov.model_dump(),
        "is_catboost_available": model2_adapter.is_catboost_available(),
        "disclaimer": "Model 2 estimates are non-binding. Binding carrier quotes govern actual booking.",
    }


class ServiceabilityCheckRequest(BaseModel):
    origin_pin: str
    destination_pin: str
    courier_codes: Optional[list[str]] = None


@app.post("/api/serviceability")
def check_serviceability_endpoint(req: ServiceabilityCheckRequest) -> dict[str, Any]:
    """
    Deterministic local PIN serviceability evaluation endpoint.
    Validates origin and destination PIN formats and evaluates carrier rules.
    """
    return execute_check_pin_serviceability_tool({
        "origin_pin": req.origin_pin,
        "destination_pin": req.destination_pin,
        "courier_codes": req.courier_codes,
    })


@app.post("/api/rates")
def calculate_rates(req: RateRequest) -> dict[str, Any]:
    """
    Validate inputs, calculate volumetric and billable weights,
    query couriers, rank options, and generate advisor recommendations.
    """
    # 1. Validation & Serviceability
    is_valid_m, field_errs, err_list = validate_shipment_measurements(
        req.length_cm, req.width_cm, req.height_cm, req.parcel_weight_kg
    )
    if not is_valid_m:
        raise HTTPException(status_code=422, detail={"field_errors": field_errs, "errors": err_list})

    svc_tool_res = execute_check_pin_serviceability_tool({
        "origin_pin": req.sender_pin,
        "destination_pin": req.recipient_pin,
    })
    if svc_tool_res.get("classification") == "INVALID_FORMAT":
        raise HTTPException(status_code=422, detail={"errors": [svc_tool_res.get("message")]})

    ok_p, err_p = validate_pin_code(req.sender_pin, pin_type="pickup")
    ok_d, err_d = validate_pin_code(req.recipient_pin, pin_type="delivery")
    if not ok_p or not ok_d:
        raise HTTPException(status_code=422, detail={"errors": [err_p or err_d]})

    # 2. Build Order
    order_ref = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    order = OrderInput(
        order_reference=order_ref,
        sender=Address(
            name=req.sender_name,
            street_line1="Main Street Logistics Hub",
            city=req.sender_city,
            state=req.sender_state,
            pin_code=req.sender_pin,
            phone=req.sender_phone,
        ),
        recipient=Address(
            name=req.recipient_name,
            street_line1="Customer Delivery Address",
            city=req.recipient_city,
            state=req.recipient_state,
            pin_code=req.recipient_pin,
            phone=req.recipient_phone,
        ),
        dimensions=Dimensions(
            length_cm=req.length_cm,
            width_cm=req.width_cm,
            height_cm=req.height_cm,
        ),
        parcel_weight_kg=req.parcel_weight_kg,
        package_type=PackageType(req.package_type),
        is_cod=req.is_cod,
        cod_amount=req.cod_amount if req.is_cod else 0.0,
    )

    # 3. Provider rates
    mode = (req.provider_mode or get_active_provider_mode()).lower().strip()
    if mode in ("sandbox", "local_sandbox"):
        mode = "local_sandbox"
    if mode == "manual_sandbox":
        cached = _ACTIVE_QUOTES_CACHE.get(order.order_reference, [])
        manual_quotes = [q for q in cached if getattr(q, "source_type", "") == "MANUAL_SANDBOX"]
        if not manual_quotes:
            db_quotes_data = db_manager.get_quotes_for_order(order.order_reference)
            for row in db_quotes_data:
                if row.get("source_type") == "MANUAL_SANDBOX" and row.get("quote_data_json"):
                    try:
                        manual_quotes.append(CourierQuote.model_validate_json(row["quote_data_json"]))
                    except Exception:
                        pass
        raw_quotes = manual_quotes
    else:
        provider = get_provider(mode)
        raw_quotes = provider.get_rates(order)

    # Filter excluded couriers if any
    quotes = [q for q in raw_quotes if q.courier_code not in req.excluded_couriers]

    # 4. Persistence
    db_manager.save_order_model(order)
    for q in quotes:
        db_manager.save_quote_model(q, order.order_reference)

    _ACTIVE_ORDERS_CACHE[order.order_reference] = order
    _ACTIVE_QUOTES_CACHE[order.order_reference] = quotes

    # 5. Ranking
    mode_map = {
        "lowest_cost": RankingMode.LOWEST_COST,
        "fastest": RankingMode.FASTEST_DELIVERY,
        "balanced": RankingMode.BALANCED,
    }
    selected_mode = mode_map.get(req.ranking_mode.lower(), RankingMode.LOWEST_COST)
    ranking = rank_couriers(quotes, RankingPreferences(mode=selected_mode))

    # 6. Advisor
    adv_prefs = UserShippingPreferences(
        max_budget=req.max_budget,
        max_delivery_days=req.max_delivery_days,
    )
    advisor = build_advisor_recommendation(quotes, adv_prefs)

    # 7. Packaging alternatives
    custom_pkgs_data = db_manager.get_custom_packagings()
    custom_pkgs = [
        CustomPackaging(
            packaging_id=r["packaging_id"],
            name=r["name"],
            package_type=PackageType(r["package_type"]),
            outer_dimensions=Dimensions(
                length_cm=r["outer_length_cm"],
                width_cm=r["outer_width_cm"],
                height_cm=r["outer_height_cm"],
            ),
            wall_thickness_cm=r["wall_thickness_cm"],
            padding_cm=r["padding_cm"],
            tare_weight_kg=r["tare_weight_kg"],
        )
        for r in custom_pkgs_data
    ]
    pkg_analysis = evaluate_packaging_alternatives(
        product_dimensions=order.dimensions,
        product_net_weight_kg=order.parcel_weight_kg,
        current_outer_dimensions=order.dimensions,
        custom_packages=custom_pkgs,
        couriers=ALL_DEMO_COURIERS,
    ) if custom_pkgs else []

    vol_div_5000 = (req.length_cm * req.width_cm * req.height_cm) / 5000.0
    has_eligible = bool(ranking and ranking.selected_winner)
    selected_id = ranking.selected_winner.quote.quote_id if has_eligible else None

    return {
        "order": order.model_dump(),
        "volumetric_weight_5000": round(vol_div_5000, 2),
        "actual_weight_kg": req.parcel_weight_kg,
        "chargeable_weight_estimated": max(req.parcel_weight_kg, vol_div_5000),
        "quotes": [q.model_dump() for q in quotes],
        "ranking": ranking.model_dump() if ranking else None,
        "advisor": advisor.model_dump() if advisor else None,
        "packaging_analysis": [a.model_dump() for a in pkg_analysis] if pkg_analysis else [],
        "selected_quote_id": selected_id,
        "has_eligible_couriers": has_eligible,
        "serviceability_report": svc_tool_res,
        "comparison_table": getattr(ranking, "comparison_table", []),
        "winner_reasons": getattr(ranking, "winner_reasons", []),
        "trade_off_analysis": getattr(ranking, "trade_off_analysis", ""),
        "loss_reasons": getattr(ranking, "loss_reasons", {}),
        "decision_provenance": getattr(ranking, "decision_provenance", {}),
    }


@app.post("/api/dispatch")
def approve_and_dispatch(payload: ApprovalDispatchPayload) -> dict[str, Any]:
    """
    Human-in-the-loop approved dispatch gate.
    Verifies freshness, checks idempotency locks, creates shipment with provider,
    generates shipping label PDF & preview, schedules pickup, and runs verification.
    """
    order = _ACTIVE_ORDERS_CACHE.get(payload.order_reference)
    if not order:
        order_dict = db_manager.get_order(payload.order_reference)
        if not order_dict:
            raise HTTPException(status_code=404, detail=f"Order '{payload.order_reference}' not found.")
        sender_d = json.loads(order_dict["sender_json"])
        recip_d = json.loads(order_dict["recipient_json"])
        order = OrderInput(
            order_reference=order_dict["order_reference"],
            sender=Address(**sender_d),
            recipient=Address(**recip_d),
            dimensions=Dimensions(
                length_cm=order_dict["length_cm"],
                width_cm=order_dict["width_cm"],
                height_cm=order_dict["height_cm"],
            ),
            parcel_weight_kg=order_dict["parcel_weight_kg"],
            package_type=PackageType(order_dict["package_type"]),
            is_cod=bool(order_dict["is_cod"]),
            cod_amount=order_dict["cod_amount"],
        )

    quotes = _ACTIVE_QUOTES_CACHE.get(payload.order_reference) or []
    quote = next((q for q in quotes if q.quote_id == payload.quote_id), None)
    if not quote:
        db_quotes = db_manager.get_quotes_for_order(payload.order_reference)
        matching = next((q for q in db_quotes if q["quote_id"] == payload.quote_id), None)
        if matching:
            q_data = json.loads(matching["quote_data_json"])
            quote = CourierQuote(**q_data)
        else:
            raise HTTPException(status_code=404, detail=f"Quote '{payload.quote_id}' not found.")

    # Strict check: Block unserviceable or ineligible couriers from human approval and dispatch
    if not quote.is_serviceable or not quote.is_eligible or quote.cost_breakdown is None:
        reason = quote.unserviceable_reason or "; ".join(quote.eligibility_reasons or []) or "Courier quote is unserviceable or ineligible."
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve or dispatch an unserviceable or ineligible courier quote: {reason}",
        )

    provider_mode = (payload.provider_mode or get_active_provider_mode()).lower().strip()
    if provider_mode in ("sandbox", "local_sandbox"):
        provider_mode = "local_sandbox"
    provider = get_provider(provider_mode)

    approval = ApprovalRecord(
        order_reference=order.order_reference,
        quote_id=quote.quote_id,
        courier_code=quote.courier_code,
        approved_by=payload.approved_by,
        total_cost=payload.total_cost,
    )

    try:
        dispatch_res: DispatchAttemptResult = dispatch_service.attempt_dispatch(
            order=order,
            quote=quote,
            approval=approval,
            provider=provider,
        )
    except ShipmentAlreadyCreatedError as e:
        shp = db_manager.get_shipment(order.order_reference)
        return {
            "status": "ALREADY_CREATED",
            "message": str(e),
            "shipment_id": shp["shipment_id"] if shp else None,
            "tracking_number": shp.get("tracking_number") if shp else None,
        }
    except DuplicateDispatchError as e:
        raise HTTPException(status_code=409, detail=f"Duplicate dispatch in progress: {e}")
    except ReconciliationRequiredError as e:
        raise HTTPException(status_code=409, detail=f"Reconciliation required: {e}")
    except ApprovalInvalidatedError as e:
        raise HTTPException(status_code=400, detail=f"Approval invalidated: {e}")
    except StaleQuoteError as e:
        raise HTTPException(status_code=400, detail=f"Quote stale: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Dispatch error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dispatch failed: {e}")

    # Schedule pickup
    pickup_res = None
    if hasattr(provider, "schedule_pickup"):
        try:
            pickup_res = provider.schedule_pickup(dispatch_res.shipment_id)
        except Exception:
            pass

    # 5-Point Verification & Shipment Result
    ship_res = ShipmentResult(
        success=True,
        shipment_id=dispatch_res.shipment_id,
        tracking_number=dispatch_res.tracking_number,
        label_status=dispatch_res.label_status,
        label_url_or_data=dispatch_res.label_data,
        order_reference=order.order_reference,
        courier_code=quote.courier_code,
        is_demo_data=getattr(quote, "is_demo_data", True),
    )
    report = verification_service.verify_shipment(
        order=order,
        quote=quote,
        approval=approval,
        shipment_result=ship_res,
        provider=provider,
        pickup_result=pickup_res,
    )

    # Generate PDF label and cache it
    pdf_bytes = generate_label_from_shipment_context(
        order=order,
        quote=quote,
        shipment_result=ship_res,
        pickup_result=pickup_res,
    )
    _LABEL_PDF_CACHE[dispatch_res.shipment_id] = pdf_bytes

    # Generate HTML/SVG label preview
    html_preview = generate_label_preview_from_shipment_context(
        order=order,
        quote=quote,
        shipment_result=ship_res,
        pickup_result=pickup_res,
    )

    return {
        "status": "CREATED",
        "shipment_id": dispatch_res.shipment_id,
        "order_reference": order.order_reference,
        "tracking_number": dispatch_res.tracking_number,
        "label_status": dispatch_res.label_status,
        "pickup": pickup_res.model_dump() if pickup_res and hasattr(pickup_res, "model_dump") else (pickup_res if isinstance(pickup_res, dict) else None),
        "verification_report": report.model_dump() if report else None,
        "label_html_preview": html_preview,
        "explanation": dispatch_res.explanation,
    }


@app.get("/api/labels/{shipment_id}/pdf")
def download_label_pdf(shipment_id: str):
    """Download shipping label as binary PDF."""
    pdf_bytes = _LABEL_PDF_CACHE.get(shipment_id)
    if not pdf_bytes:
        shp = db_manager.get_shipment_by_id(shipment_id)
        if not shp:
            raise HTTPException(status_code=404, detail="Shipment not found.")
        order_dict = db_manager.get_order(shp["order_reference"])
        if not order_dict:
            raise HTTPException(status_code=404, detail="Order record not found.")

        sender_d = json.loads(order_dict["sender_json"])
        recip_d = json.loads(order_dict["recipient_json"])
        order = OrderInput(
            order_reference=order_dict["order_reference"],
            sender=Address(**sender_d),
            recipient=Address(**recip_d),
            dimensions=Dimensions(
                length_cm=order_dict["length_cm"],
                width_cm=order_dict["width_cm"],
                height_cm=order_dict["height_cm"],
            ),
            parcel_weight_kg=order_dict["parcel_weight_kg"],
            package_type=PackageType(order_dict["package_type"]),
            is_cod=bool(order_dict["is_cod"]),
            cod_amount=order_dict["cod_amount"],
        )

        db_quotes = db_manager.get_quotes_for_order(shp["order_reference"])
        matching = next((q for q in db_quotes if q["quote_id"] == shp["selected_quote_id"]), None)
        quote = None
        if matching:
            quote = CourierQuote(**json.loads(matching["quote_data_json"]))

        mock_ship_res = ShipmentResult(
            success=True,
            shipment_id=shipment_id,
            tracking_number=shp.get("tracking_number") or "AWB-DEMO-001",
            label_status=shp.get("label_status", "UNSUPPORTED"),
            label_url_or_data=shp.get("label_data"),
            order_reference=shp["order_reference"],
            courier_code=shp["courier_code"],
            is_demo_data=True,
        )
        pdf_bytes = generate_label_from_shipment_context(
            order=order,
            quote=quote,
            shipment_result=mock_ship_res,
        )
        _LABEL_PDF_CACHE[shipment_id] = pdf_bytes

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="shipping_label_{shipment_id}.pdf"'},
    )


@app.get("/api/labels/{shipment_id}/preview")
def get_label_preview(shipment_id: str):
    """Return HTML/SVG preview of shipping label."""
    shp = db_manager.get_shipment_by_id(shipment_id)
    if not shp:
        raise HTTPException(status_code=404, detail="Shipment not found.")
    order_dict = db_manager.get_order(shp["order_reference"])
    if not order_dict:
        raise HTTPException(status_code=404, detail="Order not found.")

    sender_d = json.loads(order_dict["sender_json"])
    recip_d = json.loads(order_dict["recipient_json"])

    db_quotes = db_manager.get_quotes_for_order(shp["order_reference"])
    matching = next((q for q in db_quotes if q["quote_id"] == shp["selected_quote_id"]), None)
    courier_name = matching["courier_name"] if matching else shp.get("courier_code", "Courier")
    service_type = matching["service_type"] if matching else "Standard"

    html_preview = generate_label_preview_html(
        tracking_number=shp.get("tracking_number") or "AWB-DEMO-001",
        order_reference=shp["order_reference"],
        courier_name=courier_name,
        service_type=service_type,
        shipment_id=shipment_id,
        origin_pin=sender_d.get("pin_code", "000000"),
        origin_city=sender_d.get("city", "Origin Hub"),
        origin_state=sender_d.get("state", "IN"),
        destination_pin=recip_d.get("pin_code", "000000"),
        destination_city=recip_d.get("city", "Destination Hub"),
        destination_state=recip_d.get("state", "IN"),
        sender_name=sender_d.get("name", "Sender"),
        recipient_name=recip_d.get("name", "Recipient"),
        actual_weight_kg=order_dict.get("parcel_weight_kg", 1.0),
        volumetric_weight_kg=(order_dict["length_cm"] * order_dict["width_cm"] * order_dict["height_cm"]) / 5000.0,
        billable_weight_kg=max(order_dict["parcel_weight_kg"], (order_dict["length_cm"] * order_dict["width_cm"] * order_dict["height_cm"]) / 5000.0),
        dimensions_cm=f"{order_dict['length_cm']}×{order_dict['width_cm']}×{order_dict['height_cm']} cm",
        is_cod=bool(order_dict["is_cod"]),
        cod_amount=order_dict["cod_amount"],
    )
    return HTMLResponse(content=html_preview)


@app.get("/api/audit-logs")
def get_audit_logs(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve immutable audit log events."""
    logs = db_manager.get_audit_logs()
    return logs[:limit]


@app.get("/api/custom-packaging")
def get_custom_packagings() -> list[dict[str, Any]]:
    return db_manager.get_custom_packagings()


@app.post("/api/custom-packaging")
def add_custom_packaging(payload: CustomPackagingPayload) -> dict[str, Any]:
    pkg_id = db_manager.insert_custom_packaging(payload.model_dump())
    return {"status": "created", "packaging_id": pkg_id}


@app.delete("/api/custom-packaging/{pkg_id}")
def delete_custom_packaging(pkg_id: str) -> dict[str, Any]:
    ok = db_manager.delete_custom_packaging(pkg_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Packaging box not found.")
    return {"status": "deleted", "packaging_id": pkg_id}


@app.get("/api/couriers")
def get_couriers() -> list[dict[str, Any]]:
    return db_manager.get_courier_capabilities()


# -----------------------------------------------------------------------------
# Static File & UI Routes
# -----------------------------------------------------------------------------

@app.get("/")
@app.get("/api/index")
def serve_index():
    index_file = _STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Adaptive Shipping Intelligence Agent</h1><p>Frontend static files initializing...</p>")
    return FileResponse(index_file)


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


# Mount static folder
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
