"""
ShipSandbox Client Shell.
Integrates strictly with verified API endpoints.
Keeps unverified capabilities (rate retrieval, shipment creation, label generation,
pickup scheduling, and webhook parsing) disabled until their specifications are confirmed.
"""

from __future__ import annotations

import os
from typing import Any, Optional
import httpx
from src.domain.models import CourierQuote, OrderInput
from src.persistence.state_machine import ApprovalRecord
from src.providers.base import (
    CourierProvider,
    ShipmentResult,
    TrackingEvent,
    TrackingResult,
)


class UnconfirmedEndpointError(NotImplementedError):
    """
    Raised when an unconfirmed or unverified ShipSandbox endpoint is called.
    Guarantees compliance with the safety policy: do not invent API endpoints.
    """
    pass


class ShipSandboxClient(CourierProvider):
    """
    HTTP client for documented ShipSandbox endpoints.
    Only the tracking endpoint is enabled; all other endpoints are explicitly locked.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ):
        self.base_url = (base_url or os.getenv("SHIPSANDBOX_BASE_URL", "https://api.shipsandbox.example.com")).rstrip("/")
        self.api_key = api_key or os.getenv("SHIPSANDBOX_API_KEY", "")
        self.timeout = timeout_seconds

    @property
    def provider_code(self) -> str:
        return "SHIPSANDBOX_API"

    @property
    def provider_name(self) -> str:
        return "ShipSandbox Documented API Client"

    @property
    def is_demo(self) -> bool:
        return False

    def get_rates(self, order: OrderInput) -> list[CourierQuote]:
        """Disabled: Rate retrieval endpoint not confirmed in specification."""
        raise UnconfirmedEndpointError(
            "ShipSandbox rate retrieval endpoint is not confirmed in verified documentation. "
            "Kept disabled to prevent inventing fictitious courier rates."
        )

    def create_shipment(
        self,
        order: OrderInput,
        quote: CourierQuote,
        approval: ApprovalRecord,
        idempotency_key: str,
    ) -> ShipmentResult:
        """Disabled: Shipment creation endpoint not confirmed in specification."""
        raise UnconfirmedEndpointError(
            "ShipSandbox shipment creation endpoint is not confirmed in verified documentation. "
            "Kept disabled to prevent creating unauthorized or fictitious carrier bookings."
        )

    def download_label(self, shipment_id: str) -> None:
        """Disabled: Label retrieval endpoint not confirmed in specification."""
        raise UnconfirmedEndpointError(
            "ShipSandbox label retrieval endpoint is not confirmed in verified documentation."
        )

    def schedule_pickup(self, shipment_id: str) -> None:
        """Disabled: Pickup scheduling endpoint not confirmed in specification."""
        raise UnconfirmedEndpointError(
            "ShipSandbox pickup scheduling endpoint is not confirmed in verified documentation."
        )

    def verify_webhook(self, payload: bytes, signature_header: str) -> bool:
        """Disabled: Webhook signature algorithm not confirmed in specification."""
        raise UnconfirmedEndpointError(
            "ShipSandbox webhook signature verification algorithm is not confirmed."
        )

    def cancel_shipment(self, shipment_id: str) -> bool:
        """Disabled: Cancellation endpoint not confirmed in specification."""
        raise UnconfirmedEndpointError(
            "ShipSandbox shipment cancellation endpoint is not confirmed in verified documentation."
        )

    def get_tracking(self, tracking_number: str) -> TrackingResult:
        """
        Verified endpoint: Queries the documented ShipSandbox tracking resource.
        Endpoint: GET /v1/tracking/{tracking_number}
        """
        endpoint = f"{self.base_url}/v1/tracking/{tracking_number}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "AdaptiveShippingAgent/0.1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(endpoint, headers=headers)
                response.raise_for_status()
                data = response.json()

                events = [
                    TrackingEvent(
                        timestamp=ev.get("timestamp", ""),
                        status=ev.get("status", "UNKNOWN"),
                        location=ev.get("location", "Facility"),
                        description=ev.get("description", ""),
                    )
                    for ev in data.get("events", [])
                ]

                return TrackingResult(
                    tracking_number=tracking_number,
                    courier_code=data.get("courier_code", "SHIPSANDBOX"),
                    courier_name=data.get("courier_name", "ShipSandbox Carrier"),
                    current_status=data.get("current_status", "IN_TRANSIT"),
                    is_delivered=data.get("is_delivered", False),
                    estimated_delivery=data.get("estimated_delivery"),
                    events=events,
                    is_demo_data=False,
                    missing_evidence=[],
                    disclaimer="VERIFIED API: Data retrieved from verified ShipSandbox tracking endpoint.",
                )
        except httpx.HTTPError as exc:
            # Safe fallback preserving audit trail without fabricating data
            return TrackingResult(
                tracking_number=tracking_number,
                courier_code="SHIPSANDBOX",
                courier_name="ShipSandbox Carrier",
                current_status="UNREACHABLE",
                is_delivered=False,
                estimated_delivery=None,
                events=[],
                is_demo_data=False,
                missing_evidence=[
                    f"Tracking endpoint query failed: {type(exc).__name__}",
                    f"Target endpoint: {endpoint}",
                ],
                disclaimer="ShipSandbox tracking endpoint returned an error or could not be reached.",
            )
