"""
UI Helper Functions and Session State Management.
Pure functions (no Streamlit state) for input validation, formatting,
and converting domain objects into display-ready structures.
These helpers contain NO business logic duplicates – they only call existing domain modules.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

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
from src.domain.freshness import QuoteFreshnessStatus, check_quote_freshness
from src.domain.ranking import RankingResult, RankedCourierQuote


# ---------------------------------------------------------------------------
# Input Validation Helpers
# ---------------------------------------------------------------------------

def validate_pin_code(pin: str, pin_type: str = "", country: str = "India") -> tuple[bool, str]:
    """
    Validate postal PIN/ZIP code format.
    Must be exactly 6 numeric digits for India, or 5 digits for US.
    Return (is_valid, error_message). Empty error = ok.
    """
    cleaned = pin.strip() if pin else ""
    type_str = f"{pin_type.strip()} " if pin_type else ""
    c = (country or "India").strip().upper()
    if not cleaned:
        if c in ("US", "USA", "UNITED STATES"):
            return False, f"ZIP code is required. Enter a valid 5-digit {type_str}ZIP code.".replace("  ", " ")
        return False, f"PIN code is required. Enter a valid 6-digit {type_str}PIN code (must be exactly 6 digits).".replace("  ", " ")

    if c in ("US", "USA", "UNITED STATES"):
        if not re.fullmatch(r"^\d{5}$", cleaned):
            return False, f"Enter a valid 5-digit {type_str}ZIP code (e.g. 78756).".replace("  ", " ")
        return True, ""

    if not re.fullmatch(r"^[1-9]\d{5}$", cleaned):
        return False, f"Enter a valid 6-digit {type_str}PIN code (must be exactly 6 digits).".replace("  ", " ")
    return True, ""


def verify_route_serviceability(
    origin_pin: str,
    dest_pin: str,
    provider: Optional[Any] = None,
) -> tuple[bool, str, bool]:
    """
    Validate PIN formats and verify route serviceability against provider network capability rules.
    Returns (is_valid_and_serviceable, explanation_or_error, is_demo_data).
    """
    ok_p, err_p = validate_pin_code(origin_pin, pin_type="pickup")
    if not ok_p:
        return False, err_p, True

    ok_d, err_d = validate_pin_code(dest_pin, pin_type="delivery")
    if not ok_d:
        return False, err_d, True

    if provider is not None and hasattr(provider, "check_route_serviceability"):
        return provider.check_route_serviceability(origin_pin, dest_pin)

    return True, "", True


def validate_phone(phone: str) -> tuple[bool, str]:
    """Return (is_valid, error_message)."""
    cleaned = re.sub(r"[\s\-\(\)\+]", "", phone.strip())
    if not cleaned:
        return False, "Phone number is required."
    if not re.fullmatch(r"^\d{10,15}$", cleaned):
        return False, f"Phone must contain 10–15 digits (got '{phone}')."
    return True, ""


def validate_positive_float(value: Any, field_name: str, max_val: float = 1e9) -> tuple[bool, str]:
    """Validate a float is present, numeric, finite, strictly positive (> 0) and within max_val."""
    if value is None:
        if "length" in field_name.lower():
            return False, "Please enter the parcel length."
        if "width" in field_name.lower():
            return False, "Please enter the parcel width."
        if "height" in field_name.lower():
            return False, "Please enter the parcel height."
        if "weight" in field_name.lower():
            return False, "Please enter the actual parcel weight."
        return False, f"Please enter the {field_name.lower()}."

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            if "length" in field_name.lower():
                return False, "Please enter the parcel length."
            if "width" in field_name.lower():
                return False, "Please enter the parcel width."
            if "height" in field_name.lower():
                return False, "Please enter the parcel height."
            if "weight" in field_name.lower():
                return False, "Please enter the actual parcel weight."
            return False, f"Please enter the {field_name.lower()}."
        try:
            val = float(cleaned)
        except ValueError:
            return False, f"{field_name} must be a valid number."
    else:
        try:
            val = float(value)
        except (TypeError, ValueError):
            return False, f"{field_name} must be a valid number."

    if math.isnan(val) or math.isinf(val):
        return False, f"{field_name} must be a finite number (NaN and infinite values are not allowed)."

    if val <= 0.0:
        return False, f"{field_name} must be greater than zero (greater than 0)."

    if val > max_val:
        return False, f"{field_name} exceeds allowed maximum ({max_val})."

    return True, ""


def validate_shipment_measurements(
    length_cm: Any,
    width_cm: Any,
    height_cm: Any,
    parcel_weight_kg: Any,
) -> tuple[bool, dict[str, str], list[str]]:
    """
    Validate all 4 mandatory measurements: length, width, height, and actual weight.
    Returns (is_valid, field_errors_dict, error_messages_list).
    """
    field_errors: dict[str, str] = {}
    error_list: list[str] = []
    has_zero_or_negative = False

    for val, name, key in [
        (length_cm, "Parcel length", "length_cm"),
        (width_cm, "Parcel width", "width_cm"),
        (height_cm, "Parcel height", "height_cm"),
        (parcel_weight_kg, "Actual parcel weight", "parcel_weight_kg"),
    ]:
        ok, msg = validate_positive_float(val, name, max_val=1000.0 if "weight" in key else 500.0)
        if not ok:
            field_errors[key] = msg
            error_list.append(msg)
            if "greater than" in msg.lower():
                has_zero_or_negative = True

    if has_zero_or_negative:
        zero_summary = "Dimensions and weight must be greater than zero."
        if zero_summary not in error_list:
            error_list.append(zero_summary)

    return len(field_errors) == 0, field_errors, error_list


def validate_order_reference(ref: str) -> tuple[bool, str]:
    """Validate a merchant order reference."""
    ref = ref.strip()
    if len(ref) < 3:
        return False, "Order reference must be at least 3 characters."
    if len(ref) > 64:
        return False, "Order reference must be at most 64 characters."
    return True, ""


def build_order_input_from_form(form_data: dict[str, Any]) -> tuple[Optional[OrderInput], list[str]]:
    """
    Attempt to construct an OrderInput from flat form_data dict.
    Returns (order, []) on success, or (None, [error_messages]) on failure.
    Enforces mandatory dimensions (length, width, height) and actual weight.
    """
    errors: list[str] = []

    try:
        sender = Address(
            name=form_data.get("sender_name", ""),
            phone=form_data.get("sender_phone", ""),
            street_line1=form_data.get("sender_street", ""),
            street_line2=form_data.get("sender_street2") or None,
            city=form_data.get("sender_city", ""),
            state=form_data.get("sender_state", ""),
            pin_code=form_data.get("sender_pin", ""),
        )
    except ValidationError as exc:
        for e in exc.errors():
            loc_f = e['loc'][-1] if e.get('loc') else 'field'
            errors.append(f"Sender – {loc_f}: {e['msg']}")
        sender = None  # type: ignore[assignment]

    try:
        recipient = Address(
            name=form_data.get("recipient_name", ""),
            phone=form_data.get("recipient_phone", ""),
            street_line1=form_data.get("recipient_street", ""),
            street_line2=form_data.get("recipient_street2") or None,
            city=form_data.get("recipient_city", ""),
            state=form_data.get("recipient_state", ""),
            pin_code=form_data.get("recipient_pin", ""),
        )
    except ValidationError as exc:
        for e in exc.errors():
            loc_f = e['loc'][-1] if e.get('loc') else 'field'
            errors.append(f"Recipient – {loc_f}: {e['msg']}")
        recipient = None  # type: ignore[assignment]

    # Mandatory measurement validations
    len_raw = form_data.get("length_cm")
    wid_raw = form_data.get("width_cm")
    hgt_raw = form_data.get("height_cm")
    wt_raw = form_data.get("parcel_weight_kg")

    ok_m, field_m_errs, list_m_errs = validate_shipment_measurements(
        length_cm=len_raw,
        width_cm=wid_raw,
        height_cm=hgt_raw,
        parcel_weight_kg=wt_raw,
    )
    if not ok_m:
        errors.extend(list_m_errs)

    dimensions: Optional[Dimensions] = None
    if ok_m:
        try:
            dimensions = Dimensions(
                length_cm=float(len_raw),
                width_cm=float(wid_raw),
                height_cm=float(hgt_raw),
            )
        except (ValidationError, ValueError) as exc:
            errors.append(f"Dimensions: {exc}")
            dimensions = None

    is_cod = bool(form_data.get("is_cod", False))
    cod_amount = float(form_data.get("cod_amount", 0.0))

    if errors or sender is None or recipient is None or dimensions is None:
        return None, errors

    try:
        order = OrderInput(
            order_reference=form_data.get("order_reference", "").strip(),
            sender=sender,
            recipient=recipient,
            parcel_weight_kg=float(wt_raw),
            dimensions=dimensions,
            package_type=PackageType(form_data.get("package_type", "box")),
            is_cod=is_cod,
            cod_amount=cod_amount,
            declared_value=float(form_data.get("declared_value", 0.0)),
            product_description=form_data.get("product_description", "General Merchandise").strip() or "General Merchandise",
        )
        return order, []
    except (ValidationError, ValueError) as exc:
        if hasattr(exc, "errors"):
            for e in exc.errors():  # type: ignore[attr-defined]
                loc = e.get("loc", ())
                field = loc[-1] if loc else "order"
                errors.append(f"{field}: {e['msg']}")
        else:
            errors.append(str(exc))
        return None, errors



# ---------------------------------------------------------------------------
# Display Formatters
# ---------------------------------------------------------------------------

def fmt_inr(amount: float, currency: str = "INR") -> str:
    """Format a float with currency code/symbol."""
    c = (currency or "INR").upper()
    if c == "USD":
        return f"${amount:,.2f} USD"
    return f"{c} {amount:,.2f}"


def fmt_kg(kg: float) -> str:
    """Format weight in kg."""
    return f"{kg:.3f} kg"


def fmt_age(seconds: float) -> str:
    """Format seconds as a human-readable age string."""
    if seconds < 0:
        return "Unknown"
    if seconds < 60:
        return f"{seconds:.1f}s ago"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m ago"
    hours = minutes / 60
    return f"{hours:.1f}h ago"


def freshness_badge(quote: CourierQuote, max_age_seconds: int = 900) -> tuple[str, str]:
    """
    Return (label, colour) for Streamlit badge.
    colour is one of: green, orange, red.
    """
    check = check_quote_freshness(quote, max_age_seconds=max_age_seconds)
    age_str = fmt_age(check.age_seconds)
    if check.status == QuoteFreshnessStatus.FRESH:
        return f"Fresh ({age_str})", "green"
    elif check.status == QuoteFreshnessStatus.EXPIRED:
        return f"Expired ({age_str})", "red"
    else:
        return "Unverifiable", "orange"


def quote_source_badge(quote: CourierQuote) -> str:
    """Return a prominent DEMO DATA, SANDBOX, or LIVE label string."""
    if quote.source_type in ("MANUAL_SANDBOX", "SHIPSANDBOX"):
        return "[MANUAL SANDBOX] Verified rate entered manually via carrier dashboard"
    if quote.is_demo_data:
        return "[DEMO DATA] Simulated rate, not a live carrier price"
    return "[LIVE QUOTE]"


def format_cost_breakdown(cb: CostBreakdown) -> dict[str, str]:
    """Return a display-ready ordered dict of cost line items."""
    curr = getattr(cb, "currency", "INR")
    return {
        "Base Freight": fmt_inr(cb.base_freight, currency=curr),
        f"Fuel Surcharge ({cb.fuel_surcharge_rate_pct}%)": fmt_inr(cb.fuel_surcharge, currency=curr),
        "COD Fee": fmt_inr(cb.cod_fee, currency=curr),
        "Handling Fee": fmt_inr(cb.handling_fee, currency=curr),
        "Subtotal (before tax)": fmt_inr(cb.subtotal_before_tax, currency=curr),
        f"GST ({cb.tax_rate_pct}%)": fmt_inr(cb.tax_amount, currency=curr),
        "**Total Landed Cost**": f"**{fmt_inr(cb.total_cost, currency=curr)}**",
    }


def format_weight_breakdown(wr: WeightResult) -> dict[str, str]:
    """Return a display-ready ordered dict of weight breakdown."""
    penalty = " [Volumetric Penalty]" if wr.is_volumetric_penalty else " [Dead-weight Higher]"
    return {
        "Actual (Dead) Weight": fmt_kg(wr.actual_weight_kg),
        "Volumetric Weight": fmt_kg(wr.volumetric_weight_kg),
        f"Volumetric Divisor (Demo default: 5000 cm³/kg)": f"{wr.divisor_used:,.0f} cm³/kg",
        "Chargeable Weight": fmt_kg(wr.chargeable_weight_kg) + penalty,
        "Billing Slab": fmt_kg(wr.billing_slab_kg),
    }


def shipment_status_colour(status: ShipmentStatus) -> str:
    """Map shipment status to a Streamlit colour string."""
    mapping = {
        ShipmentStatus.DRAFT: "gray",
        ShipmentStatus.QUOTED: "blue",
        ShipmentStatus.PENDING_APPROVAL: "orange",
        ShipmentStatus.DISPATCHING: "orange",
        ShipmentStatus.CREATED: "green",
        ShipmentStatus.CREATION_UNCERTAIN: "red",
        ShipmentStatus.CANCELLED: "gray",
        ShipmentStatus.FAILED: "red",
    }
    return mapping.get(status, "gray")


def format_audit_event(row: dict[str, Any]) -> dict[str, str]:
    """Prepare an audit log row for display. Parses JSON safely."""
    try:
        details = json.loads(row.get("details_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        details = {"raw": str(row.get("details_json", ""))}

    timestamp_raw = row.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp_raw)
        timestamp_display = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        timestamp_display = timestamp_raw

    return {
        "timestamp": timestamp_display,
        "event": row.get("event_type", ""),
        "actor": row.get("actor", ""),
        "order_ref": row.get("order_reference") or "—",
        "details_preview": json.dumps(details, indent=2)[:300],
    }


def describe_ranking_result(result: RankingResult) -> str:
    """Produce a short human-readable explanation of a ranking result."""
    if result.selected_winner is None:
        return result.decision_explanation

    w = result.selected_winner
    parts = [f"**Recommended courier:** {w.quote.courier_name} ({w.quote.service_type})"]
    parts.append(f"**Total landed cost:** {fmt_inr(w.total_cost)}")
    if w.estimated_delivery_days:
        parts.append(f"**Estimated delivery:** {w.estimated_delivery_days} business days")
    parts.append(f"**Strategy:** {result.mode.value.replace('_', ' ').title()}")
    if result.cost_margin_over_runner_up is not None and result.cost_margin_over_runner_up > 0:
        parts.append(f"**Cost margin over runner-up:** {fmt_inr(result.cost_margin_over_runner_up)}")
    parts.append(f"\n_{result.decision_explanation}_")
    return "  \n".join(parts)


def friendly_courier_status(quote: CourierQuote, is_winner: bool = False) -> tuple[str, str, str]:
    """
    Map courier quote eligibility to understandable user-facing status labels.
    Returns (status_label, badge_colour, short_explanation).
    """
    if is_winner:
        return "Recommended", "green", "Best verified option matching your delivery and cost preferences."

    if not quote.is_serviceable or not quote.is_eligible:
        reason = quote.unserviceable_reason or "Exceeds courier size or regional route restriction."
        return "Not available for this parcel", "red", reason

    if quote.cost_breakdown is None:
        return "Price unavailable", "gray", "Carrier did not provide pricing for this route/weight."

    el_status = getattr(quote, "eligibility_status", "ELIGIBLE")
    if el_status in ("UNVERIFIED", "PENDING"):
        return "Needs verification", "orange", "Carrier size limits or regional route unconfirmed. Requires verification."

    return "Eligible", "blue", "Eligible and ready for dispatch."


def format_packaging_recommendation(box_name: str, savings_inr: float = 0.0, reason: str = "") -> str:
    """Format an automatic packaging recommendation in plain language."""
    msg = f"**Recommended packaging:** {box_name}"
    if reason:
        msg += f"  \n*Why:* {reason}"
    elif savings_inr > 0:
        msg += f"  \n*Why:* Fits the parcel safely while saving approximately {fmt_inr(savings_inr)} in volumetric freight."
    else:
        msg += "  \n*Why:* Fits the parcel snugly while reducing unused space and dimensional charges."
    return msg

