"""
Configurable Courier Name and Brand Mapping.
Maps simulated/demo service codes to recognizable Indian courier service brands
while explicitly preserving synthetic/simulated labels and disclosures.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel


class SimulatedCourierInfo(BaseModel):
    courier_code: str
    brand_name: str
    service_label: str
    display_name: str
    service_type: str
    is_simulated: bool = True
    disclaimer: str = "SIMULATED COURIER OPTIONS — NOT LIVE CARRIER QUOTES"


# Configurable mapping table for recognizable Indian courier services
CONFIGURABLE_SIMULATED_COURIERS: Dict[str, Dict[str, Any]] = {
    "DEMO_AIR_EXPRESS": {
        "brand_name": "Blue Dart",
        "service_label": "Express",
        "display_name": "Blue Dart Express (Simulated)",
        "service_type": "Air Express Priority",
    },
    "DEMO_SURFACE_STD": {
        "brand_name": "Delhivery",
        "service_label": "Surface",
        "display_name": "Delhivery Surface (Simulated)",
        "service_type": "Surface Ground Economy",
    },
    "DEMO_ECONOMY_CARGO": {
        "brand_name": "DTDC",
        "service_label": "Heavy Cargo",
        "display_name": "DTDC Heavy Cargo (Simulated)",
        "service_type": "Heavy Commercial Freight",
    },
    "DEMO_SAME_DAY": {
        "brand_name": "XpressBees",
        "service_label": "Same-Day",
        "display_name": "XpressBees Same-Day (Simulated)",
        "service_type": "Same-Day Metro Delivery",
    },
    "DEMO_BUDGET_SAVER": {
        "brand_name": "India Post",
        "service_label": "Parcel Post",
        "display_name": "India Post Parcel (Simulated)",
        "service_type": "Budget Surface Economy",
    },
    "DEMO_OVERNIGHT": {
        "brand_name": "Blue Dart",
        "service_label": "Overnight",
        "display_name": "Blue Dart Overnight (Simulated)",
        "service_type": "Overnight Priority Air",
    },
    "DEMO_REGIONAL_ROAD": {
        "brand_name": "DTDC",
        "service_label": "Regional Road",
        "display_name": "DTDC Regional (Simulated)",
        "service_type": "Regional Highway Express",
    },
    "DEMO_B2B_LOGISTICS": {
        "brand_name": "Delhivery",
        "service_label": "Freight B2B",
        "display_name": "Delhivery Freight (Simulated)",
        "service_type": "B2B Scheduled Cargo",
    },
    "DEMO_SMART_PARCEL": {
        "brand_name": "Ecom Express",
        "service_label": "Parcel Connect",
        "display_name": "Ecom Express Parcel (Simulated)",
        "service_type": "Standard Parcel Delivery",
    },
    "DEMO_EXPRESS_GOLD": {
        "brand_name": "XpressBees",
        "service_label": "Gold Priority",
        "display_name": "XpressBees Priority (Simulated)",
        "service_type": "Premium Fast Track",
    },
}

SIMULATED_NOTICE_BANNER = "SIMULATED COURIER OPTIONS — NOT LIVE CARRIER QUOTES"


def get_simulated_courier_info(courier_code: str) -> Optional[SimulatedCourierInfo]:
    """Retrieve simulated courier configuration by code."""
    data = CONFIGURABLE_SIMULATED_COURIERS.get(courier_code)
    if not data:
        return None
    return SimulatedCourierInfo(
        courier_code=courier_code,
        brand_name=data["brand_name"],
        service_label=data["service_label"],
        display_name=data["display_name"],
        service_type=data["service_type"],
    )


def get_courier_display_name(courier_code: str, fallback_name: str = "") -> str:
    """Return configured recognizable Indian courier display name if simulated code, otherwise fallback."""
    info = get_simulated_courier_info(courier_code)
    return info.display_name if info else fallback_name


def get_courier_brand(courier_code: str, fallback_brand: str = "") -> str:
    """Return recognized carrier brand name."""
    info = get_simulated_courier_info(courier_code)
    return info.brand_name if info else fallback_brand


def get_courier_service_label(courier_code: str, fallback_label: str = "") -> str:
    """Return distinct service label (e.g., Express, Surface, Heavy Cargo)."""
    info = get_simulated_courier_info(courier_code)
    return info.service_label if info else fallback_label
