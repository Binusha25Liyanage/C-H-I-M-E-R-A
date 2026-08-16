"""
combined_report.py
Builds the new combined SKU + Invoice report: for a territory's route, on
the most recent previous-visit date, one row per outlet showing how many
separate invoices/bills it had AND its product-level SKU breakdown
together — replacing the need to cross-reference two separate reports.

Design rule: every process here takes a LIST of territories. Calling it
with one territory is "single mode"; calling it with many is "bulk mode" —
same function, same code path, no special-casing either way.
"""

import db


# ---------------------------------------------------------------------
# Known accuracy limitation — read this before trusting Pre Selling
# invoice counts.
#
# Van Selling invoices are matched to outlets by outlet_code, which is a
# stable, reliable join (same approach db.get_route_sku_report already
# uses for product data). Pre Selling orders, however, are only stored
# with a customer NAME (see db.py's pre_sales table — it has no
# outlet_code column), because that's the only field present in the raw
# Sales Order export. So for Pre Selling territories, invoice counts here
# are matched by case-insensitive exact name against the outlets master —
# this is genuinely lower-confidence than the Van Selling path: a typo,
# abbreviation, or duplicate customer name across outlets will cause a
# miss or a wrong match.
#
# Every result below reports which outlets it could NOT confidently match
# (see "unmatched_pre_sales_customers") rather than silently guessing —
# treat any territory with unmatched names as needing a manual check, and
# consider whether the raw Sales Order export can be requested with an
# Outlet Code column in the future to close this gap properly.
# ---------------------------------------------------------------------


def _van_selling_invoice_counts(territory, route, visit_date_iso):
    """Returns {outlet_code: distinct_invoice_count} — reliable, outlet_code-based."""
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT outlet_code, COUNT(DISTINCT inv_number)
           FROM van_sales
           WHERE territory = ? AND route = ? AND inv_date = ? AND outlet_code IS NOT NULL
           GROUP BY outlet_code""",
        (territory, route, visit_date_iso),
    )
    counts = dict(cur.fetchall())
    conn.close()
    return counts


def _pre_selling_invoice_counts(territory, route, visit_date_iso, outlets):
    """
    Returns (counts, unmatched_customers).
    counts: {outlet_code: distinct_so_count} — matched by name, lower confidence.
    unmatched_customers: sorted list of customer names that had orders but
    could not be matched to any outlet in this route's master list.
    """
    from datetime import date, timedelta
    d = date.fromisoformat(visit_date_iso)
    date_to_iso = (d + timedelta(days=2)).isoformat()

    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT customer, COUNT(DISTINCT so_number)
           FROM pre_sales
           WHERE territory = ? AND route = ? AND order_date BETWEEN ? AND ?
           GROUP BY customer""",
        (territory, route, visit_date_iso, date_to_iso),
    )
    by_customer = dict(cur.fetchall())
    conn.close()

    # outlets: list of (territory, route, sequence, outlet_code, outlet, category)
    name_to_code = {o[4].strip().lower(): o[3] for o in outlets if o[4]}

    counts = {}
    unmatched = []
    for customer_name, n in by_customer.items():
        key = (customer_name or "").strip().lower()
        code = name_to_code.get(key)
        if code:
            counts[code] = counts.get(code, 0) + n
        else:
            unmatched.append(customer_name)

    return counts, sorted(unmatched)


def get_route_combined_report(territory, reference_date_iso):
    """
    Builds the combined report for ONE territory (its currently-scheduled
    route on reference_date_iso), resolving the previous-visit date first.

    Returns a dict:
        {
            "ok": bool,
            "territory": str,
            "reason": str or None,          # set when ok is False
            "route": str or None,
            "sale_model": str or None,
            "reference_date": str,
            "previous_visit_date": str or None,
            "rows": [
                {sequence, outlet_code, outlet, invoices, lines, subtotal}, ...
            ],
            "bills": int,                   # count of outlets with >=1 invoice
            "total": float,
            "unmatched_pre_sales_customers": [str, ...],  # only for Pre Selling
        }

    Territories that can't be built (no itinerary entry, no earlier visit,
    no Sale Model set, or no outlets found) come back with ok: False and a
    human-readable reason, rather than raising — so a bulk run can report
    per-territory status instead of failing the whole batch.
    """
    info = db.get_territory_info(territory)
    if not info:
        return {"ok": False, "territory": territory, "reason": "Territory not found in Territories master."}
    _, sale_model = info
    if not sale_model:
        return {"ok": False, "territory": territory, "reason": "No Sale Model set for this territory."}

    visit = db.get_visit_on_date(territory, reference_date_iso)
    if not visit:
        return {"ok": False, "territory": territory, "reason": f"No itinerary entry for {reference_date_iso}."}
    route, sales_rep, distributor = visit

    previous_visit_date = db.get_previous_visit(territory, route, reference_date_iso)
    if not previous_visit_date:
        return {"ok": False, "territory": territory, "route": route,
                "reason": f"No earlier visit found for route '{route}' before {reference_date_iso}."}

    outlets = db.get_outlets(territory, route)
    if not outlets:
        return {"ok": False, "territory": territory, "route": route,
                "previous_visit_date": previous_visit_date,
                "reason": "No outlets found for this route."}

    sku_report = db.get_route_sku_report(territory, route, previous_visit_date, sale_model)
    sku_by_code = {r["outlet_code"]: r for r in sku_report}

    unmatched_pre_sales_customers = []
    if sale_model == "Van Selling":
        invoice_counts = _van_selling_invoice_counts(territory, route, previous_visit_date)
    else:  # Pre Selling
        invoice_counts, unmatched_pre_sales_customers = _pre_selling_invoice_counts(
            territory, route, previous_visit_date, outlets
        )

    rows = []
    bills = 0
    total = 0.0
    for territory_, route_, sequence, outlet_code, outlet, category in outlets:
        sku_row = sku_by_code.get(outlet_code, {"lines": [], "subtotal": 0.0})
        n_invoices = invoice_counts.get(outlet_code, 0)
        if n_invoices > 0:
            bills += 1
        total += sku_row["subtotal"]
        rows.append({
            "sequence": sequence,
            "outlet_code": outlet_code,
            "outlet": outlet,
            "invoices": n_invoices,
            "lines": sku_row["lines"],
            "subtotal": sku_row["subtotal"],
        })

    return {
        "ok": True,
        "territory": territory,
        "reason": None,
        "route": route,
        "sale_model": sale_model,
        "reference_date": reference_date_iso,
        "previous_visit_date": previous_visit_date,
        "rows": rows,
        "bills": bills,
        "total": round(total, 2),
        "unmatched_pre_sales_customers": unmatched_pre_sales_customers,
    }


def get_combined_reports(territories, reference_date_iso):
    """
    Single OR bulk entry point — pass one territory name in a list for
    single mode, or every territory for bulk mode. Same function either
    way, per the project's bulk+single design rule.

    Returns a list of per-territory result dicts (see
    get_route_combined_report), in the same order as `territories`.
    """
    return [get_route_combined_report(t, reference_date_iso) for t in territories]
