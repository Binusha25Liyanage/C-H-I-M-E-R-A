"""
daily_reports.py
The three daily sales-monitoring reports carried over from SalesPulse, now
built straight from raw SFA data (see sfa_exports.py / sfa_store.py):

  SKU Report (per manager group)
      Territory | Total Bills | Total Bill Value | Bills < N Items | <Focus product> Bills
      Source: Outlet Wise SKU (Van + Pre Pending). A "bill" is a distinct
      INVOICE / SO NUMBER; "items" = product lines on that bill.

  Pivot Report (per manager group)
      For each territory, the first N bills of the day with time, value and
      gap. First gap = minutes after the reference first-bill time; later
      gaps = minutes after the previous bill. Gaps >= threshold are flagged.
      Source: Invoices + Sales Orders.

  Hourly Bill Count & Sale Value (all groups)
      Per territory and hourly bucket: bills | unproductive visits, and
      sale value. Territory is marked achieved when it meets BOTH its bill
      and value targets for its selling type.
      Source: Invoices + Sales Orders + Unproductive Visits.

Accuracy rules (the reasons SalesPulse's numbers drifted):
  * Nothing is dropped silently. Data from territories that aren't in any
    manager group is reported in an "Unassigned" section; bills with no
    time are counted in totals and listed; visits before the first hourly
    bucket get their own "Before" bucket.
  * A pending sales order that also appears as an invoice (matched on SO
    NUMBER) is counted once, as the invoice.
  * Negative bills (returns / credit notes) keep their true value and are
    flagged, never hidden.

All functions return plain dicts/lists so the UI preview, the PDFs, the
images and the tests use exactly the same numbers.
"""

from collections import defaultdict

import db
import sfa_store

UNASSIGNED = "Unassigned"


def _fmt_time(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _group_index():
    """territory -> (group, 'Van'|'Pre'), plus the ordered group map."""
    groups = sfa_store.get_groups()
    index = {}
    for g, terrs in groups.items():
        for t, model in terrs:
            index[t] = (g, model)
    return groups, index


def _with_unassigned(groups, territories_with_data, index):
    """Adds an 'Unassigned' group for territories that have data but no manager."""
    extra = sorted(t for t in territories_with_data if t and t not in index)
    groups = {g: list(v) for g, v in groups.items()}
    if extra:
        groups[UNASSIGNED] = [(t, "?") for t in extra]
    blank = "" in territories_with_data
    return groups, extra, blank


def _focus_matcher(focus):
    words = [w.lower() for w in focus.split() if w.strip()]
    return lambda product: bool(words) and all(w in (product or "").lower() for w in words)


# ---------------------------------------------------------------------------
# Bills (shared by Pivot and Hourly)
# ---------------------------------------------------------------------------
def get_bills(date):
    """
    Returns (bills, notes). Each bill: dict with territory, bill_no, kind,
    amount, minutes, created_time, customer. A pending SO that has a
    matching invoice (same SO NUMBER) is dropped so it's counted once.
    """
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT bill_no, kind, so_number, territory, customer, amount, created_time, minutes,
                  dataset, territory_source
           FROM sfa_bills WHERE bill_date=?""", (date,)).fetchall()
    conn.close()
    cols = ["bill_no", "kind", "so_number", "territory", "customer", "amount",
            "created_time", "minutes", "dataset", "territory_source"]
    bills = [dict(zip(cols, r)) for r in rows]
    invoiced_sos = {b["so_number"] for b in bills if b["kind"] == "INV" and b["so_number"]}
    deduped = [b for b in bills if not (b["kind"] == "SO" and b["bill_no"] in invoiced_sos)]
    notes = []
    dropped = len(bills) - len(deduped)
    if dropped:
        notes.append(f"{dropped} sales order(s) already invoiced were counted once (as the invoice).")
    no_time = [b for b in deduped if b["minutes"] is None]
    if no_time:
        notes.append(f"{len(no_time)} bill(s) have no created time — counted in totals but not in "
                     "time-based columns: " + ", ".join(b["bill_no"] for b in no_time[:5]))
    if any(b["dataset"] == "salespulse_history" for b in deduped):
        notes.append("Some bills for this date come from imported SalesPulse history "
                     "(not from raw SFA exports).")
    return deduped, notes


# ---------------------------------------------------------------------------
# SKU Report
# ---------------------------------------------------------------------------
def build_sku_report(date, settings=None):
    s = settings or sfa_store.get_settings()
    small_n = int(float(s["small_bill_lines"]))
    is_focus = _focus_matcher(s["focus_product"])

    conn = db.get_connection()
    lines = conn.execute(
        "SELECT territory, bill_no, product, value, units, dataset FROM sfa_sku_lines WHERE line_date=?",
        (date,)).fetchall()
    loaded = {r[0] for r in conn.execute(
        "SELECT dataset FROM sfa_datasets WHERE data_date=? AND dataset IN ('sku_van','sku_pre')", (date,))}
    conn.close()

    per_terr = defaultdict(lambda: defaultdict(lambda: {"lines": 0, "value": 0.0, "focus": False}))
    for terr, bill, product, value, units, _ds in lines:
        b = per_terr[terr or ""][bill]
        b["lines"] += 1
        b["value"] += value or 0.0
        b["focus"] = b["focus"] or is_focus(product)

    groups, index = _group_index()
    groups, unassigned, blank = _with_unassigned(groups, set(per_terr), index)

    out_groups = []
    for g, terrs in groups.items():
        rows = []
        for t, model in terrs:
            bills = per_terr.get(t, {})
            negative = [no for no, b in bills.items() if b["value"] < 0]
            rows.append({
                "territory": t, "model": model,
                "bills": len(bills),
                "value": round(sum(b["value"] for b in bills.values()), 2),
                "small_bills": sum(1 for b in bills.values() if b["lines"] < small_n),
                "focus_bills": sum(1 for b in bills.values() if b["focus"]),
                "negative_bills": negative,
                "has_data": bool(bills),
            })
        out_groups.append({
            "group": g, "rows": rows,
            "totals": {
                "territories": len(rows),
                "with_data": sum(1 for r in rows if r["has_data"]),
                "bills": sum(r["bills"] for r in rows),
                "value": round(sum(r["value"] for r in rows), 2),
                "small_bills": sum(r["small_bills"] for r in rows),
                "focus_bills": sum(r["focus_bills"] for r in rows),
            },
        })

    notes = []
    missing = {"sku_van", "sku_pre"} - loaded
    if missing:
        notes.append("Missing input: " + ", ".join(sfa_store.DATASET_LABELS[m] for m in sorted(missing))
                     + " — figures for that selling model are not included.")
    if unassigned:
        notes.append("Territories with sales but no manager group (shown under Unassigned): "
                     + ", ".join(unassigned))
    if blank:
        notes.append("Some SKU lines have no territory and could not be placed in any group.")
    neg = [(g["group"], r["territory"]) for g in out_groups for r in g["rows"] if r["negative_bills"]]
    if neg:
        notes.append("Returns / negative bills present in: " + ", ".join(t for _, t in neg)
                     + " — values shown are net of the return.")
    return {"ok": True, "date": date, "small_n": small_n, "focus_label": s["focus_product"],
            "groups": out_groups, "notes": notes, "inputs_loaded": sorted(loaded)}


# ---------------------------------------------------------------------------
# Pivot Report
# ---------------------------------------------------------------------------
def build_pivot_report(date, settings=None):
    s = settings or sfa_store.get_settings()
    n = int(float(s["pivot_bill_count"]))
    red = int(float(s["gap_red_minutes"]))
    h, m = s["first_bill_time"].split(":")
    ref = int(h) * 60 + int(m)

    bills, notes = get_bills(date)
    per_terr = defaultdict(list)
    for b in bills:
        if b["minutes"] is not None:
            per_terr[b["territory"] or ""].append(b)
    for lst in per_terr.values():
        lst.sort(key=lambda b: (b["minutes"], b["bill_no"]))

    groups, index = _group_index()
    groups, unassigned, blank = _with_unassigned(groups, {b["territory"] or "" for b in bills}, index)

    out_groups = []
    for g, terrs in groups.items():
        rows = []
        for t, model in terrs:
            shown = []
            prev = None
            for b in per_terr.get(t, [])[:n]:
                gap = b["minutes"] - (ref if prev is None else prev)
                shown.append({
                    "time": _fmt_time(b["minutes"]), "value": b["amount"], "gap": gap,
                    "late": gap >= red, "early": prev is None and gap < 0,
                    "kind": b["kind"], "bill_no": b["bill_no"], "negative": b["amount"] < 0,
                })
                prev = b["minutes"]
            rows.append({"territory": t, "model": model, "bills": shown,
                         "total_bills_today": len(per_terr.get(t, []))})
        out_groups.append({
            "group": g, "rows": rows,
            "late_first": sum(1 for r in rows if r["bills"] and r["bills"][0]["late"]),
            "no_bills": sum(1 for r in rows if not r["bills"]),
        })

    conn = db.get_connection()
    loaded = {r[0] for r in conn.execute(
        "SELECT dataset FROM sfa_datasets WHERE data_date=? AND dataset IN ('invoices','sales_orders')", (date,))}
    conn.close()
    missing = {"invoices", "sales_orders"} - loaded
    if missing and not any(b["dataset"] == "salespulse_history" for b in bills):
        notes.append("Missing input: " + ", ".join(sfa_store.DATASET_LABELS[x] for x in sorted(missing)))
    if unassigned:
        notes.append("Territories with bills but no manager group (shown under Unassigned): "
                     + ", ".join(unassigned))
    if blank:
        notes.append("Some bills have no territory and could not be placed in any group.")
    return {"ok": True, "date": date, "n": n, "red_minutes": red, "first_bill_time": s["first_bill_time"],
            "groups": out_groups, "notes": notes}


# ---------------------------------------------------------------------------
# Hourly Bill Count & Sale Value
# ---------------------------------------------------------------------------
def hourly_buckets(start_hour, end_hour, separate_early=True):
    """
    [(label, lo_minutes, hi_minutes)] covering the whole day: an optional
    'Before' bucket, one per hour, then 'After'. With separate_early=False
    early records fold into the first hourly bucket (how SalesPulse did it).
    """
    def lbl(hh):
        h12 = hh % 12 or 12
        return f"{h12}{'AM' if hh < 12 or hh == 24 else 'PM'}"
    buckets = [(f"Before {lbl(start_hour)}", 0, start_hour * 60)] if separate_early else []
    for hh in range(start_hour, end_hour):
        lo = 0 if (not separate_early and hh == start_hour) else hh * 60
        buckets.append((f"{lbl(hh)}-{lbl(hh + 1)}", lo, (hh + 1) * 60))
    buckets.append((f"After {lbl(end_hour)}", end_hour * 60, 24 * 60))
    return buckets


def build_hourly_report(date, settings=None):
    s = settings or sfa_store.get_settings()
    separate_early = s.get("separate_early_bucket", "1") not in ("0", "", "false")
    buckets = hourly_buckets(int(float(s["hourly_start_hour"])), int(float(s["hourly_end_hour"])), separate_early)
    targets = {"Van": (float(s["van_bc_target"]), float(s["van_sv_target"])),
               "Pre": (float(s["pre_bc_target"]), float(s["pre_sv_target"]))}

    def bucket_of(minutes):
        for i, (_, lo, hi) in enumerate(buckets):
            if lo <= minutes < hi:
                return i
        return len(buckets) - 1

    bills, notes = get_bills(date)
    conn = db.get_connection()
    unprod = conn.execute("SELECT territory, minutes FROM sfa_unproductive WHERE visit_date=?", (date,)).fetchall()
    loaded = {r[0] for r in conn.execute(
        "SELECT dataset FROM sfa_datasets WHERE data_date=? AND dataset IN ('invoices','sales_orders','unproductive')",
        (date,))}
    conn.close()

    nb = len(buckets)
    cell = defaultdict(lambda: [[0, 0, 0.0] for _ in range(nb)])   # territory -> [bc, upc, sv] per bucket
    extra_bc = defaultdict(lambda: [0, 0.0])                        # bills without a time
    for b in bills:
        t = b["territory"] or ""
        if b["minutes"] is None:
            extra_bc[t][0] += 1
            extra_bc[t][1] += b["amount"]
            continue
        c = cell[t][bucket_of(b["minutes"])]
        c[0] += 1
        c[2] += b["amount"]
    untimed_unprod = 0
    for t, mins in unprod:
        if mins is None:
            untimed_unprod += 1
            continue
        cell[t or ""][bucket_of(mins)][1] += 1

    groups, index = _group_index()
    terrs_with_data = set(cell) | set(extra_bc)
    groups, unassigned, blank = _with_unassigned(groups, terrs_with_data, index)

    rows, grand = [], {"bc": 0, "upc": 0, "sv": 0.0}
    for g, terrs in groups.items():
        for t, model in terrs:
            cells = [{"bc": c[0], "upc": c[1], "sv": round(c[2], 2)} for c in cell.get(t, [[0, 0, 0.0]] * nb)]
            bc = sum(c["bc"] for c in cells) + extra_bc[t][0] if t in extra_bc else sum(c["bc"] for c in cells)
            sv = sum(c["sv"] for c in cells) + (extra_bc[t][1] if t in extra_bc else 0.0)
            upc = sum(c["upc"] for c in cells)
            tgt = targets.get(model)
            achieved = bool(tgt) and bc >= tgt[0] and sv >= tgt[1]
            rows.append({"group": g, "territory": t, "model": model, "cells": cells,
                         "total": {"bc": bc, "upc": upc, "sv": round(sv, 2)}, "achieved": achieved})
            grand["bc"] += bc
            grand["upc"] += upc
            grand["sv"] += sv
    grand["sv"] = round(grand["sv"], 2)

    # Column totals per bucket.
    col_tot = [{"bc": sum(r["cells"][i]["bc"] for r in rows), "upc": sum(r["cells"][i]["upc"] for r in rows),
                "sv": round(sum(r["cells"][i]["sv"] for r in rows), 2)} for i in range(nb)]

    missing = {"invoices", "sales_orders", "unproductive"} - loaded
    if missing and not any(b["dataset"] == "salespulse_history" for b in bills):
        notes.append("Missing input: " + ", ".join(sfa_store.DATASET_LABELS[x] for x in sorted(missing)))
    if unassigned:
        notes.append("Territories with activity but no manager group (shown under Unassigned): "
                     + ", ".join(unassigned))
    if blank:
        notes.append("Some records have no territory and could not be placed in any group.")
    if untimed_unprod:
        notes.append(f"{untimed_unprod} unproductive visit(s) have no time and are not shown in buckets.")
    early = col_tot[0]["bc"] + col_tot[0]["upc"] if separate_early else 0
    if early:
        notes.append(f"{early} record(s) fall before {buckets[1][0].split('-')[0]} — shown in the "
                     f"'{buckets[0][0]}' column.")
    return {"ok": True, "date": date, "buckets": [b[0] for b in buckets], "rows": rows,
            "column_totals": col_tot, "grand": grand, "targets": targets, "notes": notes}
