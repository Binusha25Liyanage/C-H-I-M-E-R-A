"""
daily_reports_pdf.py
Renders the SKU / Pivot / Hourly reports (from daily_reports.py) to PDFs
and WhatsApp-ready PNGs, in one of two output styles:

  "classic"       Same structure and look as the SalesPulse reports the
                  managers already know (navy headers, centred titles,
                  summary box, yellow "First Bill Time" badge, vertical
                  hourly headers). No logo.

  "professional"  Chimera layout: black-and-white logo header, KPI strip,
                  right-aligned figures, "SFA data as of" freshness line,
                  data-notes box and footer.

Numbers are identical in both styles — only presentation differs. Both
show data-quality notes (missing inputs, unassigned territories, returns)
so a reader is never misled; in the classic style they are a small
footnote so the familiar layout is kept.

Pages are sized to their content (no half-empty A4 pages). Wide pages are
rotated to portrait in the PNGs so they fill a phone screen.
"""

import os
import sys
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Flowable, Image, Paragraph, Spacer, Table, TableStyle

import sfa_store

STYLES = ("classic", "professional")

# ---- palettes --------------------------------------------------------------
# Classic (SalesPulse look)
NAVY = colors.HexColor("#1B1B6B")
C_GRAY = colors.HexColor("#555555")
C_LATE_BG = colors.HexColor("#FFCCCC")
C_LATE_TX = colors.HexColor("#8B0000")
C_BADGE_BG = colors.HexColor("#FFF3B0")
C_VIOLET = colors.HexColor("#DCCBF5")
C_NEG_BG = colors.HexColor("#FFE9C7")

# Professional (Chimera)
INK = colors.HexColor("#1C1C1E")
ASH = colors.HexColor("#6E6F71")
CHERRY = colors.HexColor("#B5121B")
GRID = colors.HexColor("#D5D6D8")
ZEBRA = colors.HexColor("#F7F7F8")
PANEL = colors.HexColor("#F3F3F4")
MUTED = colors.HexColor("#A3A4A6")
LATE_BG = colors.HexColor("#FBE3E3")
NEG_BG = colors.HexColor("#FFF1D6")
ACH_BG = colors.HexColor("#E6F4EA")

if getattr(sys, "frozen", False):
    _FRONTEND_DIR = os.path.join(sys._MEIPASS, "frontend")
else:
    _FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
MONO_LOGO = os.path.join(_FRONTEND_DIR, "assets", "chimera-logo-mono.png")

MARGIN = 10 * mm

SHORT_LABELS = {"invoices": "Invoices", "sales_orders": "Sales Orders", "sku_van": "SKU Van",
                "sku_pre": "SKU Pre", "unproductive": "Unproductive Visits"}
REPORT_INPUTS = {"sku": ("sku_van", "sku_pre"), "pivot": ("invoices", "sales_orders"),
                 "hourly": ("invoices", "sales_orders", "unproductive")}


def _ps(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9, leading=11, textColor=INK)
    base.update(kw)
    return ParagraphStyle(name, **base)


def _money(v, decimals=2):
    return f"{v:,.{decimals}f}"


def _safe(name):
    return "".join(c for c in name if c.isalnum() or c in " _-").strip() or "report"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ampm(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h % 12) or 12:02d}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _focus_short(focus):
    """'Lakposha 200g' -> 'Lakposha200' (the SalesPulse column label)."""
    s = focus.replace(" ", "")
    return s[:-1] if len(s) > 1 and s[-1].lower() == "g" and s[-2].isdigit() else s


def _as_of_line(date, kind):
    """When SFA generated each input this report uses (from the export banner), or when it was imported."""
    st = {d["dataset"]: d for d in sfa_store.get_dataset_status(date)["datasets"]}
    parts = []
    for key in REPORT_INPUTS[kind]:
        d = st[key]
        if not d["loaded"]:
            parts.append(f"{SHORT_LABELS[key]} NOT LOADED")
        elif d["generated_at"]:
            parts.append(f"{SHORT_LABELS[key]} {' '.join(d['generated_at'].rsplit(' ', 2)[-2:])}")
        else:
            parts.append(f"{SHORT_LABELS[key]} (imported {d['imported_at'][11:16]})")
    return "SFA data as of: " + " | ".join(parts)


# ---- custom flowables --------------------------------------------------------
class RoundedBox(Flowable):
    """Text inside a rounded border (SalesPulse summary box / First Bill Time badge)."""

    def __init__(self, text, font="Helvetica", size=9, color=NAVY, stroke=NAVY, fill=None,
                 pad_x=8, pad_y=4, radius=4, stroke_width=0.8):
        super().__init__()
        self.text, self.font, self.size, self.color = text, font, size, color
        self.stroke, self.fill, self.pad_x, self.pad_y = stroke, fill, pad_x, pad_y
        self.radius, self.stroke_width = radius, stroke_width
        self.hAlign = "CENTER"

    def wrap(self, aw, ah):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        self.w = min(aw, stringWidth(self.text, self.font, self.size) + 2 * self.pad_x)
        self.h = self.size * 1.25 + 2 * self.pad_y
        return self.w, self.h

    def draw(self):
        c = self.canv
        c.saveState()
        c.setLineWidth(self.stroke_width)
        c.setStrokeColor(self.stroke)
        if self.fill is not None:
            c.setFillColor(self.fill)
        c.roundRect(0, 0, self.w, self.h, self.radius, stroke=1, fill=1 if self.fill is not None else 0)
        c.setFillColor(self.color)
        c.setFont(self.font, self.size)
        c.drawCentredString(self.w / 2, self.pad_y + self.size * 0.3, self.text)
        c.restoreState()


class VText(Flowable):
    """Vertical (rotated 90°) header text, as in the SalesPulse hourly report."""

    def __init__(self, text, size=6.5, height=34 * mm, color=colors.white, font="Helvetica-Bold"):
        super().__init__()
        self.text, self.size, self.h, self.color, self.font = text, size, height, color, font

    def wrap(self, aw, ah):
        self.w = min(aw, self.size * 1.6)
        return self.w, self.h

    def draw(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        c = self.canv
        sw = stringWidth(self.text, self.font, self.size)
        c.saveState()
        c.setFillColor(self.color)
        c.setFont(self.font, self.size)
        # Reads top-to-bottom on the PDF page, so it is upright in the WhatsApp
        # image (wide pages are rotated 90° counter-clockwise for phones).
        c.translate(self.w / 2 - self.size * 0.35, min(self.h, (self.h + sw) / 2))
        c.rotate(-90)
        c.drawString(0, 0, self.text)
        c.restoreState()


class HRule(Flowable):
    def __init__(self, width_pts=1.2, color=INK, space=4):
        super().__init__()
        self.lw, self.color, self.space = width_pts, color, space

    def wrap(self, aw, ah):
        self.w = aw
        return aw, self.lw + 2 * self.space

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.lw)
        self.canv.line(0, self.space, self.w, self.space)


# ---- page rendering ----------------------------------------------------------
def _render_pages(path, pages):
    """
    pages: [{"width": points, "flow": [flowables], "min_height": optional}].
    Each page's height is computed from its content.
    """
    c = rl_canvas.Canvas(path)
    for page in pages:
        avail = page["width"] - 2 * MARGIN
        sizes = [f.wrap(avail, 100_000) for f in page["flow"]]
        height = max(sum(h for _, h in sizes) + 2 * MARGIN, page.get("min_height", 0))
        c.setPageSize((page["width"], height))
        y = height - MARGIN
        for f, (w, h) in zip(page["flow"], sizes):
            y -= h
            align = getattr(f, "hAlign", "LEFT")
            if isinstance(f, Table) or align == "CENTER":
                x = MARGIN + max(0, (avail - w) / 2)
            else:
                x = MARGIN
            f.drawOn(c, x, y)
        c.showPage()
    c.save()
    return path


# ============================================================================
# CLASSIC style (SalesPulse look)
# ============================================================================
_CL_TITLE = _ps("cl_t", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY, alignment=1)
_CL_SUB = _ps("cl_s", fontName="Helvetica-Oblique", fontSize=10.5, leading=13, textColor=C_GRAY, alignment=1)
_CL_LEGEND = _ps("cl_l", fontSize=9.5, leading=12, textColor=NAVY, alignment=1)
_CL_HDR = _ps("cl_h", fontName="Helvetica-Bold", fontSize=11, leading=13, textColor=colors.white, alignment=1)
_CL_FOOT = _ps("cl_f", fontName="Helvetica-Oblique", fontSize=7.5, leading=9.5, textColor=C_GRAY)
_CL_TOTAL = _ps("cl_tt", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY)


def _classic_footnotes(notes):
    if not notes:
        return []
    return [Spacer(1, 6)] + [Paragraph("* " + _esc(n), _CL_FOOT) for n in notes]


def _classic_sku(rep):
    pages = []
    flabel = _focus_short(rep["focus_label"])
    for g in rep["groups"]:
        t = g["totals"]
        head = ["Territory", "Total Bills", "Total Bill Value", f"Bills &lt; {rep['small_n']} Items",
                f"{_esc(flabel)} Bills"]
        data = [[Paragraph(h, _CL_HDR) for h in head]]
        style = [("BACKGROUND", (0, 0), (-1, 0), NAVY),
                 ("GRID", (0, 0), (-1, -1), 0.6, NAVY),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                 ("ALIGN", (0, 1), (0, -1), "LEFT"),
                 ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                 ("FONTNAME", (1, 1), (-1, -1), "Helvetica"),
                 ("FONTSIZE", (0, 1), (-1, -1), 11),
                 ("TEXTCOLOR", (0, 1), (-1, -1), NAVY),
                 ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        for i, r in enumerate(g["rows"], start=1):
            data.append([r["territory"], r["bills"], _money(r["value"]), r["small_bills"], r["focus_bills"]])
            if r["value"] < 0:
                style.append(("BACKGROUND", (2, i), (2, i), C_NEG_BG))
        table = Table(data, colWidths=[52 * mm, 30 * mm, 40 * mm, 34 * mm, 38 * mm], repeatRows=1)
        table.setStyle(TableStyle(style))
        summary = (f"Summary: {t['territories']} Territories | Total Bills: {t['bills']} | "
                   f"Total Value: {_money(t['value'])} LKR | Bills < {rep['small_n']} Items: {t['small_bills']} | "
                   f"{flabel} Bills: {t['focus_bills']}")
        notes = [n for n in rep["notes"]
                 if not n.startswith("Returns") or any(r["negative_bills"] for r in g["rows"])]
        flow = [Paragraph(f"SKU Report - {_esc(g['group'])}", _CL_TITLE), Spacer(1, 4),
                Paragraph(f"Date: {rep['date']} | Territories: {t['territories']} | With Data: {t['with_data']} | "
                          f"No Data: {t['territories'] - t['with_data']}", _CL_SUB),
                Spacer(1, 14), table, Spacer(1, 18),
                RoundedBox(summary, size=9, color=NAVY, stroke=NAVY)]
        flow += _classic_footnotes(notes)
        pages.append({"width": 240 * mm, "flow": flow, "name": f"SKU Report - {g['group']}"})
    return pages


def _ordinal(i):
    return ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"][i] if i < 6 else f"#{i + 1}"


def _classic_pivot(rep):
    pages = []
    n = rep["n"]
    ref12 = _ampm(rep["first_bill_time"])
    for g in rep["groups"]:
        head = ["Territory"]
        for i in range(n):
            o = _ordinal(i)
            head += [f"{o} Bill Time", f"{o} Bill Gap", f"{o} Bill Value"]
        data = [[Paragraph(h, _CL_HDR) for h in head]]
        style = [("BACKGROUND", (0, 0), (-1, 0), NAVY),
                 ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                 ("ALIGN", (0, 1), (0, -1), "LEFT"),
                 ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                 ("FONTSIZE", (0, 1), (-1, -1), 11),
                 ("TEXTCOLOR", (0, 1), (-1, -1), NAVY),
                 ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
        for ri, r in enumerate(g["rows"], start=1):
            row = [r["territory"]]
            for i in range(n):
                if i < len(r["bills"]):
                    b = r["bills"][i]
                    gap = f"{b['gap']}m" if not b["early"] else f"-{-b['gap']}m"
                    row += [b["time"], gap, _money(b["value"], 0)]
                    col = 1 + i * 3
                    if b["late"]:
                        style += [("BACKGROUND", (col + 1, ri), (col + 1, ri), C_LATE_BG),
                                  ("TEXTCOLOR", (col + 1, ri), (col + 1, ri), C_LATE_TX),
                                  ("FONTNAME", (col + 1, ri), (col + 1, ri), "Helvetica-Bold")]
                    if b["negative"]:
                        style.append(("BACKGROUND", (col + 2, ri), (col + 2, ri), C_NEG_BG))
                else:
                    row += ["-", "-", "-"]
            data.append(row)
        widths = [44 * mm] + [24 * mm, 22 * mm, 25 * mm] * n
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle(style))
        flow = [Paragraph(f"Pivot Report &nbsp;&mdash;&nbsp; {_esc(g['group'])}",
                          _ps("cl_pt", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY,
                              alignment=1)),
                Spacer(1, 6),
                Paragraph(f"Date: {rep['date']} &nbsp;|&nbsp; Bills: 1 - {n} &nbsp;|&nbsp; "
                          f"Ref: {rep['first_bill_time']} &nbsp;|&nbsp; Red = Gap &gt;= {rep['red_minutes']}m", _CL_SUB),
                Spacer(1, 10),
                RoundedBox(f"First Bill Time  -  {ref12}", font="Helvetica-Bold", size=15, color=C_LATE_TX,
                           stroke=C_LATE_TX, fill=C_BADGE_BG, pad_x=12, pad_y=5, radius=6, stroke_width=1.4),
                Spacer(1, 10),
                Paragraph(f"First Bill Gap = minutes after {ref12} &nbsp;&nbsp;|&nbsp;&nbsp; Other Gaps = minutes "
                          f"after the previous bill &nbsp;&nbsp;|&nbsp;&nbsp; Red box = {rep['red_minutes']} minutes "
                          "or more", _CL_LEGEND),
                Spacer(1, 10), table]
        flow += _classic_footnotes(rep["notes"])
        pages.append({"width": max(sum(widths) + 2 * MARGIN + 8 * mm, 200 * mm), "flow": flow,
                      "name": f"Pivot Report - {g['group']}"})
    return pages


def _classic_hourly(rep, rows_per_page=20):
    buckets = rep["buckets"]
    small_hdr = _ps("cl_hh", fontName="Helvetica-Bold", fontSize=8.5, leading=10, textColor=colors.white,
                    alignment=1)
    head = [Paragraph("Group", small_hdr), Paragraph("Territory", small_hdr), Paragraph("Type", small_hdr)]
    for b in buckets:
        head += [VText(f"{b} BC|UPC"), VText(f"{b} SV")]
    head += [VText("Total BC|UPC"), VText("Total SV")]
    chunks = [rep["rows"][i:i + rows_per_page] for i in range(0, len(rep["rows"]), rows_per_page)] or [[]]
    pages = []
    hdr_note = (f"Date: {rep['date']} &nbsp;|&nbsp; Buckets: {buckets[0]} … {buckets[-1]} &nbsp;|&nbsp; "
                "Each bucket shows BC (productive) | UPC (unproductive)")
    for pi, chunk in enumerate(chunks, start=1):
        data = [head]
        style = [("BACKGROUND", (0, 0), (-1, 0), NAVY),
                 ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#8C8C9A")),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                 ("FONTSIZE", (0, 1), (-1, -1), 6.5),
                 ("TEXTCOLOR", (0, 1), (-1, -1), NAVY),
                 ("TOPPADDING", (0, 1), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 1), (-1, -1), 2.5),
                 ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]
        for ri, r in enumerate(chunk, start=1):
            row = [r["group"], r["territory"], r["model"] if r["model"] in ("Van", "Pre") else "-"]
            for c in r["cells"] + [r["total"]]:
                row += [f"{c['bc']}|{c['upc']}", _money(c["sv"])]
            data.append(row)
            if r["achieved"]:
                style.append(("BACKGROUND", (0, ri), (-1, ri), C_VIOLET))
        widths = [20 * mm, 27 * mm, 11 * mm] + [11 * mm, 15 * mm] * (len(buckets) + 1)
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle(style))
        flow = [Paragraph("Hourly Bill Count (BC) &amp; Sale Value (SV) Report", _ps(
                    "cl_ht", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=NAVY, alignment=1)),
                Spacer(1, 3),
                Paragraph(hdr_note + f" &nbsp;|&nbsp; Page {pi} of {len(chunks)}",
                          _ps("cl_hs", fontName="Helvetica-Oblique", fontSize=7.5, leading=9, textColor=C_GRAY,
                              alignment=1)),
                Spacer(1, 6),
                Paragraph(f"Total Bill Count (All Territories): {rep['grand']['bc']}", _CL_TOTAL),
                Paragraph(f"Total Sale Value (All Territories): Rs. {_money(rep['grand']['sv'])}", _CL_TOTAL),
                Spacer(1, 8), table]
        if pi == len(chunks):
            flow += [Spacer(1, 4), Paragraph(
                "Violet row = daily target achieved (bills AND sale value). Bills include invoices and pending "
                "sales orders.", _CL_FOOT)]
            flow += _classic_footnotes(rep["notes"])
        pages.append({"width": sum(widths) + 2 * MARGIN + 6 * mm, "flow": flow,
                      "name": f"Hourly BC-SV Report - Page {pi}" if len(chunks) > 1 else "Hourly BC-SV Report"})
    return pages


# ============================================================================
# PROFESSIONAL style (Chimera)
# ============================================================================
_PR_TITLE = _ps("pr_t", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=INK)
_PR_KICK = _ps("pr_k", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=ASH)
_PR_SUB = _ps("pr_s", fontSize=8.5, leading=11, textColor=ASH)
_PR_RIGHT = _ps("pr_r", fontName="Helvetica-Bold", fontSize=11, leading=13, textColor=INK, alignment=2)
_PR_RIGHT_S = _ps("pr_rs", fontSize=7.5, leading=9.5, textColor=ASH, alignment=2)
_PR_HDR = _ps("pr_h", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white, alignment=1)
_PR_KPI_L = _ps("pr_kl", fontName="Helvetica-Bold", fontSize=6.8, leading=8, textColor=ASH)
_PR_KPI_V = _ps("pr_kv", fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=INK)
_PR_NOTE = _ps("pr_n", fontSize=7.5, leading=9.5, textColor=INK)
_PR_FOOT = _ps("pr_f", fontSize=6.8, leading=8.5, textColor=ASH)


def _pro_header(kicker, title, subtitle, date, kind, avail_hint):
    logo = Image(MONO_LOGO, width=17 * mm, height=17 * mm) if os.path.exists(MONO_LOGO) else ""
    left = [Paragraph(kicker.upper(), _PR_KICK), Paragraph(_esc(title), _PR_TITLE), Paragraph(subtitle, _PR_SUB)]
    right = [Paragraph(date, _PR_RIGHT), Paragraph(_as_of_line(date, kind).replace(" | ", "<br/>"), _PR_RIGHT_S)]
    t = Table([[logo, left, right]], colWidths=[21 * mm, max(60 * mm, avail_hint - 83 * mm), 62 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    t.hAlign = "LEFT"
    return [t, HRule(1.4, INK, 5)]


def _pro_kpis(items, total_width):
    cells = [[Paragraph(_esc(label.upper()), _PR_KPI_L), Paragraph(value, _PR_KPI_V)] for label, value in items]
    w = total_width / len(items)
    t = Table([cells], colWidths=[w] * len(items))
    style = [("BACKGROUND", (0, 0), (-1, -1), PANEL), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]
    for i in range(1, len(items)):
        style.append(("LINEBEFORE", (i, 0), (i, 0), 2, colors.white))
    t.setStyle(TableStyle(style))
    t.hAlign = "LEFT"
    return t


def _pro_notes(notes, width):
    if not notes:
        return []
    body = [[Paragraph("DATA NOTES", _PR_KPI_L)]] + [[Paragraph("&bull; " + _esc(n), _PR_NOTE)] for n in notes]
    t = Table(body, colWidths=[width])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, GRID), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 2),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (0, 0), 6),
                           ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    t.hAlign = "LEFT"
    return [Spacer(1, 8), t]


def _pro_footer(page_no, pages_total):
    return [Spacer(1, 8), HRule(0.5, GRID, 2),
            Paragraph(f"CHIMERA &nbsp;&middot;&nbsp; Sales Operations Reporting &nbsp;&middot;&nbsp; Generated "
                      f"{datetime.now():%Y-%m-%d %H:%M} &nbsp;&middot;&nbsp; Internal use only &nbsp;&middot;&nbsp; "
                      f"Page {page_no} of {pages_total}", _PR_FOOT)]


def _pro_table_style(n_header_rows=1):
    return [("BACKGROUND", (0, 0), (-1, n_header_rows - 1), INK),
            ("TEXTCOLOR", (0, 0), (-1, n_header_rows - 1), colors.white),
            ("FONTNAME", (0, n_header_rows), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, n_header_rows), (-1, -1), 8.5),
            ("TEXTCOLOR", (0, n_header_rows), (-1, -1), INK),
            ("LINEBELOW", (0, n_header_rows), (-1, -1), 0.4, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]


def _pro_sku(rep):
    pages, n_pages = [], len(rep["groups"])
    flabel = rep["focus_label"]
    width = 196 * mm
    avail = width - 2 * MARGIN
    for pi, g in enumerate(rep["groups"], start=1):
        t = g["totals"]
        head = ["Territory", "Type", "Bills", "Bill Value (LKR)", f"Bills &lt; {rep['small_n']} Items",
                f"{_esc(flabel)} Bills"]
        data = [[Paragraph(h, _PR_HDR) for h in head]]
        style = _pro_table_style()
        for i, r in enumerate(g["rows"], start=1):
            data.append([r["territory"], r["model"] if r["model"] in ("Van", "Pre") else "–",
                         r["bills"], _money(r["value"]), r["small_bills"], r["focus_bills"]])
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
            if not r["has_data"]:
                style.append(("TEXTCOLOR", (1, i), (-1, i), MUTED))
            if r["value"] < 0:
                style += [("BACKGROUND", (3, i), (3, i), NEG_BG), ("TEXTCOLOR", (3, i), (3, i), CHERRY)]
        data.append(["Total", "", t["bills"], _money(t["value"]), t["small_bills"], t["focus_bills"]])
        last = len(data) - 1
        style += [("ALIGN", (0, 1), (0, -1), "LEFT"), ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                  ("ALIGN", (3, 1), (3, -1), "RIGHT"),
                  ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
                  ("LINEABOVE", (0, last), (-1, last), 1.2, INK), ("LINEBELOW", (0, last), (-1, last), 1.2, INK)]
        cw = [52 * mm, 14 * mm, 18 * mm, 34 * mm, 28 * mm, 30 * mm]
        table = Table(data, colWidths=cw, repeatRows=1)
        table.setStyle(TableStyle(style))
        table.hAlign = "LEFT"
        notes = [n for n in rep["notes"]
                 if not n.startswith("Returns") or any(r["negative_bills"] for r in g["rows"])]
        flow = _pro_header("SKU Report", g["group"],
                           f"{t['territories']} territories &nbsp;&middot;&nbsp; {t['with_data']} with sales "
                           f"&nbsp;&middot;&nbsp; {t['territories'] - t['with_data']} without",
                           rep["date"], "sku", avail)
        flow += [Spacer(1, 6), _pro_kpis([("Total bills", str(t["bills"])),
                                          ("Total value (LKR)", _money(t["value"])),
                                          (f"Bills < {rep['small_n']} items", str(t["small_bills"])),
                                          (f"{flabel} bills", str(t["focus_bills"]))], sum(cw)),
                 Spacer(1, 10), table]
        flow += _pro_notes(notes, sum(cw)) + _pro_footer(pi, n_pages)
        pages.append({"width": width, "flow": flow, "name": f"SKU Report - {g['group']}"})
    return pages


def _pro_pivot(rep):
    pages, n_pages, n = [], len(rep["groups"]), rep["n"]
    cw = [40 * mm, 12 * mm] + [16 * mm, 16 * mm, 19 * mm] * n + [16 * mm]
    width = max(sum(cw) + 2 * MARGIN + 2 * mm, 196 * mm)
    avail = width - 2 * MARGIN
    for pi, g in enumerate(rep["groups"], start=1):
        row1 = ["Territory", "Type"] + sum([[f"{_ordinal(i)} bill", "", ""] for i in range(n)], []) + ["Bills today"]
        row2 = ["", ""] + ["Time", "Gap", "Value"] * n + [""]
        data = [[Paragraph(h, _PR_HDR) if h else "" for h in row1], [Paragraph(h, _PR_HDR) if h else "" for h in row2]]
        style = _pro_table_style(2)
        style += [("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (-1, 0), (-1, 1)),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#4A4A4E"))]
        for i in range(n):
            c = 2 + i * 3
            style += [("SPAN", (c, 0), (c + 2, 0)), ("LINEBEFORE", (c, 0), (c, -1), 0.8, GRID)]
        for ri, r in enumerate(g["rows"], start=2):
            row = [r["territory"], r["model"] if r["model"] in ("Van", "Pre") else "–"]
            for i in range(n):
                if i < len(r["bills"]):
                    b = r["bills"][i]
                    gap = f"{b['gap']}m" if not b["early"] else f"{-b['gap']}m early"
                    row += [b["time"], gap, _money(b["value"], 0)]
                    col = 2 + i * 3
                    if b["late"]:
                        style += [("BACKGROUND", (col + 1, ri), (col + 1, ri), LATE_BG),
                                  ("TEXTCOLOR", (col + 1, ri), (col + 1, ri), CHERRY),
                                  ("FONTNAME", (col + 1, ri), (col + 1, ri), "Helvetica-Bold")]
                    if b["negative"]:
                        style.append(("BACKGROUND", (col + 2, ri), (col + 2, ri), NEG_BG))
                else:
                    row += ["–", "–", "–"]
            row.append(r["total_bills_today"] or "–")
            data.append(row)
            if ri % 2 == 1:
                style.append(("BACKGROUND", (0, ri), (1, ri), ZEBRA))
            if not r["bills"]:
                style.append(("TEXTCOLOR", (1, ri), (-1, ri), MUTED))
        style += [("ALIGN", (0, 2), (0, -1), "LEFT"), ("FONTNAME", (0, 2), (0, -1), "Helvetica-Bold"),
                  ("FONTSIZE", (0, 2), (-1, -1), 8.5)]
        table = Table(data, colWidths=cw, repeatRows=2)
        table.setStyle(TableStyle(style))
        table.hAlign = "LEFT"
        with_bills = sum(1 for r in g["rows"] if r["bills"])
        flow = _pro_header("Pivot Report", g["group"],
                           f"First {n} bills per territory &nbsp;&middot;&nbsp; gaps measured from "
                           f"{_ampm(rep['first_bill_time'])}, then from the previous bill &nbsp;&middot;&nbsp; "
                           f"highlighted = {rep['red_minutes']}+ minutes",
                           rep["date"], "pivot", avail)
        flow += [Spacer(1, 6), _pro_kpis([("Reference time", _ampm(rep["first_bill_time"])),
                                          ("Territories billing", f"{with_bills} / {len(g['rows'])}"),
                                          ("Late first bills", str(g["late_first"])),
                                          ("No bills yet", str(g["no_bills"]))], sum(cw)),
                 Spacer(1, 10), table]
        flow += _pro_notes(rep["notes"] + ["Bills include invoices and pending sales orders."], sum(cw))
        flow += _pro_footer(pi, n_pages)
        pages.append({"width": width, "flow": flow, "name": f"Pivot Report - {g['group']}"})
    return pages


def _pro_hourly(rep):
    buckets = rep["buckets"] + ["Total"]
    row1 = ["Group", "Territory", "Type"]
    row2 = ["", "", ""]
    for b in buckets:
        row1 += [b, ""]
        row2 += ["Bills | Unprod.", "Value"]
    data = [[Paragraph(h, _PR_HDR) if h else "" for h in row1], [Paragraph(h, _PR_HDR) if h else "" for h in row2]]
    style = _pro_table_style(2)
    style += [("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (2, 0), (2, 1)),
              ("FONTSIZE", (0, 2), (-1, -1), 7.5), ("LINEBELOW", (0, 0), (-1, 0), 0.4, colors.HexColor("#4A4A4E"))]
    for i in range(len(buckets)):
        c = 3 + i * 2
        style += [("SPAN", (c, 0), (c + 1, 0)), ("LINEBEFORE", (c, 0), (c, -1), 0.6, GRID)]
    total_col = 3 + (len(buckets) - 1) * 2
    prev_group = None
    for r in rep["rows"]:
        ri = len(data)
        row = [r["group"] if r["group"] != prev_group else "", r["territory"],
               r["model"] if r["model"] in ("Van", "Pre") else "–"]
        for c in r["cells"] + [r["total"]]:
            empty = c["bc"] == 0 and c["upc"] == 0 and c["sv"] == 0
            row += ["–" if empty else f"{c['bc']} | {c['upc']}", "–" if c["sv"] == 0 else _money(c["sv"], 0)]
        data.append(row)
        if r["group"] != prev_group and prev_group is not None:
            style.append(("LINEABOVE", (0, ri), (-1, ri), 1.1, INK))
        prev_group = r["group"]
        if r["achieved"]:
            style.append(("BACKGROUND", (0, ri), (-1, ri), ACH_BG))
        elif r["total"]["bc"] == 0 and r["total"]["upc"] == 0:
            style.append(("TEXTCOLOR", (1, ri), (-1, ri), MUTED))
    tot = ["All territories", "", ""]
    for c in rep["column_totals"] + [rep["grand"]]:
        tot += [f"{c['bc']} | {c['upc']}", _money(c["sv"], 0)]
    data.append(tot)
    last = len(data) - 1
    style += [("FONTNAME", (0, 2), (1, -1), "Helvetica-Bold"), ("ALIGN", (0, 2), (1, -1), "LEFT"),
              ("FONTNAME", (0, last), (-1, last), "Helvetica-Bold"),
              ("LINEABOVE", (0, last), (-1, last), 1.2, INK), ("LINEBELOW", (0, last), (-1, last), 1.2, INK),
              ("BACKGROUND", (total_col, 2), (total_col + 1, -1), PANEL),
              ("FONTNAME", (total_col, 2), (total_col + 1, -1), "Helvetica-Bold")]
    cw = [22 * mm, 32 * mm, 11 * mm] + [15 * mm, 15 * mm] * len(buckets)
    table = Table(data, colWidths=cw, repeatRows=2)
    table.setStyle(TableStyle(style))
    table.hAlign = "LEFT"
    width = sum(cw) + 2 * MARGIN + 2 * mm
    tg = rep["targets"]
    achieved = sum(1 for r in rep["rows"] if r["achieved"])
    flow = _pro_header("Hourly Report", "Bill Count & Sale Value by Hour",
                       f"Each bucket: bills (invoices + pending sales orders) | unproductive visits, and value "
                       f"&nbsp;&middot;&nbsp; green = daily target met (Van {tg['Van'][0]:.0f} bills &amp; "
                       f"{tg['Van'][1]:,.0f}; Pre {tg['Pre'][0]:.0f} bills &amp; {tg['Pre'][1]:,.0f})",
                       rep["date"], "hourly", width - 2 * MARGIN)
    flow += [Spacer(1, 6), _pro_kpis([("Total bills", str(rep["grand"]["bc"])),
                                      ("Total sale value (LKR)", _money(rep["grand"]["sv"])),
                                      ("Unproductive visits", str(rep["grand"]["upc"])),
                                      ("Territories on target", str(achieved))], 200 * mm),
             Spacer(1, 10), table]
    flow += _pro_notes(rep["notes"], sum(cw)) + _pro_footer(1, 1)
    return [{"width": width, "flow": flow, "name": "Hourly BC-SV Report"}]


# ============================================================================
# Public entry points
# ============================================================================
BUILDERS = {
    ("sku", "classic"): ("SKU_Report", _classic_sku), ("sku", "professional"): ("SKU_Report", _pro_sku),
    ("pivot", "classic"): ("Pivot_Report", _classic_pivot), ("pivot", "professional"): ("Pivot_Report", _pro_pivot),
    ("hourly", "classic"): ("Hourly_BC_SV_Report", _classic_hourly),
    ("hourly", "professional"): ("Hourly_BC_SV_Report", _pro_hourly),
}


def write_pdf(kind, report, folder, style="classic"):
    if style not in STYLES:
        raise ValueError(f"Unknown output style '{style}' (expected one of {', '.join(STYLES)}).")
    prefix, fn = BUILDERS[(kind, style)]
    os.makedirs(folder, exist_ok=True)
    pages = fn(report)
    path = os.path.join(folder, f"{prefix}_{report['date']}.pdf")
    _render_pages(path, pages)
    return path, [p["name"] for p in pages]


def pdf_to_images(pdf_path, page_names, folder, rotate_wide=True, scale=2.2):
    """One PNG per page. Wide pages are rotated 90° (to portrait) for phone viewing."""
    import pypdfium2 as pdfium
    os.makedirs(folder, exist_ok=True)
    out = []
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for i, name in enumerate(page_names):
            img = pdf[i].render(scale=scale).to_pil()
            if rotate_wide and img.width > img.height:
                img = img.rotate(90, expand=True)
            path = os.path.join(folder, f"{_safe(name)}.png")
            img.save(path, optimize=True)
            out.append(path)
    finally:
        pdf.close()
    return out
