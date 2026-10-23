"""
Adaptive Shipping Intelligence Agent — FastAPI Backend Server.
Provides complete REST APIs for Rate Shopping, 10-Scenario Evaluator,
Packaging Optimizer, Shipment Dispatch, and Label Generation.
Serves modern HTML/CSS/JS frontend from static directory.
"""

from __future__ import annotations

import io
import json
import os
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
from src.providers.registry import get_provider
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
_STATIC_DIR.mkdir(exist_ok=True)
(_STATIC_DIR / "css").mkdir(exist_ok=True)
(_STATIC_DIR / "js").mkdir(exist_ok=True)

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
    provider_mode: str = "demo"  # demo or shipengine
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
    provider_mode: str = "demo"


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
        "provider_modes": ["demo", "shipengine"],
    }


@app.get("/api/scenarios")
def list_scenarios() -> list[dict[str, Any]]:
    """List all 10 Evaluator Scenarios with metadata."""
    scenarios = []
    for sc_id, meta in SCENARIO_METADATA.items():
        scenarios.append({
            "id": sc_id,
            "title": meta.get("title", sc_id),
            "full_title": meta.get("full_title", sc_id),
            "description": meta.get("description", ""),
            "expected": meta.get("expected", ""),
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

    provider = DemoShippingAdapter(couriers=ALL_DEMO_COURIERS)
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

    return {
        "scenario_id": sc_id,
        "title": meta["title"],
        "order": order.model_dump(),
        "quotes": [q.model_dump() for q in quotes],
        "ranking": ranking.model_dump() if ranking else None,
        "advisor": advisor.model_dump() if advisor else None,
        "selected_quote_id": ranking.selected_winner.quote.quote_id if ranking and ranking.selected_winner else None,
    }


@app.post("/api/scenarios/{scenario_id}/run")
def run_scenario_test(scenario_id: str) -> dict[str, Any]:
    """Execute official deterministic test run for the scenario."""
    sc_id = scenario_id.upper()
    if sc_id not in SCENARIO_METADATA:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found.")

    suite = EvaluatorSuite(db_manager)
    res: ScenarioResult = suite.run_scenario(sc_id)
    return res.model_dump()


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


@app.post("/api/rates")
def calculate_rates(req: RateRequest) -> dict[str, Any]:
    """
    Validate inputs, calculate volumetric and billable weights,
    query couriers, rank options, and generate advisor recommendations.
    """
    # 1. Validation
    is_valid_m, field_errs, err_list = validate_shipment_measurements(
        req.length_cm, req.width_cm, req.height_cm, req.parcel_weight_kg
    )
    if not is_valid_m:
        raise HTTPException(status_code=422, detail={"field_errors": field_errs, "errors": err_list})

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
    provider: CourierProvider = get_provider(req.provider_mode)
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

    return {
        "order": order.model_dump(),
        "volumetric_weight_5000": round(vol_div_5000, 2),
        "actual_weight_kg": req.parcel_weight_kg,
        "chargeable_weight_estimated": max(req.parcel_weight_kg, vol_div_5000),
        "quotes": [q.model_dump() for q in quotes],
        "ranking": ranking.model_dump() if ranking else None,
        "advisor": advisor.model_dump() if advisor else None,
        "packaging_analysis": [a.model_dump() for a in pkg_analysis] if pkg_analysis else [],
        "selected_quote_id": ranking.selected_winner.quote.quote_id if ranking and ranking.selected_winner else None,
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

    provider = get_provider(payload.provider_mode)

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
def serve_index():
    index_file = _STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Adaptive Shipping Intelligence Agent</h1><p>Frontend static files initializing...</p>")
    return FileResponse(index_file)

# Mount static folder
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
