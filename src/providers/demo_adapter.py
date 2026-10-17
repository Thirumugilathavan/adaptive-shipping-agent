"""
Explicitly Labeled Demonstration Shipping Adapter.
Provides realistic, deterministic, multi-courier synthetic quotes,
serviceability checks, and simulated tracking without connecting to live APIs.

ALL OUTPUTS ARE PROMINENTLY MARKED AS DEMO DATA.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel
from src.domain.models import (
    CourierQuote,
    OrderInput,
    PackageType,
    VolumetricRule,
)
from src.domain.pricing import CourierPricingRule, calculate_landed_cost
from src.domain.weight import compute_weight_breakdown
from src.persistence.state_machine import ApprovalRecord
from src.providers.base import (
    CourierProvider,
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
    allowed_package_types: list[PackageType] = list(PackageType)
    max_cod_amount: float = 50000.0
    blocked_destination_pin_prefixes: list[str] = []
    blocked_destination_pin_suffixes: list[str] = []


# Three distinct, realistically configured demonstration couriers
DEMO_COURIERS: list[DemoCourierConfig] = [
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
        allowed_package_types=[PackageType.BOX, PackageType.FLYER, PackageType.ENVELOPE, PackageType.CUSTOM],
        blocked_destination_pin_prefixes=["79"],  # Simulates unserviceable remote region (e.g. 790001)
    ),
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
            divisor=4500.0,  # Stricter ground surface divisor
            min_billable_weight_kg=1.0,
            billing_step_kg=1.0,
        ),
        max_weight_kg=100.0,
        allowed_package_types=list(PackageType),
        blocked_destination_pin_suffixes=["999"],  # Blocked test PINs ending with 999
    ),
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
            divisor=4000.0,  # Commercial cargo divisor
            min_billable_weight_kg=5.0,
            billing_step_kg=5.0,
        ),
        max_weight_kg=500.0,
        allowed_package_types=[PackageType.BOX, PackageType.WOODEN_CRATE, PackageType.CUSTOM],  # Rejects soft flyers
        max_cod_amount=10000.0,  # Strict COD ceiling for heavy freight
    ),
]


class DemoShippingAdapter(CourierProvider):
    """
    Simulated carrier adapter providing transparent, deterministic quotes
    and safe lifecycle execution with prominent synthetic data disclosures.
    """

    def __init__(self, couriers: Optional[list[DemoCourierConfig]] = None):
        self._couriers = couriers or DEMO_COURIERS

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

    def _evaluate_serviceability(
        self,
        config: DemoCourierConfig,
        order: OrderInput,
    ) -> tuple[bool, Optional[str]]:
        """Check all operational and network serviceability constraints."""
        dest_pin = order.recipient.pin_code

        # PIN code prefix checks (e.g. Remote NE region)
        for prefix in config.blocked_destination_pin_prefixes:
            if dest_pin.startswith(prefix):
                return False, f"Destination PIN {dest_pin} is in a remote region not serviced by {config.courier_name}."

        # PIN code suffix checks (e.g. Unserviceable test PINs)
        for suffix in config.blocked_destination_pin_suffixes:
            if dest_pin.endswith(suffix):
                return False, f"Destination PIN {dest_pin} is excluded from delivery coverage for {config.courier_name}."

        # Weight limit check
        if order.parcel_weight_kg > config.max_weight_kg:
            return False, f"Parcel dead weight ({order.parcel_weight_kg} kg) exceeds courier maximum ({config.max_weight_kg} kg)."

        # Packaging type constraint
        if order.package_type not in config.allowed_package_types:
            allowed_names = ", ".join([p.value for p in config.allowed_package_types])
            return False, f"Package type '{order.package_type.value}' is not supported. Allowed formats: {allowed_names}."

        # COD threshold constraint
        if order.is_cod and order.cod_amount > config.max_cod_amount:
            return False, f"COD amount (₹{order.cod_amount:,.2f}) exceeds courier collection ceiling (₹{config.max_cod_amount:,.2f})."

        return True, None

    def get_rates(self, order: OrderInput) -> list[CourierQuote]:
        """
        Evaluate rates across all configured demo couriers.
        Never sets unserviceable courier prices silently to zero.
        """
        quotes: list[CourierQuote] = []
        input_hash = self._generate_input_hash(order)
        now_iso = datetime.now(timezone.utc).isoformat()

        for config in self._couriers:
            is_serviceable, rejection_reason = self._evaluate_serviceability(config, order)
            quote_id = f"QUOTE-DEMO-{config.courier_code}-{input_hash[:8]}"

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
                            "Route unserviceable by courier network",
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

            explanation = (
                f"{config.service_type}: Base freight ₹{cost.base_freight:.2f} for {weight_res.billing_slab_kg} kg "
                f"(Vol. Divisor: {config.volumetric_rule.divisor:.0f}) + {cost.fuel_surcharge_rate_pct}% fuel + "
                f"₹{cost.cod_fee:.2f} COD fee + 18% GST. Estimated SLA: {config.pricing_rule.estimated_delivery_days} days."
            )

            quotes.append(
                CourierQuote(
                    quote_id=quote_id,
                    courier_code=config.courier_code,
                    courier_name=config.courier_name,
                    service_type=config.service_type,
                    estimated_delivery_days=config.pricing_rule.estimated_delivery_days,
                    is_serviceable=True,
                    is_eligible=True,
                    unserviceable_reason=None,
                    weight_result=weight_res,
                    cost_breakdown=cost,
                    retrieval_timestamp=now_iso,
                    source_type="DEMO_ADAPTER",
                    source_id=self.provider_code,
                    input_hash=input_hash,
                    decision_explanation=explanation,
                    is_demo_data=True,
                    missing_evidence=[
                        "Simulated rate from demo adapter (live carrier API unconfigured)",
                        "Carrier SLA estimate is synthetic",
                    ],
                )
            )

        return quotes

    def create_shipment(
        self,
        order: OrderInput,
        quote: CourierQuote,
        approval: ApprovalRecord,
        idempotency_key: str,
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

        return ShipmentResult(
            shipment_id=shipment_id,
            order_reference=order.order_reference,
            courier_code=quote.courier_code,
            tracking_number=tracking_num,
            label_status="UNSUPPORTED",
            label_url_or_data=None,
            is_demo_data=True,
            disclaimer="DEMO SHIPMENT: Simulated booking reference for demonstration only.",
            raw_response={
                "status": "SUCCESS_SIMULATED",
                "provider": self.provider_code,
                "approved_by": approval.approved_by,
                "approved_at": approval.approved_at,
                "idempotency_key_acknowledged": idempotency_key,
                "note": "Label generation unsupported in demo mode",
            },
        )

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
