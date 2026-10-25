"""
Courier Provider Registry and Factory.
Supports 100% offline modes:
1. Local Demo Mode (default, simulated multi-courier network)
2. Local Courier Sandbox (Simulation) (self-contained offline sandbox simulation)
3. Manual Sandbox Entry mode (operator-entered rates from external dashboards)

Zero external API dependencies, zero credentials, zero network calls.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Type
from src.providers.base import CourierProvider
from src.providers.demo_adapter import DemoShippingAdapter, LocalSandboxShippingAdapter


_CURRENT_PROVIDER_MODE: str = os.getenv("SHIPPING_PROVIDER_MODE", "demo").strip().lower()

PROVIDER_REGISTRY: dict[str, Type[CourierProvider]] = {
    "demo": DemoShippingAdapter,
    "sandbox": LocalSandboxShippingAdapter,
    "local_sandbox": LocalSandboxShippingAdapter,
    "manual_sandbox": DemoShippingAdapter,  # Uses demo adapter infrastructure with manual quote tags
}


def get_active_provider_mode() -> str:
    """Return the currently selected provider mode."""
    global _CURRENT_PROVIDER_MODE
    return _CURRENT_PROVIDER_MODE


def set_active_provider_mode(mode: str) -> str:
    """Update the current provider mode. Maps 'sandbox' to 'local_sandbox'."""
    global _CURRENT_PROVIDER_MODE
    cleaned = mode.strip().lower()
    if cleaned in ("sandbox", "local_sandbox"):
        cleaned = "local_sandbox"
    elif cleaned not in ("demo", "manual_sandbox"):
        raise ValueError(f"Invalid provider mode '{mode}'. Choose from 'demo', 'local_sandbox', 'sandbox', 'manual_sandbox'.")
    _CURRENT_PROVIDER_MODE = cleaned
    return _CURRENT_PROVIDER_MODE


def get_provider(mode: Optional[str] = None) -> CourierProvider:
    """
    Factory function returning the configured CourierProvider instance.
    Defaults to local 'demo' adapter when unconfigured or in demo mode.
    """
    active_mode = (mode or get_active_provider_mode()).strip().lower()
    if active_mode in ("sandbox", "local_sandbox"):
        active_mode = "local_sandbox"
    provider_cls = PROVIDER_REGISTRY.get(active_mode)

    if not provider_cls:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(f"Unknown shipping provider mode '{active_mode}'. Available modes: {available}")

    return provider_cls()


def get_provider_status() -> dict[str, Any]:
    """Return offline mode status for the UI."""
    mode = get_active_provider_mode()
    mode_descriptions = {
        "demo": "Local Demo Mode — Offline deterministic multi-courier simulation.",
        "sandbox": "Local Courier Sandbox (Simulation) — Isolated offline sandbox test environment.",
        "local_sandbox": "Local Courier Sandbox (Simulation) — Isolated offline sandbox test environment.",
        "manual_sandbox": "Manual Sandbox Entry — Operator-entered rates from external sandbox dashboards.",
    }

    return {
        "active_mode": mode,
        "mode_description": mode_descriptions.get(mode, "Local offline mode."),
        "is_offline": True,
        "available_modes": ["demo", "local_sandbox", "sandbox", "manual_sandbox"],
        "default_mode": "demo",
    }
