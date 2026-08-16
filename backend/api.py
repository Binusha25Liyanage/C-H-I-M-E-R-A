"""
api.py
The Python <-> JavaScript bridge for the pywebview app.

Every method on the Api class here becomes callable from the frontend as
`pywebview.api.<method_name>(...)` in JavaScript, and returns a JSON-
serializable result (dict / list / str / int / bool) back to JS as a
resolved Promise.

This file is intentionally the ONLY place that talks to db.py, excel
imports, PDF export, and SFA automation on behalf of the frontend — the
frontend never imports Python modules directly, it only calls methods on
this Api object. Keeping that boundary clean means the frontend doesn't
need to know anything about SQLite, file paths, or how automation works
under the hood.
"""

import os
import db
import combined_report
import combined_report_pdf


class Api:
    def __init__(self):
        db.init_db()

    # ------------------------------------------------------------
    # Step 1 scaffold: a single proven round trip through the real
    # backend, so we know the bridge works before building any real
    # screens on top of it. Every tab we build later follows this
    # same pattern: JS calls a method here, this method calls db.py
    # (or excel_import.py / sfa_automation.py), and returns plain
    # data back to JS.
    # ------------------------------------------------------------
    def get_dashboard_summary(self):
        total, territories, routes = db.get_outlet_count()
        return {
            "ok": True,
            "total_outlets": total,
            "territories_with_outlets": territories,
            "routes_with_outlets": routes,
        }

    # ------------------------------------------------------------
    # Combined SKU + Invoice report.
    # One method, works for single or bulk depending on how many
    # territory names are passed in `territories` — per the
    # project's bulk+single design rule.
    # ------------------------------------------------------------
    def preview_combined_report(self, territory, reference_date):
        """Single-territory preview — computes the report but writes no file."""
        return combined_report.get_route_combined_report(territory, reference_date)

    def generate_combined_reports(self, territories, reference_date, output_folder):
        """
        territories: list of territory names — one item for single mode,
        many for bulk mode, same code path either way.
        Returns {"written": [...], "skipped": [...]} plus the raw
        per-territory reports so the UI can show status for every one,
        not just successes.
        """
        reports = combined_report.get_combined_reports(territories, reference_date)
        result = combined_report_pdf.build_combined_reports(reports, output_folder)
        result["reports"] = reports
        return result

    def get_all_territories(self):
        """Territory names, for populating the UI's selection list."""
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT territory FROM territories ORDER BY territory")
        names = [r[0] for r in cur.fetchall()]
        conn.close()
        return names

    def get_default_export_folder(self):
        return os.path.join(os.path.expanduser("~"), "Chimera", "exports")

    def get_import_log(self):
        return db.get_import_log()
