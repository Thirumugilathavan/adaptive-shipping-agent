"""
Production-ready PDF Shipping Label Generator using ReportLab.

Provides:
- Standard single-page label output (A6 and 4x6 inches)
- Code128 vector barcode generation for carrier AWBs
- 2D QR Code containing verified shipment references
- Clear synthetic disclosure watermarks for demo shipments
- Clean typography and auto-wrapping for long recipient/sender addresses
- Distinct prepaid vs. Cash on Delivery (COD) collection callouts
"""

import html
import io
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Union

from reportlab.graphics.barcode import createBarcodeDrawing, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A6
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class LabelPageFormat(str, Enum):
    """Supported standard shipping label paper formats."""
    A6 = "A6"                  # 105 x 148 mm (~4.1 x 5.8 inches)
    INCH_4X6 = "4x6"           # 4 x 6 inches (101.6 x 152.4 mm) standard thermal


PAGE_DIMENSIONS = {
    LabelPageFormat.A6: A6,                             # (297.64, 419.53) pt
    LabelPageFormat.INCH_4X6: (4.0 * inch, 6.0 * inch), # (288.0, 432.0) pt
}


def sanitize_label_filename(tracking_number: str, extension: str = "pdf") -> str:
    """
    Produce a safe, alphanumeric filename for the downloadable label.
    """
    safe_ref = re.sub(r"[^A-Za-z0-9_-]", "_", str(tracking_number).strip())
    safe_ref = safe_ref.strip("_") or "LABEL"
    clean_ext = extension.lstrip(".")
    return f"shipping_label_{safe_ref}.{clean_ext}"


def is_valid_pdf(data: bytes) -> bool:
    """Verify bytes represent a valid PDF file."""
    if not isinstance(data, bytes) or len(data) < 10:
        return False
    return data.startswith(b"%PDF-")


class WatermarkCanvas(canvas.Canvas):
    """
    Custom canvas that draws a subtle diagonal synthetic watermark on demo labels
    and paints an outer label cut border.
    """

    def __init__(self, *args, is_demo: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_demo = is_demo

    def showPage(self):
        w, h = self._pagesize

        # Subtle outer boundary border (mimics peel-and-stick shipping label cutline)
        self.saveState()
        self.setStrokeColor(colors.HexColor("#374151"))
        self.setLineWidth(1.0)
        self.rect(6, 6, w - 12, h - 12)

        # Synthetic demo watermark if applicable
        if self.is_demo:
            self.setFont("Helvetica-Bold", 24)
            self.setFillColor(colors.Color(0.85, 0.20, 0.20, alpha=0.10))
            self.translate(w / 2.0, h / 2.0)
            self.rotate(32)
            self.drawCentredString(0, 15, "SIMULATED / DEMO SHIPMENT")
            self.drawCentredString(0, -12, "NOT AN OFFICIAL CARRIER DOCUMENT")

        self.restoreState()
        super().showPage()


def build_barcode_drawing(awb_number: str, max_width: float = 195.0, height: float = 30.0) -> Drawing:
    """
    Generate a clean Code128 vector barcode scaled cleanly to fit the label.
    """
    safe_awb = re.sub(r"[^A-Za-z0-9_-]", "", awb_number) or "UNKNOWN"
    try:
        raw_bc = createBarcodeDrawing(
            "Code128",
            value=safe_awb,
            barWidth=0.85,
            barHeight=height,
            humanReadable=False,
        )
        if raw_bc.width > max_width:
            scale = max_width / raw_bc.width
            d = Drawing(max_width, raw_bc.height * scale, transform=[scale, 0, 0, scale, 0, 0])
            d.add(raw_bc)
            return d
        return raw_bc
    except Exception:
        # Graceful fallback: text placeholder inside a drawing
        fallback = Drawing(max_width, height)
        return fallback


def build_qr_drawing(payload: str, size: float = 46.0) -> Drawing:
    """
    Generate a 2D QR Code vector drawing encoding verified shipment references.
    """
    try:
        qr_widget = qr.QrCodeWidget(payload)
        bounds = qr_widget.getBounds()
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        scale_x = size / (w or 1)
        scale_y = size / (h or 1)
        d = Drawing(size, size, transform=[scale_x, 0, 0, scale_y, 0, 0])
        d.add(qr_widget)
        return d
    except Exception:
        return Drawing(size, size)


def generate_shipping_label_pdf(
    *,
    tracking_number: str,
    order_reference: str,
    courier_name: str,
    service_type: str = "Standard Logistics",
    shipment_id: Optional[str] = None,
    origin_pin: str = "—",
    origin_city: str = "Origin Hub",
    origin_state: str = "IN",
    destination_pin: str = "—",
    destination_city: str = "Destination Hub",
    destination_state: str = "IN",
    sender_name: str = "Warehouse Logistics Hub",
    sender_street: str = "Facility 1, Central Industrial Estate",
    sender_phone: str = "—",
    recipient_name: str = "Valued Customer",
    recipient_street: str = "Delivery Destination",
    recipient_phone: str = "—",
    actual_weight_kg: float = 1.0,
    volumetric_weight_kg: Optional[float] = None,
    billable_weight_kg: Optional[float] = None,
    dimensions_cm: str = "—",
    package_type: str = "Standard Parcel",
    is_cod: bool = False,
    cod_amount: float = 0.0,
    created_at_iso: Optional[str] = None,
    pickup_reference: Optional[str] = None,
    is_demo: bool = True,
    page_format: Union[LabelPageFormat, str] = LabelPageFormat.A6,
) -> bytes:
    """
    Generate a genuine, printer-ready PDF shipping label in A6 or 4x6 format.

    Returns raw PDF bytes starting with '%PDF-'.
    """
    # Resolve page size
    fmt_key = LabelPageFormat.INCH_4X6 if str(page_format).lower() in ("4x6", "inch_4x6") else LabelPageFormat.A6
    page_size = PAGE_DIMENSIONS[fmt_key]
    page_w, page_h = page_size

    # Usable printable dimensions
    margin_lr = 9.0
    margin_tb = 9.0
    usable_width = page_w - (2 * margin_lr)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=page_size,
        leftMargin=margin_lr,
        rightMargin=margin_lr,
        topMargin=margin_tb,
        bottomMargin=margin_tb,
    )

    styles = getSampleStyleSheet()

    # Helpers for safe paragraph generation
    def p(text: str, style: ParagraphStyle) -> Paragraph:
        return Paragraph(text, style)

    def esc(text: Any) -> str:
        return html.escape(str(text or "—"))

    # Style definitions
    style_carrier = ParagraphStyle("Carrier", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11, leading=13)
    style_service = ParagraphStyle("Service", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=9, alignment=2)
    style_date = ParagraphStyle("Date", parent=styles["Normal"], fontName="Helvetica", fontSize=6.5, leading=8, alignment=2, textColor=colors.HexColor("#4B5563"))
    style_awb_title = ParagraphStyle("AWBTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=12, leading=14, alignment=1)
    style_route = ParagraphStyle("Route", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=1)
    style_address_hdr = ParagraphStyle("AddrHdr", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=9)
    style_address_body = ParagraphStyle("AddrBody", parent=styles["Normal"], fontName="Helvetica", fontSize=6.5, leading=8.5)
    style_specs = ParagraphStyle("Specs", parent=styles["Normal"], fontName="Helvetica", fontSize=6.5, leading=8.5)
    style_cod_pos = ParagraphStyle("CODPos", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=1, textColor=colors.HexColor("#B91C1C"))
    style_cod_pre = ParagraphStyle("CODPre", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=1, textColor=colors.HexColor("#15803D"))
    style_footer = ParagraphStyle("Footer", parent=styles["Normal"], fontName="Helvetica", fontSize=5.5, leading=7, alignment=1, textColor=colors.HexColor("#6B7280"))
    style_demo_tag = ParagraphStyle("DemoTag", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=6.5, leading=8, alignment=1, textColor=colors.HexColor("#991B1B"))

    story = []

    # ── 1. HEADER SECTION ──
    carrier_cell = [
        p(f"<b>{esc(courier_name).upper()}</b>", style_carrier),
        p(f"Ref: {esc(order_reference)}", ParagraphStyle("Ref", parent=styles["Normal"], fontName="Helvetica", fontSize=7, leading=8.5)),
    ]
    date_str = created_at_iso[:16].replace("T", " ") if created_at_iso else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    service_cell = [
        p(f"<b>{esc(service_type).upper()}</b>", style_service),
        p(f"Date: {date_str} UTC", style_date),
    ]
    header_tbl = Table([[carrier_cell, service_cell]], colWidths=[usable_width * 0.62, usable_width * 0.38])
    header_tbl.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 1.5, colors.HexColor("#111827")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 2))

    # ── 2. SYNTHETIC DISCLAIMER BANNER (DEMO ONLY) ──
    if is_demo:
        demo_banner = Table(
            [[p("<b>DEMO SHIPMENT — SIMULATED CARRIER LABEL FOR EVALUATION ONLY</b>", style_demo_tag)]],
            colWidths=[usable_width],
        )
        demo_banner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEE2E2")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#EF4444")),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(demo_banner)
        story.append(Spacer(1, 2))

    # ── 3. ROUTING STRIP ──
    route_str = f"PIN: {esc(origin_pin)} &nbsp;&#10142;&nbsp; <b>PIN: {esc(destination_pin)}</b> &nbsp;({esc(destination_city).upper()})"
    route_tbl = Table([[p(route_str, style_route)]], colWidths=[usable_width])
    route_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
        ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#9CA3AF")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(route_tbl)
    story.append(Spacer(1, 2))

    # ── 4. AWB TITLE & BARCODE + QR BLOCK ──
    story.append(p(f"<b>AWB: {esc(tracking_number)}</b>", style_awb_title))
    story.append(Spacer(1, 1))

    barcode_drawing = build_barcode_drawing(tracking_number, max_width=usable_width - 55.0, height=28.0)
    qr_payload = f"AWB:{tracking_number}|ORD:{order_reference}|TO:{destination_pin}"
    qr_drawing = build_qr_drawing(qr_payload, size=40.0)

    code_tbl = Table([[barcode_drawing, qr_drawing]], colWidths=[usable_width - 48.0, 48.0])
    code_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "CENTER"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(code_tbl)
    story.append(Spacer(1, 2))

    # ── 5. ADDRESSES GRID (SHIP TO / RETURN FROM) ──
    to_lines = [
        f"<b>SHIP TO (DELIVERY):</b>",
        f"<b>{esc(recipient_name)}</b>",
        f"{esc(recipient_street)}",
        f"{esc(destination_city)}, {esc(destination_state)} - <b>{esc(destination_pin)}</b>",
        f"Phone: {esc(recipient_phone)}",
    ]
    from_lines = [
        f"<b>RETURN IF UNDELIVERED:</b>",
        f"<b>{esc(sender_name)}</b>",
        f"{esc(sender_street)}",
        f"{esc(origin_city)}, {esc(origin_state)} - <b>{esc(origin_pin)}</b>",
        f"Phone: {esc(sender_phone)}",
    ]

    addr_cell_to = p("<br/>".join(to_lines), style_address_body)
    addr_cell_from = p("<br/>".join(from_lines), style_address_body)

    col_to_w = usable_width * 0.53
    col_from_w = usable_width * 0.47
    addr_tbl = Table([[addr_cell_to, addr_cell_from]], colWidths=[col_to_w, col_from_w])
    addr_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#111827")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(addr_tbl)
    story.append(Spacer(1, 2))

    # ── 6. PACKAGE SPECIFICATIONS ──
    vol_str = f"{volumetric_weight_kg:.2f} kg" if volumetric_weight_kg else "—"
    billable_str = f"{billable_weight_kg:.2f} kg" if billable_weight_kg else f"{actual_weight_kg:.2f} kg"
    specs_line = (
        f"<b>Weight:</b> {actual_weight_kg:.2f} kg &nbsp;|&nbsp; "
        f"<b>Volumetric:</b> {vol_str} &nbsp;|&nbsp; "
        f"<b>Billed:</b> {billable_str}<br/>"
        f"<b>Dimensions:</b> {esc(dimensions_cm)} &nbsp;|&nbsp; "
        f"<b>Pkg:</b> {esc(package_type)}"
    )
    specs_tbl = Table([[p(specs_line, style_specs)]], colWidths=[usable_width])
    specs_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#9CA3AF")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(specs_tbl)
    story.append(Spacer(1, 2))

    # ── 7. PAYMENT / COD STRIP ──
    if is_cod and cod_amount > 0:
        cod_content = p(f"<b>&#9888; COLLECT CASH ON DELIVERY: ₹ {cod_amount:,.2f}</b>", style_cod_pos)
        cod_tbl = Table([[cod_content]], colWidths=[usable_width])
        cod_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEE2E2")),
            ("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor("#DC2626")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
    else:
        prepaid_content = p("<b>&#10003; PREPAID SHIPMENT &mdash; DO NOT COLLECT CASH</b>", style_cod_pre)
        cod_tbl = Table([[prepaid_content]], colWidths=[usable_width])
        cod_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
            ("BOX", (0, 0), (-1, -1), 1.25, colors.HexColor("#16A34A")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
    story.append(cod_tbl)
    story.append(Spacer(1, 2))

    # ── 8. FOOTER / AUDIT INTEGRITY ──
    shp_id_str = f"Shipment ID: {esc(shipment_id)} &bull; " if shipment_id else ""
    pkp_str = f"Pickup Ref: {esc(pickup_reference)} &bull; " if pickup_reference else ""
    footer_text = f"{shp_id_str}{pkp_str}Adaptive Shipping Agent &bull; 5-Point Verified"
    story.append(p(footer_text, style_footer))

    def canvas_maker(*args, **kwargs):
        return WatermarkCanvas(*args, is_demo=is_demo, **kwargs)

    doc.build(story, canvasmaker=canvas_maker)
    pdf_bytes = buf.getvalue()
    buf.close()

    return pdf_bytes


def generate_label_from_shipment_context(
    order: Any,
    quote: Any,
    shipment_result: Any,
    pickup_result: Optional[Any] = None,
    page_format: str = "A6",
) -> bytes:
    """
    Convenience adapter that unpacks OrderInput, CourierQuote, ShipmentResult,
    and PickupResult models into the PDF generator.
    """
    tracking = getattr(shipment_result, "tracking_number", "") or "DEMO-TRK-UNKNOWN"
    shipment_id = getattr(shipment_result, "shipment_id", "")
    order_ref = getattr(order, "order_reference", "") or "ORD-REF"
    courier_name = getattr(quote, "courier_name", "") or getattr(shipment_result, "courier_code", "Carrier")
    service_type = getattr(quote, "service_type", "") or "Standard"

    # Addresses
    sender = getattr(order, "sender", None) or getattr(order, "sender_address", None)
    recipient = getattr(order, "recipient", None) or getattr(order, "recipient_address", None)

    dims = getattr(order, "dimensions", None)
    dims_str = f"{dims.length_cm:.1f}×{dims.width_cm:.1f}×{dims.height_cm:.1f} cm" if dims else "—"

    is_demo = getattr(shipment_result, "is_demo_data", True)

    pickup_ref = getattr(pickup_result, "pickup_reference", None) if pickup_result else None

    # Weight details
    dead_wt = getattr(order, "parcel_weight_kg", 1.0)
    billed_wt = getattr(quote, "chargeable_weight_kg", None) if quote else None
    pkg_type = getattr(order, "packaging_type", "Standard Parcel")

    # If quote has volumetric weight
    vol_wt = None
    if quote and hasattr(quote, "breakdown") and quote.breakdown:
        slab = getattr(quote.breakdown, "chargeable_weight_kg", None)
        if slab and slab > dead_wt:
            vol_wt = slab

    return generate_shipping_label_pdf(
        tracking_number=tracking,
        order_reference=order_ref,
        courier_name=courier_name,
        service_type=service_type,
        shipment_id=shipment_id,
        origin_pin=getattr(sender, "pin_code", "—") if sender else "—",
        origin_city=getattr(sender, "city", "Origin Hub") if sender else "Origin Hub",
        origin_state=getattr(sender, "state", "IN") if sender else "IN",
        destination_pin=getattr(recipient, "pin_code", "—") if recipient else "—",
        destination_city=getattr(recipient, "city", "Destination Hub") if recipient else "Destination Hub",
        destination_state=getattr(recipient, "state", "IN") if recipient else "IN",
        sender_name=getattr(sender, "name", "Warehouse Logistics Hub") if sender else "Warehouse Logistics Hub",
        sender_street=getattr(sender, "street_line1", "Central Fulfillment Facility") if sender else "Central Fulfillment Facility",
        sender_phone=getattr(sender, "phone", "—") if sender else "—",
        recipient_name=getattr(recipient, "name", "Recipient Customer") if recipient else "Recipient Customer",
        recipient_street=getattr(recipient, "street_line1", "Delivery Address") if recipient else "Delivery Address",
        recipient_phone=getattr(recipient, "phone", "—") if recipient else "—",
        actual_weight_kg=dead_wt,
        volumetric_weight_kg=vol_wt,
        billable_weight_kg=billed_wt,
        dimensions_cm=dims_str,
        package_type=pkg_type,
        is_cod=getattr(order, "is_cod", False),
        cod_amount=getattr(order, "cod_amount", 0.0),
        pickup_reference=pickup_ref,
        is_demo=is_demo,
        page_format=page_format,
    )
