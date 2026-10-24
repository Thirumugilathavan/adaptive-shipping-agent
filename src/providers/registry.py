"""
Courier Provider Registry and Factory.
Enforces local offline operation via the deterministic DemoShippingAdapter.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Type
from src.providers.base import CourierProvider
from src.providers.demo_adapter import DemoShippingAdapter


PROVIDER_REGISTRY: dict[str, Type[CourierProvider]] = {
    "demo": DemoShippingAdapter,
}


def get_provider(mode: Optional[str] = None) -> CourierProvider:
    """
    Factory function returning the configured CourierProvider instance.
    Defaults to local 'demo' adapter. External carrier APIs are disabled.
    """
    active_mode = (mode or os.getenv("SHIPPING_PROVIDER_MODE", "demo")).strip().lower()
    provider_cls = PROVIDER_REGISTRY.get(active_mode)

    if not provider_cls:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(f"Unknown shipping provider mode '{active_mode}'. Available modes: {available}")

    return provider_cls()
