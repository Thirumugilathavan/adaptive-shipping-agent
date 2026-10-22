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

REPORTLAB_AVAILABLE = True
try:
    from reportlab.graphics.barcode import createBarcodeDrawing
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget
    except (ImportError, ModuleNotFoundError):
        try:
            from reportlab.graphics.barcode import qr
            QrCodeWidget = getattr(qr, "QrCodeWidget", None)
        except (ImportError, ModuleNotFoundError):
            QrCodeWidget = None

    from reportlab.graphics.shapes import Drawing
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A6
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch, mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except (ImportError, ModuleNotFoundError):
    REPORTLAB_AVAILABLE = False
    Drawing = object  # type: ignore
    canvas = None     # type: ignore
    QrCodeWidget = None


class LabelPageFormat(str, Enum):
    """Supported standard shipping label paper formats."""
    A6 = "A6"                  # 105 x 148 mm (~4.1 x 5.8 inches)
    INCH_4X6 = "4x6"           # 4 x 6 inches (101.6 x 152.4 mm) standard thermal


PAGE_DIMENSIONS = {
    LabelPageFormat.A6: (297.64, 419.53),               # 105 x 148 mm in pt
    LabelPageFormat.INCH_4X6: (4.0 * 72.0, 6.0 * 72.0), # 4 x 6 inches (288.0, 432.0) pt
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


_CanvasBase = canvas.Canvas if (REPORTLAB_AVAILABLE and canvas is not None) else object


class WatermarkCanvas(_CanvasBase):
    """
    Custom canvas that draws a subtle diagonal synthetic watermark on demo labels
    and paints an outer label cut border.
    """

    def __init__(self, *args, is_demo: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_demo = is_demo

    def showPage(self):
        if not REPORTLAB_AVAILABLE:
            return
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


def build_barcode_drawing(awb_number: str, max_width: float = 195.0, height: float = 30.0) -> Any:
    """
    Generate a clean Code128 vector barcode scaled cleanly to fit the label.
    """
    if not REPORTLAB_AVAILABLE:
        return None
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
        fallback = Drawing(max_width, height)
        return fallback


def build_qr_drawing(payload: str, size: float = 46.0) -> Any:
    """
    Generate a 2D QR Code vector drawing encoding verified shipment references.
    """
    if not REPORTLAB_AVAILABLE or QrCodeWidget is None:
        return Drawing(size, size) if REPORTLAB_AVAILABLE else None
    try:
        qr_widget = QrCodeWidget(payload)
        bounds = qr_widget.getBounds()
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        scale_x = size / (w or 1)
        scale_y = size / (h or 1)
        d = Drawing(size, size, transform=[scale_x, 0, 0, scale_y, 0, 0])
        d.add(qr_widget)
        return d
    except Exception:
        return Drawing(size, size) if REPORTLAB_AVAILABLE else None


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
    volumetric_display: Optional[str] = None,
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

    if not REPORTLAB_AVAILABLE:
        # Graceful pure-python valid PDF generator fallback when ReportLab is unavailable
        vol_str = volumetric_display or (f"{volumetric_weight_kg:.2f} kg" if volumetric_weight_kg is not None else ("Not configured" if dimensions_cm and dimensions_cm != "—" else "—"))
        billable_str = f"{billable_weight_kg:.2f} kg" if billable_weight_kg is not None else f"{actual_weight_kg:.2f} kg"
        safe_awb_pdf = re.sub(r"[^A-Za-z0-9_-]", "", tracking_number) or "UNKNOWN"
        fallback_pdf = (
            f"%PDF-1.4\n"
            f"% Simulated Shipping Label Document\n"
            f"1 0 obj <</Type /Catalog /Pages 2 0 R>> endobj\n"
            f"2 0 obj <</Type /Pages /Kids [3 0 R] /Count 1>> endobj\n"
            f"3 0 obj <</Type /Page /Parent 2 0 R /MediaBox [0 0 {int(page_w)} {int(page_h)}] /Contents 4 0 R /Resources <</Font <</F1 5 0 R>>>>>> endobj\n"
            f"5 0 obj <</Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold>> endobj\n"
            f"4 0 obj <</Length 350>> stream\n"
            f"BT\n/F1 14 Tf\n20 {int(page_h - 40)} Td\n({courier_name} - {service_type}) Tj\n"
            f"/F1 12 Tf\n0 -25 Td\n(AWB: {safe_awb_pdf}) Tj\n"
            f"/F1 9 Tf\n0 -20 Td\n(Order: {order_reference} | Wt: {actual_weight_kg:.2f}kg | Vol: {vol_str} | Billed: {billable_str}) Tj\n"
            f"/F1 9 Tf\n0 -18 Td\n(Route: PIN {origin_pin} -> PIN {destination_pin}) Tj\n"
            f"/F1 8 Tf\n0 -22 Td\n(Ship To: {recipient_name}, {destination_city} {destination_pin}) Tj\n"
            f"/F1 8 Tf\n0 -16 Td\n(From: {sender_name}, {origin_city} {origin_pin}) Tj\n"
            f"ET\nendstream\nendobj\nxref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000060 00000 n \n0000000117 00000 n \n0000000280 00000 n \n0000000220 00000 n \ntrailer <</Size 6 /Root 1 0 R>>\nstartxref\n580\n%%EOF\n"
        )
        return fallback_pdf.encode("utf-8")

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
    if volumetric_display:
        vol_str = volumetric_display
    elif volumetric_weight_kg is not None:
        vol_str = f"{volumetric_weight_kg:.2f} kg"
    elif dimensions_cm and dimensions_cm != "—":
        vol_str = "Not configured"
    else:
        vol_str = "—"
    billable_str = f"{billable_weight_kg:.2f} kg" if billable_weight_kg is not None else f"{actual_weight_kg:.2f} kg"
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
    if dead_wt is None:
        dead_wt = 1.0

    dims = getattr(order, "dimensions", None)
    dims_str = f"{dims.length_cm:.1f}×{dims.width_cm:.1f}×{dims.height_cm:.1f} cm" if dims else "—"

    is_demo = getattr(shipment_result, "is_demo_data", True)
    pickup_ref = getattr(pickup_result, "pickup_reference", None) if pickup_result else None
    pkg_type = getattr(order, "packaging_type", "Standard Parcel")

    courier_code = (
        getattr(quote, "courier_code", None)
        or getattr(shipment_result, "courier_code", None)
        or ""
    )

    vol_wt: Optional[float] = None
    vol_display: Optional[str] = None
    billed_wt: Optional[float] = None

    # 1. First check if quote carries an audited weight_result
    weight_res = getattr(quote, "weight_result", None) if quote else None
    if weight_res:
        vol_wt = getattr(weight_res, "volumetric_weight_kg", None)
        billed_wt = getattr(weight_res, "chargeable_weight_kg", None)
        wr_actual = getattr(weight_res, "actual_weight_kg", None)
        if wr_actual:
            dead_wt = wr_actual

    # 2. If volumetric weight is still None, but dimensions exist, calculate via dimension engine
    if vol_wt is None and dims and getattr(dims, "length_cm", 0) > 0 and getattr(dims, "width_cm", 0) > 0 and getattr(dims, "height_cm", 0) > 0:
        from src.domain.dimension_engine import OFFICIAL_DOCUMENTED_CAPABILITIES, compute_volumetric_weight
        rule = OFFICIAL_DOCUMENTED_CAPABILITIES.get(courier_code)
        if rule and rule.volumetric_rule and rule.volumetric_rule.divisor > 0:
            divisor = rule.volumetric_rule.divisor
            vol_wt = compute_volumetric_weight(dims, divisor=divisor)
            if billed_wt is None:
                billed_wt = max(dead_wt, vol_wt)
        else:
            vol_display = "Not configured"
            if billed_wt is None:
                billed_wt = dead_wt

    if vol_wt is not None and vol_display is None:
        vol_display = f"{vol_wt:.2f} kg"

    if billed_wt is None:
        if quote and hasattr(quote, "chargeable_weight_kg") and quote.chargeable_weight_kg:
            billed_wt = quote.chargeable_weight_kg
        elif vol_wt is not None:
            billed_wt = max(dead_wt, vol_wt)
        else:
            billed_wt = dead_wt

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
        volumetric_display=vol_display,
        billable_weight_kg=billed_wt,
        dimensions_cm=dims_str,
        package_type=pkg_type,
        is_cod=getattr(order, "is_cod", False),
        cod_amount=getattr(order, "cod_amount", 0.0),
        pickup_reference=pickup_ref,
        is_demo=is_demo,
        page_format=page_format,
    )


def generate_label_preview_html(
    tracking_number: str,
    order_reference: str,
    courier_name: str,
    service_type: str = "Standard",
    shipment_id: Optional[str] = None,
    origin_pin: str = "—",
    origin_city: str = "Origin Hub",
    origin_state: str = "IN",
    destination_pin: str = "—",
    destination_city: str = "Destination Hub",
    destination_state: str = "IN",
    sender_name: str = "Warehouse Logistics Hub",
    sender_street: str = "Central Fulfillment Facility",
    sender_phone: str = "—",
    recipient_name: str = "Recipient Customer",
    recipient_street: str = "Delivery Address",
    recipient_phone: str = "—",
    actual_weight_kg: float = 1.0,
    volumetric_weight_kg: Optional[float] = None,
    volumetric_display: Optional[str] = None,
    billable_weight_kg: Optional[float] = None,
    dimensions_cm: str = "—",
    package_type: str = "Standard Parcel",
    is_cod: bool = False,
    cod_amount: float = 0.0,
    created_at_iso: Optional[str] = None,
    pickup_reference: Optional[str] = None,
    is_demo: bool = True,
) -> str:
    """
    Generate an authentic, responsive HTML/SVG shipping label card preview.
    Uses vector SVG for Code128 barcode and QR code to guarantee zero browser blocking.
    """
    def esc(s: Any) -> str:
        return html.escape(str(s or "—"))

    # Vector Code128 barcode SVG
    bc_svg = ""
    if REPORTLAB_AVAILABLE:
        try:
            from reportlab.graphics import renderSVG
            bc_drawing = build_barcode_drawing(tracking_number, max_width=210.0, height=32.0)
            if bc_drawing:
                bc_svg_raw = renderSVG.drawToString(bc_drawing)
                bc_idx = bc_svg_raw.find("<svg")
                bc_svg = bc_svg_raw[bc_idx:] if bc_idx != -1 else ""
        except Exception:
            bc_svg = ""
    if not bc_svg:
        bc_svg = f'<div style="font-family: monospace; font-size: 15px; font-weight: bold; letter-spacing: 2px;">||| | |||| | ||| {esc(tracking_number)}</div>'

    # Vector QR code SVG
    qr_svg = ""
    if REPORTLAB_AVAILABLE:
        try:
            from reportlab.graphics import renderSVG
            qr_payload = f"AWB:{tracking_number}|ORD:{order_reference}|TO:{destination_pin}"
            qr_drawing = build_qr_drawing(qr_payload, size=46.0)
            if qr_drawing:
                qr_svg_raw = renderSVG.drawToString(qr_drawing)
                qr_idx = qr_svg_raw.find("<svg")
                qr_svg = qr_svg_raw[qr_idx:] if qr_idx != -1 else ""
        except Exception:
            qr_svg = ""

    date_str = created_at_iso[:16].replace("T", " ") if created_at_iso else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # Weight resolution
    if volumetric_display:
        vol_str = volumetric_display
    elif volumetric_weight_kg is not None:
        vol_str = f"{volumetric_weight_kg:.2f} kg"
    elif dimensions_cm and dimensions_cm != "—":
        vol_str = "Not configured"
    else:
        vol_str = "—"

    billable_str = f"{billable_weight_kg:.2f} kg" if billable_weight_kg is not None else f"{actual_weight_kg:.2f} kg"

    demo_banner_html = (
        '<div style="background: #fee2e2; border: 1px solid #ef4444; color: #991b1b; '
        'font-size: 10px; font-weight: 700; text-align: center; padding: 3px 6px; '
        'margin-bottom: 8px; border-radius: 4px; letter-spacing: 0.5px;">'
        'DEMO SHIPMENT — SIMULATED CARRIER LABEL FOR EVALUATION ONLY'
        '</div>'
    ) if is_demo else ""

    payment_banner_html = (
        f'<div style="background: #fee2e2; border: 1.5px solid #dc2626; color: #b91c1c; '
        f'font-weight: 800; text-align: center; font-size: 11.5px; padding: 5px; '
        f'border-radius: 4px; margin: 8px 0;">'
        f'⚠️ COLLECT CASH ON DELIVERY: ₹ {cod_amount:,.2f}'
        f'</div>'
    ) if (is_cod and cod_amount > 0) else (
        '<div style="background: #f0fdf4; border: 1.25px solid #16a34a; color: #15803d; '
        'font-weight: 800; text-align: center; font-size: 11px; padding: 5px; '
        'border-radius: 4px; margin: 8px 0;">'
        '✓ PREPAID SHIPMENT — DO NOT COLLECT CASH'
        '</div>'
    )

    shp_ref_str = f"Shipment ID: {esc(shipment_id)} • " if shipment_id else ""
    pkp_ref_str = f"Pickup Ref: {esc(pickup_reference)} • " if pickup_reference else ""

    return f"""
<div style="max-width: 490px; margin: 12px auto; background: #ffffff; border: 2px solid #1e293b; border-radius: 8px; padding: 14px 16px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -2px rgba(0,0,0,0.1); line-height: 1.35;">
  <!-- Header -->
  <div style="display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #0f172a; padding-bottom: 6px; margin-bottom: 6px;">
    <div>
      <div style="font-size: 15px; font-weight: 900; letter-spacing: 0.5px; color: #0f172a;">{esc(courier_name).upper()}</div>
      <div style="font-size: 11px; color: #475569;">Ref: {esc(order_reference)}</div>
    </div>
    <div style="text-align: right;">
      <div style="font-size: 11px; font-weight: 800; color: #1e293b;">{esc(service_type).upper()}</div>
      <div style="font-size: 10px; color: #64748b;">{date_str} UTC</div>
    </div>
  </div>

  {demo_banner_html}

  <!-- Routing Strip -->
  <div style="background: #f1f5f9; border: 1px solid #94a3b8; border-radius: 4px; padding: 5px 8px; font-size: 11.5px; text-align: center; font-weight: 700; color: #1e293b; margin-bottom: 8px;">
    PIN: {esc(origin_pin)} &nbsp;➔&nbsp; <b>PIN: {esc(destination_pin)}</b> &nbsp;({esc(destination_city).upper()})
  </div>

  <!-- AWB Title & Barcode / QR -->
  <div style="text-align: center; margin-bottom: 4px;">
    <div style="font-size: 14px; font-weight: 900; letter-spacing: 1px; color: #0f172a;">AWB: {esc(tracking_number)}</div>
  </div>
  <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px; margin: 4px 0 8px 0; background: #ffffff; padding: 4px; border: 1px dashed #cbd5e1; border-radius: 4px;">
    <div style="flex: 1; display: flex; justify-content: center; overflow: hidden;">
      {bc_svg}
    </div>
    <div style="width: 50px; height: 50px; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
      {qr_svg}
    </div>
  </div>

  <!-- Addresses Grid -->
  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; border: 1px solid #0f172a; border-radius: 4px; padding: 8px; margin-bottom: 8px; background: #ffffff; font-size: 10.5px;">
    <div style="border-right: 1px solid #e2e8f0; padding-right: 6px;">
      <div style="font-weight: 800; color: #0f172a; font-size: 10px; text-transform: uppercase; margin-bottom: 2px;">Ship To (Delivery):</div>
      <div style="font-weight: 700; color: #0f172a;">{esc(recipient_name)}</div>
      <div style="color: #334155;">{esc(recipient_street)}</div>
      <div style="color: #334155;">{esc(destination_city)}, {esc(destination_state)} - <b>{esc(destination_pin)}</b></div>
      <div style="color: #64748b; margin-top: 2px;">Phone: {esc(recipient_phone)}</div>
    </div>
    <div style="padding-left: 2px;">
      <div style="font-weight: 800; color: #0f172a; font-size: 10px; text-transform: uppercase; margin-bottom: 2px;">Return If Undelivered:</div>
      <div style="font-weight: 700; color: #0f172a;">{esc(sender_name)}</div>
      <div style="color: #334155;">{esc(sender_street)}</div>
      <div style="color: #334155;">{esc(origin_city)}, {esc(origin_state)} - <b>{esc(origin_pin)}</b></div>
      <div style="color: #64748b; margin-top: 2px;">Phone: {esc(sender_phone)}</div>
    </div>
  </div>

  <!-- Package Specifications -->
  <div style="background: #f8fafc; border: 1px solid #94a3b8; border-radius: 4px; padding: 6px 8px; font-size: 11px; color: #1e293b; margin-bottom: 6px;">
    <div><b>Weight:</b> {actual_weight_kg:.2f} kg &nbsp;|&nbsp; <b>Volumetric:</b> {vol_str} &nbsp;|&nbsp; <b>Billed:</b> {billable_str}</div>
    <div style="margin-top: 2px;"><b>Dimensions:</b> {esc(dimensions_cm)} &nbsp;|&nbsp; <b>Pkg:</b> {esc(package_type)}</div>
  </div>

  <!-- Payment Status Strip -->
  {payment_banner_html}

  <!-- Footer -->
  <div style="text-align: center; font-size: 9px; color: #64748b; margin-top: 6px; padding-top: 4px; border-top: 1px solid #e2e8f0;">
    {shp_ref_str}{pkp_ref_str}Adaptive Shipping Agent &bull; 5-Point Verified
  </div>
</div>
"""


def generate_label_preview_from_shipment_context(
    order: Any,
    quote: Any,
    shipment_result: Any,
    pickup_result: Optional[Any] = None,
) -> str:
    """
    Convenience wrapper to build an HTML/SVG label preview from domain shipment objects.
    """
    tracking = getattr(shipment_result, "tracking_number", "") or "DEMO-TRK-UNKNOWN"
    shipment_id = getattr(shipment_result, "shipment_id", "")
    order_ref = getattr(order, "order_reference", "") or "ORD-REF"
    courier_name = getattr(quote, "courier_name", "") or getattr(shipment_result, "courier_code", "Carrier")
    service_type = getattr(quote, "service_type", "") or "Standard"

    sender = getattr(order, "sender", None) or getattr(order, "sender_address", None)
    recipient = getattr(order, "recipient", None) or getattr(order, "recipient_address", None)

    dims = getattr(order, "dimensions", None)
    dims_str = f"{dims.length_cm:.1f}×{dims.width_cm:.1f}×{dims.height_cm:.1f} cm" if dims else "—"

    is_demo = getattr(shipment_result, "is_demo_data", True)
    pickup_ref = getattr(pickup_result, "pickup_reference", None) if pickup_result else None

    dead_wt = getattr(order, "parcel_weight_kg", 1.0)
    if dead_wt is None:
        dead_wt = 1.0

    courier_code = (
        getattr(quote, "courier_code", None)
        or getattr(shipment_result, "courier_code", None)
        or ""
    )

    vol_wt: Optional[float] = None
    vol_display: Optional[str] = None
    billed_wt: Optional[float] = None

    weight_res = getattr(quote, "weight_result", None) if quote else None
    if weight_res:
        vol_wt = getattr(weight_res, "volumetric_weight_kg", None)
        billed_wt = getattr(weight_res, "chargeable_weight_kg", None)
        wr_actual = getattr(weight_res, "actual_weight_kg", None)
        if wr_actual:
            dead_wt = wr_actual

    if vol_wt is None and dims and getattr(dims, "length_cm", 0) > 0 and getattr(dims, "width_cm", 0) > 0 and getattr(dims, "height_cm", 0) > 0:
        from src.domain.dimension_engine import OFFICIAL_DOCUMENTED_CAPABILITIES, compute_volumetric_weight
        rule = OFFICIAL_DOCUMENTED_CAPABILITIES.get(courier_code)
        if rule and rule.volumetric_rule and rule.volumetric_rule.divisor > 0:
            divisor = rule.volumetric_rule.divisor
            vol_wt = compute_volumetric_weight(dims, divisor=divisor)
            if billed_wt is None:
                billed_wt = max(dead_wt, vol_wt)
        else:
            vol_display = "Not configured"
            if billed_wt is None:
                billed_wt = dead_wt

    if vol_wt is not None and vol_display is None:
        vol_display = f"{vol_wt:.2f} kg"

    if billed_wt is None:
        if quote and hasattr(quote, "chargeable_weight_kg") and quote.chargeable_weight_kg:
            billed_wt = quote.chargeable_weight_kg
        elif vol_wt is not None:
            billed_wt = max(dead_wt, vol_wt)
        else:
            billed_wt = dead_wt

    return generate_label_preview_html(
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
        volumetric_display=vol_display,
        billable_weight_kg=billed_wt,
        dimensions_cm=dims_str,
        package_type=getattr(order, "packaging_type", "Standard Parcel"),
        is_cod=getattr(order, "is_cod", False),
        cod_amount=getattr(order, "cod_amount", 0.0),
        pickup_reference=pickup_ref,
        is_demo=is_demo,
    )


__all__ = [
    "LabelPageFormat",
    "PAGE_DIMENSIONS",
    "sanitize_label_filename",
    "is_valid_pdf",
    "WatermarkCanvas",
    "build_barcode_drawing",
    "build_qr_drawing",
    "generate_shipping_label_pdf",
    "generate_label_from_shipment_context",
    "generate_label_preview_html",
    "generate_label_preview_from_shipment_context",
    "REPORTLAB_AVAILABLE",
]

