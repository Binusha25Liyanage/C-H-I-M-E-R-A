"""
rep_attendance_images.py
The rep attendance pictures (PNG, for WhatsApp) and one PDF, in two looks:

  classic       the structure from Lakmee Muster, as the user sends it today:
                maroon title "LOGGED IN DATE MM/DD/YYYY", dark header row
                TERRITORY | LOGGED IN | LOGGED OUT, white / cream rows, black grid.
                Image 1 = reps not logged out yet ("-" in LOGGED OUT), image 2 = logged out.
  professional  Chimera's layout (like the SKU / Pivot / Hourly reports): logo header, KPI strip,
                # · Territory · Sales rep · Type · Logged in (· Logged out); image 1 also lists the
                working territories (Outlet Updates) with no login that day.

Both are drawn as PDF pages with ReportLab and turned into PNGs (pypdfium2), the same way the
daily reports are, so they look identical on every PC.
"""

import os
import re
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

import daily_reports_pdf as D

MAROON = colors.HexColor("#800020")
DARK = colors.HexColor("#2E2E30")
CREAM = colors.HexColor("#F5EFE0")
BLACK = colors.HexColor("#000000")

# Classic used Calibri (Lakmee Muster); fall back to Helvetica where it isn't installed.
_FONT_DIRS = [r"C:\Windows\Fonts", os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts"),
              "/usr/share/fonts/truetype/crosextra", "/usr/share/fonts/truetype/liberation"]


def _find_font(names):
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
    return None


_REG = _find_font(["calibri.ttf", "Carlito-Regular.ttf"])
_BLD = _find_font(["calibrib.ttf", "Carlito-Bold.ttf"])
if _REG and _BLD:
    try:
        pdfmetrics.registerFont(TTFont("CHM-Cal", _REG))
        pdfmetrics.registerFont(TTFont("CHM-Cal-B", _BLD))
        CL_FONT, CL_BOLD = "CHM-Cal", "CHM-Cal-B"
    except Exception:
        CL_FONT, CL_BOLD = "Helvetica", "Helvetica-Bold"
else:
    CL_FONT, CL_BOLD = "Helvetica", "Helvetica-Bold"


def _us_date(day):
    d = date.fromisoformat(day)
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def short_rep(rep, territory):
    """'CSR Galgamuwa - Avishka' -> 'Avishka' (the territory is in its own column)."""
    rep = (rep or "").strip()
    if " - " in rep:
        head, tail = rep.rsplit(" - ", 1)
        if tail.strip() and (re.match(r"^(CSR|DSR|SR|PSR|REP)\b", head.strip(), re.I) or
                             (territory and territory.lower().replace(" ", "") in head.lower().replace(" ", ""))):
            return tail.strip()
    return rep


def _sorted(rows):
    return sorted(rows, key=lambda r: (r["in_time"] or "99", r["territory"].lower()))


# ---------------------------------------------------------------------------
# Classic — the uploaded structure
# ---------------------------------------------------------------------------
def _classic_page(day, rows):
    title = D._ps("cl_att_t", fontName=CL_BOLD, fontSize=17, leading=20, textColor=colors.white, alignment=1)
    widths = [172, 128, 132]
    data = [[Paragraph(f"LOGGED IN DATE {_us_date(day)}", title), "", ""], ["TERRITORY", "LOGGED IN", "LOGGED OUT"]]
    for r in rows:
        data.append([r["territory"], r["in_time"] or "-", r["out_time"] or "-"])
    st = [("SPAN", (0, 0), (-1, 0)), ("BACKGROUND", (0, 0), (-1, 0), MAROON), ("BACKGROUND", (0, 1), (-1, 1), DARK),
          ("FONTNAME", (0, 1), (-1, 1), CL_BOLD), ("FONTSIZE", (0, 1), (-1, 1), 16), ("TEXTCOLOR", (0, 1), (-1, 1), CREAM),
          ("FONTNAME", (0, 2), (-1, -1), CL_FONT), ("FONTSIZE", (0, 2), (-1, -1), 15), ("TEXTCOLOR", (0, 2), (-1, -1), BLACK),
          ("GRID", (0, 0), (-1, -1), 0.9, BLACK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("ALIGN", (0, 1), (0, -1), "LEFT"), ("ALIGN", (1, 1), (-1, -1), "CENTER"),
          ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("LEFTPADDING", (0, 0), (-1, -1), 9)]
    for i in range(len(rows)):
        if i % 2 == 1:
            st.append(("BACKGROUND", (0, i + 2), (-1, i + 2), CREAM))
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle(st))
    return t, sum(widths)


# ---------------------------------------------------------------------------
# Professional — Chimera layout
# ---------------------------------------------------------------------------
def _pro_pages(day, info, groups):
    width = 180 * mm
    avail = width - 2 * D.MARGIN
    c = info["counts"]
    d = date.fromisoformat(day)
    as_of = info.get("as_of") or ""
    D_as_of = D._as_of_line
    cellb = D._ps("pa_cb", fontName="Helvetica-Bold", fontSize=9.5, leading=11.5, textColor=D.INK)
    cell = D._ps("pa_c", fontName="Helvetica", fontSize=9, leading=11, textColor=D.INK)
    small = D._ps("pa_s", fontName="Helvetica", fontSize=7.5, leading=9, textColor=D.ASH)
    sec = D._ps("pa_sec", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=D.INK)
    plain = D._ps("pa_nl", fontSize=8.5, leading=12, textColor=D.INK)
    working = c.get("working") or 0
    kpis = [("Logged in", str(c["logged_in"])),
            ("Not logged out", str(c["not_logged_out"])), ("Logged out", str(c["logged_out"]))]
    if working:
        kpis.append(("Not logged in", (f"<font color='#B5121B'>{c['not_logged_in']}</font>" if c["not_logged_in"] else "0")
                     + f" <font size=8 color='#6E6F71'>of {working} working</font>"))
    pages = []
    try:
        D._as_of_line = lambda _date, _kind: (f"As of {as_of}" if as_of else "From SFA") + " | Rep attendance from SFA"
        for g, rows in groups:
            out = g == "logged_out"
            title = "Reps Logged Out" if out else "Reps Not Logged Out Yet"
            sub = "Logged in and logged out today" if out else "Logged in to SFA and still in the field"
            flow = D._pro_header("Rep attendance", title, sub, f"{d:%a} {d.day} {d:%b %Y}", "att", avail)
            flow += [Spacer(1, 6), D._pro_kpis(kpis, avail), Spacer(1, 10),
                     Paragraph(f"{'LOGGED OUT' if out else 'NOT LOGGED OUT YET'} &nbsp;<font color='#6E6F71'>{len(rows)} "
                               f"rep{'s' if len(rows) != 1 else ''}</font>", sec), Spacer(1, 4)]
            head = ["#", "Territory", "Sales rep", "Type", "Logged in"] + (["Logged out"] if out else [])
            data = [[Paragraph(h, D._PR_HDR) for h in head]]
            st = D._pro_table_style()
            for i, r in enumerate(rows, 1):
                line = [str(i), Paragraph(D._esc(r["territory"]), cellb), Paragraph(D._esc(short_rep(r["rep"], r["territory"])), cell),
                        Paragraph(D._esc(r["rep_type"] or ""), small), r["in_time"] or "–"]
                if out:
                    line.append(r["out_time"] or "–")
                data.append(line)
                if i % 2 == 0:
                    st.append(("BACKGROUND", (0, i), (-1, i), D.ZEBRA))
            if not rows:
                data.append(["", Paragraph("Nobody yet.", small), "", "", ""] + ([""] if out else []))
            st += [("ALIGN", (1, 1), (3, -1), "LEFT"), ("TEXTCOLOR", (0, 1), (0, -1), D.ASH), ("FONTSIZE", (0, 1), (0, -1), 7.5)]
            fixed = [9 * mm, 42 * mm, 0, 22 * mm, 22 * mm] + ([22 * mm] if out else [])
            fixed[2] = avail - sum(fixed)
            t = Table(data, colWidths=fixed, repeatRows=1)
            t.setStyle(TableStyle(st))
            t.hAlign = "LEFT"
            flow.append(t)
            if not out:
                if working:
                    nl = info.get("not_logged_in") or []
                    flow += [Spacer(1, 10), Paragraph(f"NOT LOGGED IN &nbsp;<font color='#6E6F71'>{len(nl)} of {working} working "
                                                      "territories</font>", sec), Spacer(1, 3),
                             Paragraph(" &nbsp;&middot;&nbsp; ".join(D._esc(x) for x in nl) if nl
                                       else "<font color='#1E6B3A'>Every working territory has logged in.</font>", plain)]
                else:
                    flow += [Spacer(1, 8), Paragraph("Switch on your working territories in Outlet Updates to see who hasn't "
                                                     "logged in.", small)]
            flow += [Spacer(1, 8), D.HRule(0.5, D.GRID, 2),
                     Paragraph(f"CHIMERA &nbsp;&middot;&nbsp; Rep Attendance &nbsp;&middot;&nbsp; Generated "
                               f"{datetime.now():%Y-%m-%d %H:%M} &nbsp;&middot;&nbsp; Internal use only", D._PR_FOOT)]
            pages.append({"width": width, "flow": flow, "name": f"Rep_Attendance_{day}_{GROUP_FILE[g]}"})
    finally:
        D._as_of_line = D_as_of
    return pages


GROUP_FILE = {"not_logged_out": "Not_Logged_Out", "logged_out": "Logged_Out"}


def _render_classic(path, day, groups):
    """Classic pages: just the table, a thin white edge (like Lakmee Muster's image)."""
    c = rl_canvas.Canvas(path)
    names = []
    edge = 6
    for g, rows in groups:
        t, w = _classic_page(day, rows)
        tw, th = t.wrap(w, 100000)
        c.setPageSize((tw + 2 * edge, th + 2 * edge))
        t.drawOn(c, edge, edge)
        c.showPage()
        names.append(f"Rep_Attendance_{day}_{GROUP_FILE[g]}")
    c.save()
    return names


def build(day, info, style, folder, include_empty=False):
    """info = rep_attendance.get_day(day) (+ 'as_of'). -> {"pdf": path, "images": [{group, path}]}.
    A group with nobody in it gets no image unless include_empty."""
    os.makedirs(folder, exist_ok=True)
    rows = _sorted(info["rows"])
    groups = [(g, [r for r in rows if r["group"] == g]) for g in ("not_logged_out", "logged_out")]
    groups = [(g, rs) for g, rs in groups if rs or include_empty or (style == "professional" and g == "not_logged_out")]
    if not groups:
        return {"pdf": "", "images": []}
    pdf = os.path.join(folder, f"Rep_Attendance_{day}_{style}.pdf")
    if style == "classic":
        names = _render_classic(pdf, day, groups)
    else:
        pages = _pro_pages(day, info, groups)
        D._render_pages(pdf, pages)
        names = [p["name"] for p in pages]
    sub = os.path.join(folder, style)
    paths = D.pdf_to_images(pdf, names, sub, rotate_wide=False, scale=2.6 if style == "professional" else 2.4)
    return {"pdf": pdf, "images": [{"group": g, "style": style, "path": p} for (g, _), p in zip(groups, paths)]}


# ---------------------------------------------------------------------------
# Field Force Attendance — every working territory, grouped by its ASM (optional extra image)
# ---------------------------------------------------------------------------
NO_ASM = "No ASM"
STATUS = {"field": ("IN THE FIELD", "#8A5A00", "#FFF4DC"), "out": ("LOGGED OUT", "#1E6B3A", "#E3F3E8"),
          "none": ("NOT LOGGED IN", "#B5121B", "#FBE3E4")}


def manager_groups(info):
    """[(asm, [row])] — reps who logged in (ASM from SFA's TEAM column) plus the working territories that
    didn't (ASM / rep / type from the last day Chimera saw them: info['last_seen']). 'No ASM' last."""
    seen = info.get("last_seen") or {}
    rows = [dict(r, status="out" if r["out_time"] else "field") for r in info["rows"]]
    for t in info.get("not_logged_in") or []:
        ls = seen.get(t) or {}
        rows.append({"territory": t, "rep": "", "rep_type": ls.get("rep_type", ""), "asm": ls.get("asm", ""),
                     "in_time": "", "out_time": "", "status": "none"})
    out = {}
    for r in rows:
        out.setdefault((r.get("asm") or "").strip() or NO_ASM, []).append(r)
    for rs in out.values():
        rs.sort(key=lambda r: (r["status"] == "none", r["in_time"] or "99", r["territory"].lower()))
    return sorted(out.items(), key=lambda kv: (kv[0] == NO_ASM, kv[0].lower()))


def _by_manager_page(day, info):
    width = 190 * mm
    avail = width - 2 * D.MARGIN
    d = date.fromisoformat(day)
    groups = manager_groups(info)
    allr = [r for _, rs in groups for r in rs]
    li = sum(1 for r in allr if r["status"] != "none")
    lo = sum(1 for r in allr if r["status"] == "out")
    nli = len(allr) - li
    cell = D._ps("fm_c", fontName="Helvetica", fontSize=9, leading=11, textColor=D.INK)
    cellc = D._ps("fm_cc", fontName="Helvetica", fontSize=9, leading=11, textColor=D.INK, alignment=1)
    cellb = D._ps("fm_cb", fontName="Helvetica-Bold", fontSize=9.5, leading=11.5, textColor=D.INK)
    small = D._ps("fm_s", fontName="Helvetica", fontSize=7.5, leading=9, textColor=D.ASH)
    sec = D._ps("fm_sec", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=D.INK)
    bar_l = D._ps("fm_bl", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=colors.white)
    bar_r = D._ps("fm_br", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#F3D9DA"), alignment=2)
    red = lambda n: f"<font color='#B5121B'>{n}</font>" if n else "0"
    flow = D._pro_header("Rep attendance", "Field Force Attendance",
                         "Every working territory, grouped by its ASM" if info.get("working_count") else
                         "Reps who logged in, grouped by their ASM", f"{d:%a} {d.day} {d:%b %Y}", "att", avail)
    kpis = [("Logged in", f"{li}" + (f" <font size=8 color='#6E6F71'>of {len(allr)}</font>" if nli else "")),
            ("In the field", str(li - lo)), ("Logged out", str(lo))]
    if info.get("working_count"):
        kpis.append(("Not logged in", red(nli)))
    kpis.append(("Managers", str(sum(1 for m, _ in groups if m != NO_ASM))))
    flow += [Spacer(1, 6), D._pro_kpis(kpis, avail), Spacer(1, 10), Paragraph("BY MANAGER", sec), Spacer(1, 4)]
    summ = [[Paragraph(h, D._PR_HDR) for h in ["Manager", "Territories", "Logged in", "In the field", "Logged out",
                                                "Not logged in", "First login"]]]
    for m, rs in groups:
        ins = sorted(r["in_time"] for r in rs if r["status"] != "none" and r["in_time"])
        n_none = sum(1 for r in rs if r["status"] == "none")
        summ.append([Paragraph(D._esc(m), cellb), str(len(rs)), str(len(ins)), str(sum(1 for r in rs if r["status"] == "field")),
                     str(sum(1 for r in rs if r["status"] == "out")), Paragraph(f"<b>{red(n_none)}</b>" if n_none else "0", cellc),
                     ins[0][:5] if ins else "–"])
    st = D._pro_table_style() + [("ALIGN", (0, 1), (0, -1), "LEFT")]
    for i in range(2, len(summ), 2):
        st.append(("BACKGROUND", (0, i), (-1, i), D.ZEBRA))
    t = Table(summ, colWidths=[avail - 6 * 22 * mm] + [22 * mm] * 6, repeatRows=1)
    t.setStyle(TableStyle(st))
    t.hAlign = "LEFT"
    flow += [t, Spacer(1, 12)]
    cols = [9 * mm, 40 * mm, 0, 20 * mm, 20 * mm, 20 * mm, 27 * mm]
    cols[2] = avail - sum(cols)
    for m, rs in groups:
        n_in = sum(1 for r in rs if r["status"] != "none")
        bar = Table([[Paragraph(D._esc(m), bar_l),
                      Paragraph(f"{n_in} of {len(rs)} logged in &middot; {sum(1 for r in rs if r['status'] == 'out')} logged out", bar_r)]],
                    colWidths=[avail * 0.55, avail * 0.45])
        bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), D.CHERRY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
        data = [[Paragraph(h, D._PR_HDR) for h in ["#", "Territory", "Sales rep", "Type", "Logged in", "Logged out", "Status"]]]
        st = D._pro_table_style()
        for i, r in enumerate(rs, 1):
            lbl, fg, bg = STATUS[r["status"]]
            data.append([str(i), Paragraph(D._esc(r["territory"]), cellb),
                         Paragraph(D._esc(short_rep(r["rep"], r["territory"]) or "–"), cell), Paragraph(D._esc(r["rep_type"] or ""), small),
                         r["in_time"] or "–", r["out_time"] or "–",
                         Paragraph(f"<b>{lbl}</b>", D._ps("fm_" + r["status"], fontName="Helvetica-Bold", fontSize=6.8, leading=8.5,
                                                           textColor=colors.HexColor(fg), alignment=1))])
            st.append(("BACKGROUND", (6, i), (6, i), colors.HexColor(bg)))
            if i % 2 == 0:
                st.append(("BACKGROUND", (0, i), (5, i), D.ZEBRA))
            if r["status"] == "none":
                st.append(("TEXTCOLOR", (1, i), (5, i), D.ASH))
        st += [("ALIGN", (1, 1), (3, -1), "LEFT"), ("TEXTCOLOR", (0, 1), (0, -1), D.ASH), ("FONTSIZE", (0, 1), (0, -1), 7.5)]
        tt = Table(data, colWidths=cols, repeatRows=1)
        tt.setStyle(TableStyle(st))
        tt.hAlign = bar.hAlign = "LEFT"
        flow += [bar, tt, Spacer(1, 10)]
    flow += [D.HRule(0.5, D.GRID, 2),
             Paragraph(f"CHIMERA &nbsp;&middot;&nbsp; Field Force Attendance &nbsp;&middot;&nbsp; Generated "
                       f"{datetime.now():%Y-%m-%d %H:%M} &nbsp;&middot;&nbsp; Internal use only", D._PR_FOOT)]
    return {"width": width, "flow": flow, "name": f"Field_Force_Attendance_{day}"}


def build_by_manager(day, info, folder):
    """The Field Force Attendance image (+ its own PDF). -> {"pdf", "images": [{group: 'by_manager', style: 'manager', path}]}"""
    if not info["rows"] and not info.get("not_logged_in"):
        return {"pdf": "", "images": []}
    os.makedirs(folder, exist_ok=True)
    as_of = info.get("as_of") or ""
    keep = D._as_of_line
    try:
        D._as_of_line = lambda _d, _k: (f"As of {as_of}" if as_of else "From SFA") + " | Rep attendance from SFA"
        page = _by_manager_page(day, info)
    finally:
        D._as_of_line = keep
    pdf = os.path.join(folder, f"Field_Force_Attendance_{day}.pdf")
    D._render_pages(pdf, [page])
    paths = D.pdf_to_images(pdf, [page["name"]], os.path.join(folder, "manager"), rotate_wide=False, scale=2.6)
    return {"pdf": pdf, "images": [{"group": "by_manager", "style": "manager", "path": p} for p in paths]}
