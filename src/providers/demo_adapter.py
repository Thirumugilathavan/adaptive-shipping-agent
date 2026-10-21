"""
Explicitly Labeled Demonstration Shipping Adapter.
Provides realistic, deterministic, multi-courier synthetic quotes,
serviceability checks, and simulated tracking without connecting to live APIs.

ALL OUTPUTS ARE PROMINENTLY MARKED AS DEMO DATA.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel
from src.domain.models import (
    CourierQuote,
    OrderInput,
    PackageType,
    VolumetricRule,
)
from src.domain.dimension_engine import (
    CourierCapabilityRule,
    CourierEligibilityReport,
    EligibilityStatus,
    RuleVerificationStatus,
    evaluate_courier_dimension_eligibility,
    OFFICIAL_DOCUMENTED_CAPABILITIES,
)
from src.domain.pricing import CourierPricingRule, calculate_landed_cost
from src.domain.weight import compute_weight_breakdown
from src.persistence.state_machine import ApprovalRecord
from src.providers.base import (
    CourierProvider,
    PickupResult,
    ShipmentResult,
    TrackingEvent,
    TrackingResult,
)


class DemoCourierConfig(BaseModel):
    """Configuration specification for a simulated demo courier."""
    courier_code: str
    courier_name: str
    service_type: str
    pricing_rule: CourierPricingRule
    volumetric_rule: VolumetricRule
    max_weight_kg: float = 100.0
    max_length_cm: Optional[float] = None
    max_girth_cm: Optional[float] = None
    max_single_dimension_cm: Optional[float] = None
    max_length_plus_girth_cm: Optional[float] = None
    allowed_package_types: list[PackageType] = list(PackageType)
    max_cod_amount: float = 50000.0
    blocked_destination_pin_prefixes: list[str] = []
    blocked_destination_pin_suffixes: list[str] = []
    evidence_source: str = "Documented Carrier Guidelines"
    evidence_url: Optional[str] = None
    rule_status: str = "VERIFIED"
    market_scope: str = "India Domestic"


# Core three couriers for baseline test compatibility
CORE_DEMO_COURIERS: list[DemoCourierConfig] = [
    # 1. Air Express Priority
    DemoCourierConfig(
        courier_code="DEMO_AIR_EXPRESS",
        courier_name="Demo Air Express (Simulated)",
        service_type="Air Express Priority",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_AIR_EXPRESS",
            courier_name="Demo Air Express (Simulated)",
            service_type="Air Express Priority",
            first_slab_kg=0.5,
            first_slab_rate=70.0,
            additional_slab_rate=45.0,
            fuel_surcharge_pct=12.0,
            cod_fixed_fee=50.0,
            cod_percentage_rate=2.5,
            min_cod_fee=50.0,
            handling_fee=15.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=2,
        ),
        volumetric_rule=VolumetricRule(
            divisor=5000.0,  # Standard air freight divisor
            min_billable_weight_kg=0.5,
            billing_step_kg=0.5,
        ),
        max_weight_kg=30.0,
        max_length_cm=120.0,
        max_single_dimension_cm=120.0,
        max_girth_cm=300.0,
        max_length_plus_girth_cm=300.0,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE, PackageType.CUSTOM, PackageType.POLYBAG],
        blocked_destination_pin_prefixes=["79"],  # Remote region
        evidence_source="Blue Dart Domestic Priority & Delhivery Air Express Documentation",
        evidence_url="https://www.bluedart.com/conditions",
        rule_status="VERIFIED",
    ),
    # 2. Surface Ground Economy
    DemoCourierConfig(
        courier_code="DEMO_SURFACE_STD",
        courier_name="Demo Surface Standard (Simulated)",
        service_type="Surface Ground Economy",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_SURFACE_STD",
            courier_name="Demo Surface Standard (Simulated)",
            service_type="Surface Ground Economy",
            first_slab_kg=1.0,
            first_slab_rate=50.0,
            additional_slab_rate=30.0,
            fuel_surcharge_pct=8.0,
            cod_fixed_fee=40.0,
            cod_percentage_rate=2.0,
            min_cod_fee=40.0,
            handling_fee=10.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=5,
        ),
        volumetric_rule=VolumetricRule(
            divisor=4500.0,
            min_billable_weight_kg=1.0,
            billing_step_kg=1.0,
        ),
        max_weight_kg=100.0,
        max_length_cm=150.0,
        max_single_dimension_cm=150.0,
        max_girth_cm=350.0,
        max_length_plus_girth_cm=350.0,
        allowed_package_types=list(PackageType),
        blocked_destination_pin_suffixes=["999"],
        evidence_source="Delhivery Surface Network Terms & Blue Dart Surface Guidelines",
        evidence_url="https://help.delhivery.com/docs/calculating-volumetric-weight",
        rule_status="VERIFIED",
    ),
    # 3. Heavy Commercial Freight
    DemoCourierConfig(
        courier_code="DEMO_ECONOMY_CARGO",
        courier_name="Demo Heavy Cargo (Simulated)",
        service_type="Heavy Commercial Freight",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_ECONOMY_CARGO",
            courier_name="Demo Heavy Cargo (Simulated)",
            service_type="Heavy Commercial Freight",
            first_slab_kg=5.0,
            first_slab_rate=180.0,
            additional_slab_rate=80.0,
            fuel_surcharge_pct=6.0,
            cod_fixed_fee=60.0,
            cod_percentage_rate=1.5,
            min_cod_fee=60.0,
            handling_fee=25.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=7,
        ),
        volumetric_rule=VolumetricRule(
            divisor=4000.0,
            min_billable_weight_kg=5.0,
            billing_step_kg=5.0,
        ),
        max_weight_kg=500.0,
        max_length_cm=250.0,
        max_single_dimension_cm=250.0,
        max_girth_cm=500.0,
        max_length_plus_girth_cm=500.0,
        allowed_package_types=[PackageType.BOX, PackageType.WOODEN_CRATE, PackageType.CUSTOM_CRATE, PackageType.CUSTOM],
        max_cod_amount=10000.0,
        evidence_source="Shiprocket Heavy / B2B Commercial Freight Specifications",
        evidence_url="https://support.shiprocket.in/support/solutions/articles/43000664201-explain-the-different-types-of-weight-terminology-used-on-the-platform-",
        rule_status="VERIFIED",
    ),
]

# Additional 7 couriers to provide 10 distinct options in the application
EXTENDED_DEMO_COURIERS: list[DemoCourierConfig] = [
    # 4. Same-Day Metro Express
    DemoCourierConfig(
        courier_code="DEMO_SAME_DAY",
        courier_name="Demo Flash Same-Day (Simulated)",
        service_type="Same-Day Metro Delivery",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_SAME_DAY",
            courier_name="Demo Flash Same-Day (Simulated)",
            service_type="Same-Day Metro Delivery",
            first_slab_kg=0.5,
            first_slab_rate=130.0,
            additional_slab_rate=65.0,
            fuel_surcharge_pct=15.0,
            cod_fixed_fee=70.0,
            cod_percentage_rate=3.0,
            min_cod_fee=70.0,
            handling_fee=25.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=1,
        ),
        volumetric_rule=VolumetricRule(
            divisor=5000.0,
            min_billable_weight_kg=0.5,
            billing_step_kg=0.5,
        ),
        max_weight_kg=15.0,
        max_length_cm=60.0,
        max_girth_cm=180.0,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE],
        blocked_destination_pin_prefixes=["7", "8"],
        max_cod_amount=25000.0,
    ),
    # 5. Budget Saver Surface
    DemoCourierConfig(
        courier_code="DEMO_BUDGET_SAVER",
        courier_name="Demo Budget Saver (Simulated)",
        service_type="Budget Surface Economy",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_BUDGET_SAVER",
            courier_name="Demo Budget Saver (Simulated)",
            service_type="Budget Surface Economy",
            first_slab_kg=1.0,
            first_slab_rate=52.0,
            additional_slab_rate=32.0,
            fuel_surcharge_pct=8.0,
            cod_fixed_fee=40.0,
            cod_percentage_rate=2.0,
            min_cod_fee=40.0,
            handling_fee=10.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=8,
        ),
        volumetric_rule=VolumetricRule(
            divisor=4500.0,
            min_billable_weight_kg=1.0,
            billing_step_kg=1.0,
        ),
        max_weight_kg=40.0,
        max_length_cm=120.0,
        max_girth_cm=300.0,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER],
        max_cod_amount=15000.0,
    ),
    # 6. Premium Overnight Express
    DemoCourierConfig(
        courier_code="DEMO_OVERNIGHT",
        courier_name="Demo NightJet Overnight (Simulated)",
        service_type="Overnight Priority Air",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_OVERNIGHT",
            courier_name="Demo NightJet Overnight (Simulated)",
            service_type="Overnight Priority Air",
            first_slab_kg=0.5,
            first_slab_rate=110.0,
            additional_slab_rate=55.0,
            fuel_surcharge_pct=14.0,
            cod_fixed_fee=65.0,
            cod_percentage_rate=2.8,
            min_cod_fee=65.0,
            handling_fee=20.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=1,
        ),
        volumetric_rule=VolumetricRule(
            divisor=5000.0,
            min_billable_weight_kg=0.5,
            billing_step_kg=0.5,
        ),
        max_weight_kg=25.0,
        max_length_cm=100.0,
        max_girth_cm=250.0,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE, PackageType.CUSTOM],
        blocked_destination_pin_prefixes=["79"],
    ),
    # 7. Regional Road Express
    DemoCourierConfig(
        courier_code="DEMO_REGIONAL_ROAD",
        courier_name="Demo Regional Road Express (Simulated)",
        service_type="Regional Highway Express",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_REGIONAL_ROAD",
            courier_name="Demo Regional Road Express (Simulated)",
            service_type="Regional Highway Express",
            first_slab_kg=1.5,
            first_slab_rate=58.0,
            additional_slab_rate=32.0,
            fuel_surcharge_pct=9.0,
            cod_fixed_fee=42.0,
            cod_percentage_rate=2.0,
            min_cod_fee=42.0,
            handling_fee=12.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=3,
        ),
        volumetric_rule=VolumetricRule(
            divisor=4500.0,
            min_billable_weight_kg=1.5,
            billing_step_kg=1.0,
        ),
        max_weight_kg=120.0,
        max_length_cm=180.0,
        max_girth_cm=400.0,
        allowed_package_types=list(PackageType),
        max_cod_amount=40000.0,
    ),
    # 8. B2B Commercial Logistics
    DemoCourierConfig(
        courier_code="DEMO_B2B_LOGISTICS",
        courier_name="Demo B2B Bulk Logistics (Simulated)",
        service_type="B2B Scheduled Cargo",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_B2B_LOGISTICS",
            courier_name="Demo B2B Bulk Logistics (Simulated)",
            service_type="B2B Scheduled Cargo",
            first_slab_kg=10.0,
            first_slab_rate=260.0,
            additional_slab_rate=85.0,
            fuel_surcharge_pct=7.0,
            cod_fixed_fee=0.0,
            cod_percentage_rate=0.0,
            min_cod_fee=0.0,
            handling_fee=40.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=4,
        ),
        volumetric_rule=VolumetricRule(
            divisor=4000.0,
            min_billable_weight_kg=10.0,
            billing_step_kg=5.0,
        ),
        max_weight_kg=500.0,
        max_length_cm=300.0,
        max_girth_cm=600.0,
        allowed_package_types=[PackageType.BOX, PackageType.WOODEN_CRATE, PackageType.CUSTOM],
        max_cod_amount=0.0,  # No COD for B2B
    ),
    # 9. Smart Parcel Connect (No max_length_cm configured -> unverified size check)
    DemoCourierConfig(
        courier_code="DEMO_SMART_PARCEL",
        courier_name="Demo Smart Parcel Connect (Simulated)",
        service_type="Standard Parcel Delivery",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_SMART_PARCEL",
            courier_name="Demo Smart Parcel Connect (Simulated)",
            service_type="Standard Parcel Delivery",
            first_slab_kg=0.5,
            first_slab_rate=65.0,
            additional_slab_rate=38.0,
            fuel_surcharge_pct=10.0,
            cod_fixed_fee=45.0,
            cod_percentage_rate=2.1,
            min_cod_fee=45.0,
            handling_fee=14.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=4,
        ),
        volumetric_rule=VolumetricRule(
            divisor=5000.0,
            min_billable_weight_kg=0.5,
            billing_step_kg=0.5,
        ),
        max_weight_kg=50.0,
        max_length_cm=None,
        max_girth_cm=None,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE, PackageType.CUSTOM],
        max_cod_amount=30000.0,
    ),
    # 10. Express Gold Tier (No max_length_cm configured -> unverified size check)
    DemoCourierConfig(
        courier_code="DEMO_EXPRESS_GOLD",
        courier_name="Demo Express Gold Line (Simulated)",
        service_type="Premium Fast Track",
        pricing_rule=CourierPricingRule(
            courier_code="DEMO_EXPRESS_GOLD",
            courier_name="Demo Express Gold Line (Simulated)",
            service_type="Premium Fast Track",
            first_slab_kg=0.5,
            first_slab_rate=85.0,
            additional_slab_rate=48.0,
            fuel_surcharge_pct=11.0,
            cod_fixed_fee=55.0,
            cod_percentage_rate=2.4,
            min_cod_fee=55.0,
            handling_fee=18.0,
            gst_tax_rate_pct=18.0,
            estimated_delivery_days=2,
        ),
        volumetric_rule=VolumetricRule(
            divisor=5000.0,
            min_billable_weight_kg=0.5,
            billing_step_kg=0.5,
        ),
        max_weight_kg=35.0,
        max_length_cm=None,
        max_girth_cm=None,
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE, PackageType.CUSTOM],
        max_cod_amount=45000.0,
    ),
]

# Combined 10 couriers
ALL_DEMO_COURIERS: list[DemoCourierConfig] = CORE_DEMO_COURIERS + EXTENDED_DEMO_COURIERS

# Backward compatibility alias
DEMO_COURIERS: list[DemoCourierConfig] = CORE_DEMO_COURIERS


class DemoShippingAdapter(CourierProvider):
    """
    Simulated carrier adapter providing transparent, deterministic quotes
    and safe lifecycle execution with prominent synthetic data disclosures.
    """

    def __init__(self, couriers: Optional[list[DemoCourierConfig]] = None, use_extended: bool = False):
        if couriers is not None:
            self._couriers = couriers
        elif use_extended or os.getenv("SHIPPING_DEMO_EXTENDED", "0") == "1":
            self._couriers = ALL_DEMO_COURIERS
        else:
            self._couriers = CORE_DEMO_COURIERS
        self._created_shipments: dict[str, ShipmentResult] = {}

    @property
    def provider_code(self) -> str:
        return "DEMO_SHIPPING_ADAPTER"

    @property
    def provider_name(self) -> str:
        return "Demonstration Multi-Courier Adapter (Simulated)"

    @property
    def is_demo(self) -> bool:
        return True

    def _generate_input_hash(self, order: OrderInput) -> str:
        raw = (
            f"{order.order_reference}:{order.dimensions.volume_cm3}:"
            f"{order.parcel_weight_kg}:{order.sender.pin_code}:"
            f"{order.recipient.pin_code}:{order.package_type.value}:"
            f"{order.is_cod}:{order.cod_amount}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _to_capability_rule(self, config: DemoCourierConfig) -> CourierCapabilityRule:
        """Convert DemoCourierConfig to unified CourierCapabilityRule."""
        doc_cap = OFFICIAL_DOCUMENTED_CAPABILITIES.get(config.courier_code)
        ev_source = config.evidence_source
        ev_url = config.evidence_url
        r_status = RuleVerificationStatus.VERIFIED

        if doc_cap:
            ev_source = doc_cap.evidence_source
            ev_url = doc_cap.evidence_url
            r_status = doc_cap.rule_status

        if config.max_length_cm is None and config.max_girth_cm is None and config.courier_code in ("DEMO_SMART_PARCEL", "DEMO_EXPRESS_GOLD", "DEMO_REGIONAL_UNVERIFIED"):
            r_status = RuleVerificationStatus.UNVERIFIED
            ev_source = "Regional Partner Guidelines (Size Restrictions Unverified)"
            ev_url = None

        return CourierCapabilityRule(
            courier_code=config.courier_code,
            courier_name=config.courier_name,
            service_type=config.service_type,
            max_length_cm=config.max_length_cm,
            max_single_dimension_cm=config.max_single_dimension_cm or config.max_length_cm,
            max_girth_cm=config.max_girth_cm,
            max_length_plus_girth_cm=config.max_length_plus_girth_cm or config.max_girth_cm,
            max_actual_weight_kg=config.max_weight_kg,
            allowed_package_types=config.allowed_package_types,
            volumetric_rule=config.volumetric_rule,
            max_cod_amount=config.max_cod_amount,
            blocked_destination_pin_prefixes=config.blocked_destination_pin_prefixes,
            blocked_destination_pin_suffixes=config.blocked_destination_pin_suffixes,
            evidence_source=ev_source,
            evidence_url=ev_url,
            retrieval_timestamp="2026-10-21T00:00:00Z",
            rule_status=r_status,
            market_scope=config.market_scope,
        )

    def _evaluate_serviceability(
        self,
        config: DemoCourierConfig,
        order: OrderInput,
    ) -> tuple[bool, Optional[str], Optional[CourierEligibilityReport]]:
        """
        Check all operational and network serviceability constraints using the shared dimension engine.
        Returns (is_serviceable, rejection_reason, report).
        """
        cap = self._to_capability_rule(config)
        report = evaluate_courier_dimension_eligibility(
            outer_dims=order.dimensions,
            gross_weight_kg=order.parcel_weight_kg,
            package_type=order.package_type,
            capability=cap,
            is_cod=order.is_cod,
            cod_amount=order.cod_amount,
            dest_pin=order.recipient.pin_code,
        )

        if report.status == EligibilityStatus.INELIGIBLE:
            reason = report.rejection_reasons[0] if report.rejection_reasons else "Courier ineligible"
            return False, reason, report

        if report.status == EligibilityStatus.UNVERIFIED:
            # Serviceable route, but sizing limits unverified
            reason = report.unverified_reasons[0] if report.unverified_reasons else "Courier size eligibility not verified"
            return True, reason, report

        return True, None, report

    def get_rates(self, order: OrderInput) -> list[CourierQuote]:
        """
        Evaluate rates across all configured demo couriers.
        Never sets unserviceable courier prices silently to zero.
        """
        quotes: list[CourierQuote] = []
        input_hash = self._generate_input_hash(order)
        now_iso = datetime.now(timezone.utc).isoformat()

        for config in self._couriers:
            is_serviceable, rejection_reason, report = self._evaluate_serviceability(config, order)
            quote_id = f"QUOTE-DEMO-{config.courier_code}-{input_hash[:8]}"
            ev_source = report.evidence_source if report else config.evidence_source
            ev_url = report.evidence_url if report else config.evidence_url

            if not is_serviceable:
                # Mark as unserviceable and ineligible; NO fake zero pricing
                quotes.append(
                    CourierQuote(
                        quote_id=quote_id,
                        courier_code=config.courier_code,
                        courier_name=config.courier_name,
                        service_type=config.service_type,
                        estimated_delivery_days=None,
                        is_serviceable=False,
                        is_eligible=False,
                        eligibility_status="INELIGIBLE",
                        evidence_source=ev_source,
                        evidence_url=ev_url,
                        eligibility_reasons=report.rejection_reasons if report else [rejection_reason or "Ineligible"],
                        unserviceable_reason=rejection_reason,
                        weight_result=None,
                        cost_breakdown=None,  # Never treat as zero
                        retrieval_timestamp=now_iso,
                        source_type="DEMO_ADAPTER",
                        source_id=self.provider_code,
                        input_hash=input_hash,
                        decision_explanation=f"Courier ineligible: {rejection_reason}",
                        is_demo_data=True,
                        missing_evidence=[
                            "Route unserviceable or size restriction violated",
                            "Simulated demonstration rate",
                        ],
                    )
                )
                continue

            # Compute weight using courier-specific volumetric rule
            weight_res = compute_weight_breakdown(
                parcel_weight_kg=order.parcel_weight_kg,
                dimensions=order.dimensions,
                rule=config.volumetric_rule,
            )

            # Compute landed cost using domain pricing engine
            cost = calculate_landed_cost(
                weight_result=weight_res,
                pricing_rule=config.pricing_rule,
                is_cod=order.is_cod,
                cod_amount=order.cod_amount,
                billing_step_kg=config.volumetric_rule.billing_step_kg,
            )

            is_unverified = (report is not None and report.status == EligibilityStatus.UNVERIFIED)
            el_status = "UNVERIFIED" if is_unverified else "ELIGIBLE"
            is_el = not is_unverified

            explanation = (
                f"{config.service_type}: Base freight ₹{cost.base_freight:.2f} for {weight_res.billing_slab_kg} kg "
                f"(Vol. Divisor: {config.volumetric_rule.divisor:.0f}) + {cost.fuel_surcharge_rate_pct}% fuel + "
                f"₹{cost.cod_fee:.2f} COD fee + 18% GST. Estimated SLA: {config.pricing_rule.estimated_delivery_days} days."
            )
            if is_unverified:
                explanation = f"[Eligibility Unverified] {explanation}"

            quotes.append(
                CourierQuote(
                    quote_id=quote_id,
                    courier_code=config.courier_code,
                    courier_name=config.courier_name,
                    service_type=config.service_type,
                    estimated_delivery_days=config.pricing_rule.estimated_delivery_days,
                    is_serviceable=True,
                    is_eligible=is_el,
                    eligibility_status=el_status,
                    evidence_source=ev_source,
                    evidence_url=ev_url,
                    unverified_reasons=report.unverified_reasons if report else [],
                    unserviceable_reason=rejection_reason if is_unverified else None,
                    weight_result=weight_res,
                    cost_breakdown=cost,
                    retrieval_timestamp=now_iso,
                    source_type="DEMO_ADAPTER",
                    source_id=self.provider_code,
                    input_hash=input_hash,
                    decision_explanation=explanation,
                    is_demo_data=True,
                    missing_evidence=(
                        ["Courier maximum parcel dimension limits not documented in provider guidelines"]
                        if is_unverified else [
                            "Simulated rate from demo adapter (live carrier API unconfigured)",
                            "Carrier SLA estimate is synthetic",
                        ]
                    ),
                )
            )

        return quotes

    def create_shipment(
        self,
        order: OrderInput,
        quote: CourierQuote,
        approval: ApprovalRecord,
        idempotency_key: str,
        generate_label: bool = False,
    ) -> ShipmentResult:
        """
        Simulate shipment dispatch with strict idempotency and approval validation.
        """
        if not quote.is_serviceable or not quote.is_eligible:
            raise ValueError(f"Cannot create shipment with an ineligible quote: {quote.unserviceable_reason}")

        # Deterministic synthetic tracking number derived from order reference and courier
        hash_seed = f"{order.order_reference}:{quote.courier_code}:{idempotency_key[:8]}"
        tracking_num = f"DEMO-TRK-{hashlib.sha256(hash_seed.encode()).hexdigest()[:10].upper()}"
        shipment_id = f"DEMO-SHP-{order.order_reference}-{quote.courier_code}"

        # Store order and quote references for label and pickup generation
        if not hasattr(self, "_shipment_orders"):
            self._shipment_orders = {}
        if not hasattr(self, "_shipment_quotes"):
            self._shipment_quotes = {}
        if not hasattr(self, "_scheduled_pickups"):
            self._scheduled_pickups = {}

        self._shipment_orders[shipment_id] = order
        self._shipment_quotes[shipment_id] = quote

        # Support label generation when requested
        label_html = self.generate_label(shipment_id, tracking_number=tracking_num, order=order, quote=quote) if generate_label else None

        res = ShipmentResult(
            shipment_id=shipment_id,
            order_reference=order.order_reference,
            courier_code=quote.courier_code,
            tracking_number=tracking_num,
            label_status="AVAILABLE_HTML" if label_html else "UNSUPPORTED",
            label_url_or_data=label_html,
            is_demo_data=True,
            disclaimer="DEMO SHIPMENT: Simulated booking reference for demonstration only.",
            raw_response={
                "status": "SUCCESS_SIMULATED",
                "provider": self.provider_code,
                "approved_by": approval.approved_by,
                "approved_at": approval.approved_at,
                "idempotency_key_acknowledged": idempotency_key,
                "note": "Simulated booking" + (" with printable demo label" if label_html else ""),
            },
        )
        self._created_shipments[order.order_reference] = res
        return res

    def generate_label(
        self,
        shipment_id: str,
        tracking_number: Optional[str] = None,
        order: Optional[OrderInput] = None,
        quote: Optional[CourierQuote] = None,
    ) -> Optional[str]:
        """
        Generate a complete, printable HTML demo shipping label with clear synthetic disclosure.
        """
        ord_obj = order or getattr(self, "_shipment_orders", {}).get(shipment_id)
        q_obj = quote or getattr(self, "_shipment_quotes", {}).get(shipment_id)

        # Lookup from created shipments if not passed
        if ord_obj is None:
            for ref, shp in self._created_shipments.items():
                if shp.shipment_id == shipment_id:
                    tracking_number = shp.tracking_number
                    break

        trk = tracking_number or "DEMO-TRK-UNKNOWN"
        courier_name = q_obj.courier_name if q_obj else "Demo Carrier Network"
        service_type = q_obj.service_type if q_obj else "Standard Express"
        weight_str = f"{ord_obj.parcel_weight_kg:.2f} kg" if ord_obj else "— kg"
        dims_str = (
            f"{ord_obj.dimensions.length_cm}×{ord_obj.dimensions.width_cm}×{ord_obj.dimensions.height_cm} cm"
            if ord_obj else "— cm"
        )
        order_ref = ord_obj.order_reference if ord_obj else "—"

        s_name = ord_obj.sender.name if ord_obj else "Sender Facility"
        s_city = f"{ord_obj.sender.city}, {ord_obj.sender.state} - {ord_obj.sender.pin_code}" if ord_obj else "—"
        r_name = ord_obj.recipient.name if ord_obj else "Recipient Destination"
        r_street = ord_obj.recipient.street_line1 if ord_obj else "—"
        r_city = f"{ord_obj.recipient.city}, {ord_obj.recipient.state} - {ord_obj.recipient.pin_code}" if ord_obj else "—"
        r_phone = ord_obj.recipient.phone if ord_obj else "—"

        cod_banner = ""
        if ord_obj and ord_obj.is_cod:
            cod_banner = f"""
            <div style="background:#fee2e2;border:2px solid #ef4444;border-radius:4px;padding:6px;text-align:center;margin-top:8px;">
                <span style="color:#b91c1c;font-weight:900;font-size:14px;">COLLECT CASH ON DELIVERY: ₹{ord_obj.cod_amount:,.2f}</span>
            </div>
            """
        else:
            cod_banner = """
            <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:4px;padding:4px;text-align:center;margin-top:8px;">
                <span style="color:#15803d;font-weight:700;font-size:12px;">PREPAID — DO NOT COLLECT CASH</span>
            </div>
            """

        label_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Shipping Label - {trk}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; margin: 0; padding: 12px; background: #fff; }}
  .label-container {{ border: 2.5px solid #000; width: 380px; padding: 14px; box-sizing: border-box; background: #fff; position: relative; margin: 0 auto; }}
  .watermark {{ position: absolute; top: 38%; left: 10%; font-size: 34px; font-weight: 900; color: rgba(220, 38, 38, 0.16); transform: rotate(-30deg); pointer-events: none; text-align: center; text-transform: uppercase; border: 4px dashed rgba(220, 38, 38, 0.2); padding: 6px 12px; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #000; padding-bottom: 8px; margin-bottom: 8px; }}
  .courier-title {{ font-size: 16px; font-weight: 800; }}
  .service-badge {{ font-size: 11px; font-weight: 700; background: #000; color: #fff; padding: 2px 6px; border-radius: 3px; }}
  .barcode-box {{ text-align: center; padding: 8px 0; border-bottom: 2px solid #000; }}
  .barcode {{ font-family: "Courier New", monospace; font-size: 26px; font-weight: 900; letter-spacing: 4px; border-top: 1px dashed #666; border-bottom: 1px dashed #666; padding: 4px 0; }}
  .trk-text {{ font-size: 12px; font-weight: 700; margin-top: 4px; }}
  .grid {{ display: flex; border-bottom: 1.5px solid #000; padding: 6px 0; font-size: 11px; }}
  .grid-col {{ flex: 1; }}
  .dest-box {{ border-bottom: 2px solid #000; padding: 8px 0; }}
  .dest-title {{ font-size: 10px; font-weight: 700; color: #555; text-transform: uppercase; }}
  .dest-name {{ font-size: 14px; font-weight: 800; margin: 2px 0; }}
  .dest-addr {{ font-size: 12px; line-height: 1.35; }}
  .routing {{ display: flex; justify-content: space-between; padding-top: 8px; font-size: 11px; font-weight: 600; }}
  .demo-note {{ margin-top: 8px; font-size: 9px; color: #666; text-align: center; font-style: italic; border-top: 1px dotted #ccc; padding-top: 4px; }}
</style>
</head>
<body>
<div class="label-container">
  <div class="watermark">DEMO LABEL<br>NOT REAL AWB</div>
  <div class="header">
    <div>
      <div class="courier-title">{courier_name}</div>
      <div style="font-size:10px;color:#555;">Ref: {order_ref}</div>
    </div>
    <div class="service-badge">{service_type}</div>
  </div>
  <div class="barcode-box">
    <div class="barcode">||| | |||| | ||||| |||</div>
    <div class="trk-text">AWB: {trk}</div>
  </div>
  <div class="dest-box">
    <div class="dest-title">Ship To (Recipient):</div>
    <div class="dest-name">{r_name}</div>
    <div class="dest-addr">{r_street}<br><strong>{r_city}</strong><br>Contact: {r_phone}</div>
  </div>
  <div class="grid">
    <div class="grid-col">
      <div style="font-size:10px;color:#555;">From (Sender):</div>
      <div style="font-weight:700;">{s_name}</div>
      <div>{s_city}</div>
    </div>
    <div class="grid-col" style="border-left:1px solid #ccc;padding-left:8px;">
      <div style="font-size:10px;color:#555;">Package Details:</div>
      <div>Weight: <strong>{weight_str}</strong></div>
      <div>Dims: {dims_str}</div>
    </div>
  </div>
  {cod_banner}
  <div class="routing">
    <span>ROUTING: {trk[-6:]}</span>
    <span>DISPATCHED: {datetime.now(timezone.utc).strftime("%d-%b-%Y")}</span>
  </div>
  <div class="demo-note">
    🔵 SIMULATED DEMO LABEL: Generated for verification and demonstration. Not a valid commercial carrier label.
  </div>
</div>
</body>
</html>"""
        return label_html

    def schedule_pickup(self, shipment_id: str) -> Optional[PickupResult]:
        """
        Schedule simulated carrier pickup for the given shipment.
        """
        if not hasattr(self, "_scheduled_pickups"):
            self._scheduled_pickups = {}

        # If already scheduled, return existing record
        if shipment_id in self._scheduled_pickups:
            return self._scheduled_pickups[shipment_id]

        ord_obj = getattr(self, "_shipment_orders", {}).get(shipment_id)
        order_ref = ord_obj.order_reference if ord_obj else shipment_id
        courier_code = "DEMO_COURIER"

        # Determine courier code from shipment if found
        for ref, shp in self._created_shipments.items():
            if shp.shipment_id == shipment_id:
                courier_code = shp.courier_code
                break

        now_dt = datetime.now(timezone.utc)
        pickup_ref = f"DEMO-PKP-{hashlib.sha256(f'{shipment_id}:{now_dt.isoformat()}'.encode()).hexdigest()[:8].upper()}"
        scheduled_date = (now_dt).strftime("%Y-%m-%d")
        addr_summary = f"{ord_obj.sender.city} ({ord_obj.sender.pin_code})" if ord_obj else "Sender Facility"

        pickup = PickupResult(
            pickup_reference=pickup_ref,
            order_reference=order_ref,
            courier_code=courier_code,
            scheduled_date=scheduled_date,
            time_window="10:00 AM - 01:00 PM IST",
            pickup_address_summary=addr_summary,
            status="CONFIRMED",
            is_demo_data=True,
            disclaimer="DEMO PICKUP: Simulated pickup scheduling confirmation.",
            raw_response={
                "status": "CONFIRMED",
                "pickup_reference": pickup_ref,
                "shipment_id": shipment_id,
                "window": "10:00 AM - 01:00 PM IST",
            },
        )
        self._scheduled_pickups[shipment_id] = pickup
        return pickup

    def lookup_shipment_by_reference(self, order_reference: str) -> Optional[ShipmentResult]:
        """Lookup previously created shipment by order reference for safe reconciliation."""
        return self._created_shipments.get(order_reference)

    def get_tracking(self, tracking_number: str) -> TrackingResult:
        """
        Generate deterministic simulated tracking events for demonstration.
        """
        now = datetime.now(timezone.utc).isoformat()
        events = [
            TrackingEvent(
                timestamp=now,
                status="MANIFEST_CREATED",
                location="Origin Warehouse Facility",
                description="Shipping manifest registered and electronic billing data received [SIMULATED]",
            ),
            TrackingEvent(
                timestamp=now,
                status="PACKAGE_RECEIVED",
                location="Sorting Hub",
                description="Parcel scanned at initial consolidation facility [SIMULATED]",
            ),
            TrackingEvent(
                timestamp=now,
                status="IN_TRANSIT",
                location="En Route",
                description="Package dispatched to destination distribution center [SIMULATED]",
            ),
        ]

        return TrackingResult(
            tracking_number=tracking_number,
            courier_code="DEMO_COURIER",
            courier_name="Demo Carrier Network",
            current_status="IN_TRANSIT",
            is_delivered=False,
            estimated_delivery="Within 2-5 business days [SIMULATED]",
            events=events,
            is_demo_data=True,
            missing_evidence=["Live carrier tracking webhook unconfigured; displaying simulated checkpoint stream"],
            disclaimer="DEMO TRACKING: Simulated status history for demonstration only.",
        )

    def cancel_shipment(self, shipment_id: str) -> bool:
        """Simulate cancellation."""
        return True
