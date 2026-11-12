"""
sequence_reader.py
Reads the route sheets reps fill in, and SFA's View Route export.

Route sheet — the format in use today (one route per file):
    A1  Active Outlet List - <route>
    A2  Territory: …   Route: …   Route Code: ROUI1998   Distributor: …
    A3  Sales Rep: …   Status: Active   Outlets: 38   Printed: …
    row 6: No | Outlet Code | Outlet Name | New Sequence | Top 15 | Remark | Demand Product
    then one row per outlet, and a "Prepared by / Checked by / Date" footer.

Chimera's own format has the same visible layout (so reps fill it the same
way) plus a hidden "_chimera" sheet holding the route code etc. A workbook
may hold several routes, one per tab (e.g. a Google Sheet per territory):
every tab with an "Outlet Code" header is read as its own sheet.

Columns are found by their header text, not position, so a moved or extra
column doesn't matter. The route is identified by its Route Code (header,
hidden sheet, or the file name, e.g. "…_ROUI1998.xlsx"), else by name.
"""

import os
import re

import sequence_store as SS

HEAD = {
    "code": re.compile(r"^(outlet|customer)\s*code$|^code$"),
    "name": re.compile(r"^(outlet|customer)\s*name$|^outlet$|^name$"),
    "seq": re.compile(r"^(new\s*)?(visit\s*)?seq(uence)?(\s*(no|number))?$"),
    "top": re.compile(r"^top(\s*\d+)?(\s*outlets?)?$"),
    "remark": re.compile(r"^remarks?$"),
    "demand": re.compile(r"^demand(\s*products?)?$"),
    "no": re.compile(r"^(no|#|row)\.?$"),
}
META_LABELS = ["Route Code", "Sales Rep", "Territory", "Route", "Distributor", "Status", "Outlets", "Printed"]
_META_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(x) for x in META_LABELS) + r")\s*:", re.I)
NO_MARK = {"", "0", "no", "n", "-", "false", "none", "nil", "na", "n/a"}
ROUTE_CODE_IN_NAME = re.compile(r"(?<![A-Z0-9])((?:ROUI|ROU|TR)\d{2,})(?![0-9])", re.I)


class ReadError(Exception):
    pass


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def _meta_from_text(text):
    """'Territory: Gampaha     Route: X     Route Code: ROUI1998 …' -> {territory, route, route code …}"""
    out = {}
    hits = list(_META_RE.finditer(text))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        out[m.group(1).lower()] = text[m.end():end].strip(" |;,-\t")
    return out


def top_mark(v):
    """Any mark counts: a rank number, ✓, Y, Yes, x … -> (is_top, rank or None)."""
    if v is True:
        return True, None
    t = _s(v)
    if t.lower() in NO_MARK or v is False:
        return False, None
    try:
        f = float(t)
        if f == int(f) and int(f) >= 1:
            return True, int(f)
        return (f > 0), None
    except ValueError:
        return True, None


def _rows(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _find_header(rows):
    for i, r in enumerate(rows[:30]):
        names = [_s(c).lower() for c in r]
        cols = {}
        for k, n in enumerate(names):
            for key, rx in HEAD.items():
                if key not in cols and rx.match(n):
                    cols[key] = k
                    break
        if "code" in cols and ("seq" in cols or "top" in cols):
            return i, cols
    return None, None


def parse_rows(rows, hidden=None, file_name="", tab=""):
    """One route's rows -> {format, territory, route_code, route_name, rep, distributor, lines, problems}."""
    problems = []
    hi, cols = _find_header(rows)
    if hi is None:
        raise ReadError("No 'Outlet Code' + 'New Sequence' / 'Top 15' header row — this isn't a route sheet.")
    meta = _meta_from_text("     ".join(_s(c) for r in rows[:hi] for c in r if _s(c)))
    hidden = hidden or {}
    fmt = "chimera" if hidden.get("format") == "chimera" else "rep"
    code = hidden.get("route_code") or meta.get("route code") or ""
    if not code:
        m = ROUTE_CODE_IN_NAME.search(f"{tab} {os.path.basename(file_name)}")
        code = m.group(1).upper() if m else ""
    name = hidden.get("route_name") or meta.get("route") or ""
    if not name:
        title = _s(rows[0][0]) if rows and rows[0] else ""
        name = re.sub(r"^.*?outlet list\s*-\s*", "", title, flags=re.I) if "-" in title else ""
    territory = hidden.get("territory") or meta.get("territory") or ""
    out = {"format": fmt, "territory": territory, "route_code": code, "route_name": name,
           "rep": hidden.get("rep") or meta.get("sales rep") or "", "distributor": hidden.get("distributor") or meta.get("distributor") or "",
           "lines": [], "problems": problems, "tab": tab}

    def cell(r, key):
        k = cols.get(key)
        return r[k] if k is not None and k < len(r) else None

    seen, nums, bad_nums = {}, {}, []
    for i, r in enumerate(rows[hi + 1:], start=hi + 2):
        oc = _s(cell(r, "code"))
        if not oc:
            continue
        if re.match(r"^(prepared|checked|date|total)\b", oc, re.I):
            continue
        if oc.upper() in seen:
            problems.append({"level": "warn", "text": f"{oc} is listed twice (rows {seen[oc.upper()]} and {i}) — "
                                                      "the second row is ignored."})
            continue
        seen[oc.upper()] = i
        raw_seq = _s(cell(r, "seq"))
        seq = ""
        if raw_seq:
            try:
                f = float(raw_seq)
                if f == int(f) and f >= 1:
                    seq = str(int(f))
            except ValueError:
                pass
            if not seq:
                bad_nums.append(f"{oc} ('{raw_seq}')")
            else:
                nums.setdefault(seq, []).append(oc)
        is_top, rank = top_mark(cell(r, "top"))
        out["lines"].append({"line": i, "code": oc, "name": _s(cell(r, "name")), "rep_seq": seq,
                             "top_mark": _s(cell(r, "top")), "top": is_top, "top_rank": rank,
                             "remark": _s(cell(r, "remark")), "demand": _s(cell(r, "demand"))})
    if not out["lines"]:
        problems.append({"level": "error", "text": "The sheet has no outlets."})
    if bad_nums:
        problems.append({"level": "warn", "text": "New Sequence isn't a whole number for " + ", ".join(bad_nums[:6])
                         + ("…" if len(bad_nums) > 6 else "") + " — treated as blank (placed after the numbered outlets)."})
    dups = {n: c for n, c in nums.items() if len(c) > 1}
    if dups:
        problems.append({"level": "warn", "text": "Same New Sequence for more than one outlet: " +
                         "; ".join(f"{n} → {', '.join(c)}" for n, c in list(dups.items())[:5]) +
                         " — kept in the sheet's row order."})
    filled = sum(1 for l in out["lines"] if l["rep_seq"])
    tops = sum(1 for l in out["lines"] if l["top"])
    if out["lines"] and not filled and not tops:
        problems.append({"level": "error", "text": "Nothing is filled in — no New Sequence and no Top 15 marks."})
    elif out["lines"] and not filled:
        problems.append({"level": "warn", "text": "No New Sequence filled in — only Visit States will change "
                                                  "(the order stays as it is in SFA)."})
    if tops > SS.TOP_LIMIT:
        problems.append({"level": "warn", "text": f"{tops} outlets are marked Top 15 — all of them will be Mandatory "
                                                  "unless you change some in the preview."})
    _check_route(out)
    return out


def _check_route(out):
    p = out["problems"]
    info = SS.routes_info()
    r = SS.find_route(out["route_code"], out["route_name"], out["territory"])
    if r is None:
        if not info["routes"]:
            p.append({"level": "error", "text": "Chimera doesn't have SFA's route list yet — update it (Outlet Updates › "
                                                "Rep sheets › Routes), then read this sheet again."})
        else:
            what = out["route_code"] or out["route_name"] or "(no route code or name in the sheet)"
            p.append({"level": "error", "text": f"Route {what} isn't in SFA's route list."})
        return
    if not r["active"]:
        p.append({"level": "error", "text": f"Route {r['name']} ({r['code']}) is no longer active in SFA."})
    if out["route_code"] and out["route_code"].upper() == r["code"].upper() and out["route_name"] \
            and SS.squash(out["route_name"]) != SS.squash(r["name"]):
        p.append({"level": "warn", "text": f"The sheet calls the route '{out['route_name']}'; SFA calls {r['code']} "
                                           f"'{r['name']}' (renamed?). SFA's name is used."})
    if out["territory"] and SS.squash(out["territory"]) != SS.squash(r["territory"]):
        p.append({"level": "error", "text": f"The sheet says territory '{out['territory']}', but {r['code']} belongs to "
                                            f"'{r['territory']}' in SFA."})
    out.update(route_code=r["code"], route_name=r["name"], territory=r["territory"])


def read_workbook(path):
    """Every route in a workbook -> [parsed]. Raises ReadError if nothing in it is a route sheet."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        raise ReadError(f"That file isn't an Excel workbook Chimera can open ({type(e).__name__}).")
    hidden = {}
    if "_chimera" in wb.sheetnames:
        for k, v in wb["_chimera"].iter_rows(min_col=1, max_col=2, values_only=True):
            if k:
                hidden[str(k).strip()] = _s(v)
    out, errs = [], []
    for ws in wb.worksheets:
        if ws.title == "_chimera" or ws.sheet_state != "visible":
            continue
        try:
            h = dict(hidden)
            if len(wb.worksheets) > 2 and hidden.get(f"route_code:{ws.title}"):     # Chimera workbook with many routes
                h["route_code"] = hidden[f"route_code:{ws.title}"]
                h.pop("route_name", None)
            out.append(parse_rows(_rows(ws), h, os.path.basename(path), ws.title))
        except ReadError as e:
            errs.append(f"{ws.title}: {e}")
    if not out:
        raise ReadError(errs[0].split(": ", 1)[1] if len(errs) == 1 else
                        ("No route sheet in this file. " + " ".join(errs[:3])))
    return out


# ---------------------------------------------------------------------------
# SFA's View Route export
# ---------------------------------------------------------------------------
ROUTE_COLS = {"region": "region", "region name": "region", "area": "area", "area name": "area",
              "supervisor area": "supervisor_area", "sa name": "supervisor_area", "territory": "territory",
              "territory name": "territory", "route code": "code", "route": "name", "route name": "name",
              "visit frequency": "visit_frequency", "status": "status"}


def read_routes_excel(path):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise ReadError(f"That file isn't an Excel workbook Chimera can open ({type(e).__name__}).")
    try:
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
    finally:
        wb.close()
    hi, cols = None, {}
    for i, r in enumerate(rows[:40]):
        names = [_s(c).lower() for c in r]
        if "route code" in names and ("route" in names or "route name" in names):
            hi, cols = i, {ROUTE_COLS[n]: k for k, n in enumerate(names) if n in ROUTE_COLS}
            break
    if hi is None:
        raise ReadError("This doesn't look like SFA's View Route export — no 'ROUTE CODE' / 'ROUTE' header row.")
    out = []
    for r in rows[hi + 1:]:
        g = {f: _s(r[k]) if k < len(r) else "" for f, k in cols.items()}
        if not g.get("code") or not g.get("name"):
            continue
        if g.get("status") and g["status"].upper() != "ACTIVE":
            continue
        out.append(g)
    return out
