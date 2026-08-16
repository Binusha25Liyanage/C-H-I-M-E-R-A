"""
combined_report_pdf.py
Builds the combined SKU + Invoice report PDF, matching the confirmed
layout: Seq | Outlet Code | Outlet | Invoices | Products | Total Units |
Sales Value. One PDF per territory, saved to the given output folder.
"""

import os
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

CHERRY_RED = colors.HexColor("#B5121B")
ASH = colors.HexColor("#6E6F71")
LIGHT_ROW = colors.HexColor("#F5F5F5")

if getattr(sys, "frozen", False):
    _FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend")
else:
    _FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
LOGO_PATH = os.path.join(_FRONTEND_DIR, "assets", "chimera-logo.png")


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportHeader", fontSize=13, textColor=CHERRY_RED, spaceAfter=2, fontName="Helvetica-Bold"
    ))
    styles.add(ParagraphStyle(
        name="ReportMeta", fontSize=9, textColor=ASH, spaceAfter=8
    ))
    styles.add(ParagraphStyle(
        name="CellText", fontSize=8, leading=10
    ))
    return styles


def _outlet_row_to_table_rows(row, styles):
    """
    One outlet can have multiple product lines (like the original SKU
    report) — the first line carries Seq/Outlet Code/Outlet/Invoices,
    subsequent product lines for the same outlet leave those columns
    blank so they visually belong to the same outlet, matching the
    original report's layout convention.
    """
    lines = row["lines"]
    if not lines:
        return [[
            str(row["sequence"] or ""), row["outlet_code"], row["outlet"],
            str(row["invoices"]) if row["invoices"] else "", "", "", ""
        ]]

    table_rows = []
    for i, (product, units, value) in enumerate(lines):
        if i == 0:
            table_rows.append([
                str(row["sequence"] or ""), row["outlet_code"], row["outlet"],
                str(row["invoices"]) if row["invoices"] else "",
                Paragraph(product, styles["CellText"]), str(units), f"{value:,.2f}"
            ])
        else:
            table_rows.append([
                "", "", "", "", Paragraph(product, styles["CellText"]), str(units), f"{value:,.2f}"
            ])
    return table_rows


def build_combined_report_pdf(report, output_path):
    """
    report: one result dict from combined_report.get_route_combined_report
            (must have ok=True).
    output_path: full path to write the PDF to.
    """
    if not report.get("ok"):
        raise ValueError(f"Cannot build PDF for a failed report: {report.get('reason')}")

    styles = _styles()
    doc = SimpleDocTemplate(output_path, pagesize=A4,
                             topMargin=14 * mm, bottomMargin=14 * mm,
                             leftMargin=14 * mm, rightMargin=14 * mm)
    story = []

    if os.path.exists(LOGO_PATH):
        story.append(Image(LOGO_PATH, width=45 * mm, height=45 * mm * (768 / 1408)))
        story.append(Spacer(1, 4))

    story.append(Paragraph(f"Territory {report['territory']}", styles["ReportHeader"]))
    story.append(Paragraph(
        f"Route: {report['route']} | Sales Model: {report['sale_model']} | "
        f"Reference Date: {report['reference_date']} | Previous Visit Date: {report['previous_visit_date']}",
        styles["ReportMeta"]
    ))
    story.append(Paragraph(
        f"<b>Bills {report['bills']}</b>&nbsp;&nbsp;&nbsp;<b>Total {report['total']:,.2f}</b>",
        styles["ReportMeta"]
    ))

    header = ["Seq.", "Outlet Code", "Outlet", "Invoices", "Products", "Total Units", "Sales Value"]
    data = [header]
    for row in report["rows"]:
        data.extend(_outlet_row_to_table_rows(row, styles))

    col_widths = [10 * mm, 24 * mm, 32 * mm, 16 * mm, 62 * mm, 18 * mm, 22 * mm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CHERRY_RED),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, ASH),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_ROW]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("ALIGN", (5, 0), (6, -1), "RIGHT"),
    ]))
    story.append(table)

    if report.get("unmatched_pre_sales_customers"):
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            "<b>Note:</b> the following Pre Selling customers had orders in this window but could not be "
            "matched to an outlet by name and are NOT included in the Invoices count above: " +
            ", ".join(report["unmatched_pre_sales_customers"]),
            styles["ReportMeta"]
        ))

    doc.build(story)
    return output_path


def build_combined_reports(reports, output_folder):
    """
    Single OR bulk — pass one report in a list for single mode, or many
    for bulk. Writes one PDF per successful (ok=True) report; failed
    reports are skipped and returned separately so a bulk run's failures
    are visible rather than silently dropped.

    Returns {"written": [path, ...], "skipped": [{"territory", "reason"}, ...]}
    """
    os.makedirs(output_folder, exist_ok=True)
    written = []
    skipped = []
    for report in reports:
        if not report.get("ok"):
            skipped.append({"territory": report.get("territory"), "reason": report.get("reason")})
            continue
        safe_territory = "".join(c for c in report["territory"] if c.isalnum() or c in " _-").strip()
        path = os.path.join(output_folder, f"Combined_{safe_territory}_{report['previous_visit_date']}.pdf")
        build_combined_report_pdf(report, path)
        written.append(path)
    return {"written": written, "skipped": skipped}
