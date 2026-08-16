"""
pdf_export.py
Generates a colorful, professional PDF for the Previous Visit report.
"""

import os
import re
import sys
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Product/outlet names in this business can mix English with Sinhala script.
# Bundle a Sinhala-capable font (it ships with the app, not relying on
# whatever's installed on the machine this runs on) and register it so
# mixed-script text renders correctly instead of showing as boxes.
#
# When this app is packaged into a standalone .exe (via build.py /
# PyInstaller), bundled data files like this font are extracted to a
# temporary folder at runtime, given by sys._MEIPASS — NOT next to this
# script file. Using __file__ alone would silently fail to find the font
# once frozen (the Sinhala text would just stop rendering, with no error),
# so this checks for frozen mode first.
if getattr(sys, "frozen", False):
    _RESOURCE_BASE = sys._MEIPASS
else:
    _RESOURCE_BASE = os.path.dirname(os.path.abspath(__file__))

_FONTS_DIR = os.path.join(_RESOURCE_BASE, "fonts")
_SINHALA_REGULAR = os.path.join(_FONTS_DIR, "NotoSansSinhala-Regular.ttf")
_SINHALA_BOLD = os.path.join(_FONTS_DIR, "NotoSansSinhala-Bold.ttf")
SINHALA_FONT_AVAILABLE = os.path.exists(_SINHALA_REGULAR)

if SINHALA_FONT_AVAILABLE:
    pdfmetrics.registerFont(TTFont("NotoSinhala", _SINHALA_REGULAR))
    if os.path.exists(_SINHALA_BOLD):
        pdfmetrics.registerFont(TTFont("NotoSinhala-Bold", _SINHALA_BOLD))

_SINHALA_RUN_RE = re.compile(r"[\u0D80-\u0DFF]+")


def sinhala_safe(text):
    """
    Escapes text for use inside a reportlab Paragraph, and wraps any Sinhala
    script runs in a <font> tag pointing at the bundled Sinhala font so they
    render instead of showing as missing-glyph boxes. Non-Sinhala text is
    left in the paragraph's own font.
    """
    if text is None:
        return ""
    text = str(text)
    if not SINHALA_FONT_AVAILABLE or not _SINHALA_RUN_RE.search(text):
        return _xml_escape(text)

    out = []
    last = 0
    for m in _SINHALA_RUN_RE.finditer(text):
        out.append(_xml_escape(text[last:m.start()]))
        out.append(f'<font name="NotoSinhala">{_xml_escape(m.group())}</font>')
        last = m.end()
    out.append(_xml_escape(text[last:]))
    return "".join(out)


# Brand-ish palette
NAVY = colors.HexColor("#1F3864")
BLUE = colors.HexColor("#2E74B5")
LIGHT_BLUE = colors.HexColor("#DCE6F1")
GREEN = colors.HexColor("#2E8B57")
LIGHT_GREEN = colors.HexColor("#E2F0D9")
AMBER = colors.HexColor("#C55A11")
LIGHT_AMBER = colors.HexColor("#FCE4D6")
GREY = colors.HexColor("#F2F2F2")
WHITE = colors.white


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"],
        textColor=NAVY, fontSize=20, spaceAfter=2, alignment=TA_LEFT
    ))
    styles.add(ParagraphStyle(
        name="SubTitle", parent=styles["Normal"],
        textColor=colors.HexColor("#555555"), fontSize=10.5, spaceAfter=14, alignment=TA_LEFT
    ))
    styles.add(ParagraphStyle(
        name="GroupHeader", parent=styles["Heading2"],
        textColor=WHITE, backColor=NAVY, fontSize=12,
        spaceBefore=10, spaceAfter=6, alignment=TA_LEFT,
        leftIndent=6, borderPadding=(4, 4, 4, 4)
    ))
    styles.add(ParagraphStyle(
        name="CellText", parent=styles["Normal"], fontSize=8.3, leading=10.5
    ))
    styles.add(ParagraphStyle(
        name="CellTextBold", parent=styles["Normal"], fontSize=8.3, leading=10.5,
        fontName="Helvetica-Bold"
    ))
    return styles


def build_previous_visit_pdf(output_path, report_date, group_label, records):
    """
    records: list of dicts with keys:
        territory, group, sales_rep, current_route, previous_date, previous_route, status
        status is one of: 'ok', 'no_current_visit', 'no_previous_match'
    """
    styles = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=landscape(A4),
        topMargin=1.4 * cm, bottomMargin=1.2 * cm,
        leftMargin=1.4 * cm, rightMargin=1.4 * cm,
    )

    story = []
    story.append(Paragraph("Previous Visit Report", styles["ReportTitle"]))
    story.append(Paragraph(
        f"Reference date: <b>{report_date}</b> &nbsp;|&nbsp; Group: <b>{group_label}</b> "
        f"&nbsp;|&nbsp; Territories: <b>{len(records)}</b>",
        styles["SubTitle"]
    ))

    # group records by their group for sectioned layout
    by_group = {}
    for rec in records:
        by_group.setdefault(rec["group"], []).append(rec)

    header = ["Territory", "Sale Model", "Sales Rep", "Current Route (Ref. Date)",
              "Previous Visit Date", "Previous Route"]
    col_widths = [2.8 * cm, 2.4 * cm, 3.4 * cm, 6.6 * cm, 3.0 * cm, 6.6 * cm]

    for group_name in sorted(by_group.keys()):
        story.append(Paragraph(f"Group: {group_name}", styles["GroupHeader"]))

        table_data = [[Paragraph(h, styles["CellTextBold"]) for h in header]]
        row_styles = []

        for i, rec in enumerate(by_group[group_name], start=1):
            if rec["status"] == "no_current_visit":
                current_cell = Paragraph("<i>No itinerary for this date</i>", styles["CellText"])
                prev_date_cell = Paragraph("-", styles["CellText"])
                prev_route_cell = Paragraph("-", styles["CellText"])
                row_bg = LIGHT_AMBER
            elif rec["status"] == "no_previous_match":
                current_cell = Paragraph(sinhala_safe(rec["current_route"]), styles["CellText"])
                prev_date_cell = Paragraph("-", styles["CellText"])
                prev_route_cell = Paragraph("<i>No earlier match found</i>", styles["CellText"])
                row_bg = LIGHT_AMBER
            else:
                current_cell = Paragraph(sinhala_safe(rec["current_route"]), styles["CellText"])
                prev_date_cell = Paragraph(rec["previous_date"], styles["CellText"])
                prev_route_cell = Paragraph(sinhala_safe(rec["previous_route"]), styles["CellText"])
                row_bg = LIGHT_GREEN if i % 2 == 0 else WHITE

            table_data.append([
                Paragraph(sinhala_safe(rec["territory"]), styles["CellTextBold"]),
                Paragraph(rec.get("sale_model") or "-", styles["CellText"]),
                Paragraph(sinhala_safe(rec.get("sales_rep")) or "-", styles["CellText"]),
                current_cell, prev_date_cell, prev_route_cell,
            ])
            row_styles.append(("BACKGROUND", (0, i), (-1, i), row_bg))

        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
        base_style = [
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        tbl.setStyle(TableStyle(base_style + row_styles))
        story.append(tbl)
        story.append(Spacer(1, 10))

    doc.build(story)
    return output_path


def build_customer_sales_pdf(output_path, ref_date, group_label, van_records, pre_records):
    """
    van_records / pre_records: list of dicts with keys:
        territory, group, route, date, customer, sales_value, extra (e.g. outlet or SO status)
    Produces one PDF with a Van Selling section and a Pre Selling section,
    each grouped by responsible-person Group.
    """
    styles = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=landscape(A4),
        topMargin=1.4 * cm, bottomMargin=1.2 * cm,
        leftMargin=1.4 * cm, rightMargin=1.4 * cm,
    )

    story = []
    story.append(Paragraph("Customer Sales by Route Report", styles["ReportTitle"]))
    van_total = sum(r["sales_value"] for r in van_records)
    pre_total = sum(r["sales_value"] for r in pre_records)
    story.append(Paragraph(
        f"Reference date: <b>{ref_date}</b> &nbsp;|&nbsp; Group: <b>{group_label}</b> "
        f"&nbsp;|&nbsp; Van Selling customers: <b>{len(van_records)}</b> "
        f"(Rs. {van_total:,.2f}) &nbsp;|&nbsp; Pre Selling customers: <b>{len(pre_records)}</b> "
        f"(Rs. {pre_total:,.2f})",
        styles["SubTitle"]
    ))

    def render_section(title, records, date_col_label):
        story.append(Paragraph(title, styles["GroupHeader"]))
        if not records:
            story.append(Paragraph("<i>No matching customers found.</i>", styles["CellText"]))
            story.append(Spacer(1, 10))
            return

        by_group = {}
        for rec in records:
            by_group.setdefault(rec["group"], []).append(rec)

        header = ["Territory", "Route", "Previous Visit Date", "Customer", date_col_label, "Sales Value (Rs.)"]
        col_widths = [2.8 * cm, 6.8 * cm, 3.2 * cm, 5.7 * cm, 3.0 * cm, 3.5 * cm]

        for group_name in sorted(by_group.keys()):
            recs = by_group[group_name]
            group_total = sum(r["sales_value"] for r in recs)
            story.append(Paragraph(
                f"{group_name}  —  {len(recs)} customers, Rs. {group_total:,.2f}",
                ParagraphStyle(name="SubGroup", parent=styles["Normal"], fontSize=10.5,
                                textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=4)
            ))

            table_data = [[Paragraph(h, styles["CellTextBold"]) for h in header]]
            row_styles = []
            for i, rec in enumerate(recs, start=1):
                table_data.append([
                    Paragraph(sinhala_safe(rec["territory"]), styles["CellText"]),
                    Paragraph(sinhala_safe(rec["route"]), styles["CellText"]),
                    Paragraph(rec["previous_visit_date"], styles["CellText"]),
                    Paragraph(sinhala_safe(rec["customer"]), styles["CellText"]),
                    Paragraph(rec["date"], styles["CellText"]),
                    Paragraph(f"{rec['sales_value']:,.2f}", styles["CellText"]),
                ])
                row_styles.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GREEN if i % 2 == 0 else WHITE))

            tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
            base_style = [
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ALIGN", (4, 0), (4, -1), "RIGHT"),
            ]
            tbl.setStyle(TableStyle(base_style + row_styles))
            story.append(tbl)
            story.append(Spacer(1, 8))
        story.append(Spacer(1, 6))

    render_section("Van Selling — Customers (Invoices)", van_records, "Invoice Date")
    render_section("Pre Selling — Customers (Sales Orders)", pre_records, "Order Date")

    doc.build(story)
    return output_path


def build_outlets_pdf(output_path, territory_label, route_label, records):
    """
    records: list of tuples (territory, route, sequence, outlet_code, outlet, category)
    sorted ascending by sequence (already sorted by the caller/db query).
    Grouped by Territory, then Route, within the PDF.
    """
    styles = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=landscape(A4),
        topMargin=1.4 * cm, bottomMargin=1.2 * cm,
        leftMargin=1.4 * cm, rightMargin=1.4 * cm,
    )

    story = []
    story.append(Paragraph("Territory / Route-wise Customer List", styles["ReportTitle"]))
    story.append(Paragraph(
        f"Territory: <b>{territory_label}</b> &nbsp;|&nbsp; Route: <b>{route_label}</b> "
        f"&nbsp;|&nbsp; Outlets: <b>{len(records)}</b>",
        styles["SubTitle"]
    ))

    by_territory = {}
    for rec in records:
        territory, route, sequence, outlet_code, outlet, category = rec
        by_territory.setdefault(territory, {}).setdefault(route, []).append(rec)

    header = ["Seq.", "Outlet Code", "Outlet", "Category"]
    col_widths = [1.8 * cm, 4.0 * cm, 9.0 * cm, 6.5 * cm]

    for territory in sorted(by_territory.keys()):
        story.append(Paragraph(f"Territory: {territory}", styles["GroupHeader"]))
        routes = by_territory[territory]
        for route in sorted(routes.keys()):
            recs = routes[route]
            story.append(Paragraph(
                f"Route: {sinhala_safe(route)}  —  {len(recs)} outlets",
                ParagraphStyle(name="SubGroup2", parent=styles["Normal"], fontSize=10.5,
                                textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=4)
            ))

            table_data = [[Paragraph(h, styles["CellTextBold"]) for h in header]]
            row_styles = []
            for i, (territory_, route_, sequence, outlet_code, outlet, category) in enumerate(recs, start=1):
                seq_disp = str(sequence) if sequence is not None else "-"
                table_data.append([
                    Paragraph(seq_disp, styles["CellText"]),
                    Paragraph(sinhala_safe(outlet_code), styles["CellText"]),
                    Paragraph(sinhala_safe(outlet), styles["CellText"]),
                    Paragraph(sinhala_safe(category) or "-", styles["CellText"]),
                ])
                row_styles.append(("BACKGROUND", (0, i), (-1, i), LIGHT_BLUE if i % 2 == 0 else WHITE))

            tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
            base_style = [
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ]
            tbl.setStyle(TableStyle(base_style + row_styles))
            story.append(tbl)
            story.append(Spacer(1, 8))
        story.append(Spacer(1, 6))

    doc.build(story)
    return output_path


def build_sku_route_report_pdf(output_path, territory, route, ref_date, previous_visit_date, sale_model, records):
    """
    Matches the reference format: header block (Territory / Route / Bills /
    Total), then one row per outlet in sequence order, with every product
    line for that outlet stacked inside the same row (Products / Total
    Units / Sales Value columns), and a blank row for outlets with no sale.
    'Bills' = count of outlets with at least one sale line (distinct
    customers, not distinct invoices).

    ref_date is the date the report was requested for (the route currently
    scheduled that day); previous_visit_date is the most recent earlier
    date that same route was actually run, which is the date the SKU
    sales figures below are drawn from. Both are shown since they can
    differ — the figures reflect previous_visit_date, not ref_date.
    """
    styles = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=1.4 * cm, bottomMargin=1.2 * cm,
        leftMargin=1.2 * cm, rightMargin=1.2 * cm,
    )

    bills = sum(1 for r in records if r["lines"])
    total = sum(r["subtotal"] for r in records)

    story = []
    story.append(Paragraph(f"Territory {sinhala_safe(territory)}", styles["ReportTitle"]))
    story.append(Paragraph(
        f"Route: <b>{sinhala_safe(route)}</b> &nbsp;|&nbsp; Sales Model: <b>{sale_model}</b> "
        f"&nbsp;|&nbsp; Reference Date: <b>{ref_date}</b> "
        f"&nbsp;|&nbsp; Previous Visit Date: <b>{previous_visit_date}</b>",
        styles["SubTitle"]
    ))
    story.append(Paragraph(
        f"Bills <b>{bills}</b> &nbsp;&nbsp;&nbsp; Total <b>{total:,.2f}</b>",
        ParagraphStyle(name="BillsTotal", parent=styles["Normal"], fontSize=12,
                        textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=10)
    ))

    header = ["Seq.", "Outlet Code", "Outlet", "Products", "Total Units", "Sales Value"]
    col_widths = [1.3 * cm, 2.9 * cm, 3.6 * cm, 6.8 * cm, 2.0 * cm, 2.4 * cm]

    table_data = [[Paragraph(h, styles["CellTextBold"]) for h in header]]
    row_styles = []

    for i, rec in enumerate(records, start=1):
        seq_disp = str(rec["sequence"]) if rec["sequence"] is not None else "-"
        if rec["lines"]:
            products_html = "<br/>".join(sinhala_safe(p) for p, u, v in rec["lines"])
            units_html = "<br/>".join(f"{u:g}" for p, u, v in rec["lines"])
            value_html = "<br/>".join(f"{v:,.2f}" for p, u, v in rec["lines"])
            row_bg = LIGHT_GREEN if i % 2 == 0 else WHITE
        else:
            products_html = ""
            units_html = ""
            value_html = ""
            row_bg = GREY

        table_data.append([
            Paragraph(seq_disp, styles["CellText"]),
            Paragraph(sinhala_safe(rec["outlet_code"]), styles["CellText"]),
            Paragraph(sinhala_safe(rec["outlet"]), styles["CellText"]),
            Paragraph(products_html, styles["CellText"]),
            Paragraph(units_html, styles["CellText"]),
            Paragraph(value_html, styles["CellText"]),
        ])
        row_styles.append(("BACKGROUND", (0, i), (-1, i), row_bg))

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    base_style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (4, 0), (5, -1), "RIGHT"),
    ]
    tbl.setStyle(TableStyle(base_style + row_styles))
    story.append(tbl)

    doc.build(story)
    return output_path
