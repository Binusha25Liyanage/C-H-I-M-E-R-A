"""
last_visit_summary.py
"Last Visit Sales Summary Report": given a reference date (e.g.
tomorrow), for every Van Selling territory, find the scheduled route,
resolve the previous visit date, and VERIFY the sales rep actually
worked that day by checking real invoice data — never assumed. If
confirmed worked, build the same combined invoice+SKU report already
used elsewhere. Territories that aren't Van Selling, or where the rep
didn't work, are reported with a clear reason rather than silently
skipped or guessed at.

Design rule: single OR bulk through one function, same as every other
process in this project.
"""

import db
import combined_report


def _all_van_selling_territories():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT territory FROM territories WHERE sale_model = 'Van Selling' ORDER BY territory")
    names = [r[0] for r in cur.fetchall()]
    conn.close()
    return names


def _rep_worked_on_date(territory, route, visit_date_iso):
    """
    True only if at least one Van Selling invoice exists for this exact
    territory/route/date — the real, checked signal for whether the
    sales rep actually worked that day. No entry means no work, per the
    requirement: this is never assumed from the itinerary alone.
    """
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM van_sales WHERE territory = ? AND route = ? AND inv_date = ?",
        (territory, route, visit_date_iso),
    )
    n = cur.fetchone()[0]
    conn.close()
    return n > 0


def get_last_visit_summary(territory, reference_date_iso):
    """
    Builds the report for ONE territory. Returns the same shape as
    combined_report.get_route_combined_report (ok, reason, route,
    previous_visit_date, rows, bills, total, ...) plus a "worked" flag,
    so it feeds directly into the existing PDF builder with no
    special-casing needed.
    """
    info = db.get_territory_info(territory)
    if not info:
        return {"ok": False, "territory": territory, "reason": "Territory not found in Territories master."}
    _, sale_model = info
    if sale_model != "Van Selling":
        return {"ok": False, "territory": territory,
                "reason": f"Sale Model is '{sale_model}' — this report covers Van Selling territories only."}

    visit = db.get_visit_on_date(territory, reference_date_iso)
    if not visit:
        return {"ok": False, "territory": territory, "reason": f"No itinerary entry for {reference_date_iso}."}
    route, _, _ = visit

    previous_visit_date = db.get_previous_visit(territory, route, reference_date_iso)
    if not previous_visit_date:
        return {"ok": False, "territory": territory, "route": route,
                "reason": f"No earlier visit found for route '{route}' before {reference_date_iso}."}

    if not _rep_worked_on_date(territory, route, previous_visit_date):
        return {
            "ok": False, "territory": territory, "route": route,
            "previous_visit_date": previous_visit_date, "worked": False,
            "reason": f"Sales rep did not work on {previous_visit_date} — no invoices recorded for this route.",
        }

    report = combined_report.get_route_combined_report(territory, reference_date_iso)
    report["worked"] = True
    return report


def get_last_visit_summaries(territories, reference_date_iso):
    """
    Single OR bulk. Pass one territory name in a list for single mode.
    Pass an empty list (or None) for bulk mode across every Van Selling
    territory — matching the requirement that this report always covers
    "every territory with Van sales" when not scoped to one.
    """
    if not territories:
        territories = _all_van_selling_territories()
    return [get_last_visit_summary(t, reference_date_iso) for t in territories]
