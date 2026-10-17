"""
Courier Provider Registry and Factory.
Enables pluggable provider switching via configuration without altering core logic.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Type
from src.providers.base import CourierProvider
from src.providers.demo_adapter import DemoShippingAdapter
from src.providers.shipsandbox_client import ShipSandboxClient


PROVIDER_REGISTRY: dict[str, Type[CourierProvider]] = {
    "demo": DemoShippingAdapter,
    "shipsandbox": ShipSandboxClient,
}


def get_provider(mode: Optional[str] = None) -> CourierProvider:
    """
    Factory function returning the configured CourierProvider instance.
    Defaults to 'demo' adapter if unspecified or unconfigured.
    """
    active_mode = (mode or os.getenv("SHIPPING_PROVIDER_MODE", "demo")).strip().lower()
    provider_cls = PROVIDER_REGISTRY.get(active_mode)

    if not provider_cls:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(f"Unknown shipping provider mode '{active_mode}'. Available modes: {available}")

    return provider_cls()
