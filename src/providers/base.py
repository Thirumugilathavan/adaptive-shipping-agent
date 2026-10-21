"""
CourierProvider Abstract Interface and Protocol.
Decouples domain business logic from specific courier APIs and sandbox adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from src.domain.models import CourierQuote, OrderInput
from src.persistence.state_machine import ApprovalRecord


class TrackingEvent(BaseModel):
    """Single checkpoint in a shipment's transit lifecycle."""
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of event")
    status: str = Field(..., description="Standardized event code (e.g. IN_TRANSIT)")
    location: str = Field(..., description="City or facility name")
    description: str = Field(..., description="Human-readable event summary")


class TrackingResult(BaseModel):
    """Complete tracking status response with immutable audit fields."""
    tracking_number: str
    courier_code: str
    courier_name: str
    current_status: str
    is_delivered: bool = False
    estimated_delivery: Optional[str] = None
    events: list[TrackingEvent] = Field(default_factory=list)
    is_demo_data: bool = True
    missing_evidence: list[str] = Field(default_factory=list)
    disclaimer: str = "DEMO TRACKING: Simulated status history for demonstration only."


class PickupResult(BaseModel):
    """Result of a carrier pickup scheduling operation."""
    pickup_reference: str
    order_reference: str
    courier_code: str
    scheduled_date: str
    time_window: str
    pickup_address_summary: str
    status: str = "CONFIRMED"  # 'CONFIRMED', 'PENDING', 'FAILED'
    is_demo_data: bool = True
    disclaimer: str = "DEMO PICKUP: Simulated pickup scheduling confirmation."
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ShipmentResult(BaseModel):
    """Outcome of a shipment creation request."""
    shipment_id: str
    order_reference: str
    courier_code: str
    tracking_number: str
    label_status: str = Field(default="UNSUPPORTED", description="'UNSUPPORTED', 'AVAILABLE_URL', or 'AVAILABLE_PDF'")
    label_url_or_data: Optional[str] = None
    is_demo_data: bool = True
    disclaimer: str = "DEMO SHIPMENT: Simulated booking reference for demonstration only."
    raw_response: dict[str, Any] = Field(default_factory=dict)


class CourierProvider(ABC):
    """
    Abstract Base Class for all shipping providers and sandbox adapters.
    Ensures uniform contracts for rate discovery, booking, and tracking.
    """

    @property
    @abstractmethod
    def provider_code(self) -> str:
        """Machine-readable provider identifier (e.g., 'DEMO_ADAPTER')."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider label (e.g., 'Demonstration Shipping Adapter')."""
        pass

    @property
    @abstractmethod
    def is_demo(self) -> bool:
        """Returns True if this provider emits synthetic demonstration data."""
        pass

    @abstractmethod
    def get_rates(self, order: OrderInput) -> list[CourierQuote]:
        """
        Query rates and serviceability for all available couriers under this provider.
        Must return consistent CourierQuote models with transparent fee breakdowns.
        Unserviceable couriers must be marked with is_serviceable=False and is_eligible=False.
        """
        pass

    @abstractmethod
    def create_shipment(
        self,
        order: OrderInput,
        quote: CourierQuote,
        approval: ApprovalRecord,
        idempotency_key: str,
    ) -> ShipmentResult:
        """
        Dispatch a shipment creation request with strict approval and idempotency controls.
        """
        pass

    @abstractmethod
    def get_tracking(self, tracking_number: str) -> TrackingResult:
        """
        Fetch tracking history and current delivery status.
        """
        pass

    @abstractmethod
    def cancel_shipment(self, shipment_id: str) -> bool:
        """
        Cancel a previously created shipment if supported.
        """
        pass

    def schedule_pickup(self, shipment_id: str) -> Optional[PickupResult]:
        """
        Schedule a package pickup for the specified shipment.
        Default implementation returns None if provider does not support pickup scheduling.
        """
        return None

    def generate_label(self, shipment_id: str) -> Optional[str]:
        """
        Generate or fetch shipping label printable data (HTML/SVG/URL/base64).
        Default implementation returns None if provider does not support labels.
        """
        return None

    def lookup_shipment_by_reference(self, order_reference: str) -> Optional[ShipmentResult]:
        """
        Query provider for an existing shipment created under this order reference.
        Used for safe post-timeout reconciliation.
        Default implementation returns None if provider does not support reference-based lookup.
        """
        return None
