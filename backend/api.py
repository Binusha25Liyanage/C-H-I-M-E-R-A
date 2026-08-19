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
import base64
import webview
import db
import excel_import
import combined_report
import combined_report_pdf
import last_visit_summary
import sfa_config
import sfa_connection


class Api:
    def __init__(self):
        db.init_db()
        self.window = None  # set by app.py after the pywebview window is created

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

    # ------------------------------------------------------------
    # Last Visit Sales Summary Report.
    # Bulk-by-default: pass [] to cover every Van Selling territory,
    # or one territory name for single mode — same function either way.
    # ------------------------------------------------------------
    def preview_last_visit_summary(self, territory, reference_date):
        return last_visit_summary.get_last_visit_summary(territory, reference_date)

    def generate_last_visit_summaries(self, territories, reference_date, output_folder):
        reports = last_visit_summary.get_last_visit_summaries(territories, reference_date)
        result = combined_report_pdf.build_combined_reports(reports, output_folder)
        result["reports"] = reports
        return result

    def get_default_export_folder(self):
        return os.path.join(os.path.expanduser("~"), "Chimera", "exports")

    def get_import_log(self):
        return db.get_import_log()

    # ------------------------------------------------------------
    # Real data import — opens a native file picker, parses the file
    # with excel_import.py, writes it to the database, and records the
    # import so the Dashboard/Import Data screens reflect what actually
    # happened rather than a guess.
    # ------------------------------------------------------------
    def _pick_excel_file(self):
        if self.window is None:
            return None
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Excel files (*.xlsx;*.xls)", "All files (*.*)"),
        )
        if not result:
            return None
        return result[0]

    # Maps a data-type key (matching import_log's keys and the frontend's
    # MODULES list) to its parser + inserter, handled via if/elif below
    # rather than a lookup table — each parser has a slightly different
    # return shape (itinerary returns 4 values, others return 2), so a
    # uniform table would need per-entry unwrapping logic anyway.
    def import_module(self, data_type):
        path = self._pick_excel_file()
        if not path:
            return {"ok": False, "message": "No file selected."}
        try:
            if data_type == "itinerary":
                rows, _territories, _year_months, warnings = excel_import.parse_itinerary_workbook(path)
                n = db.insert_itinerary_rows(rows)
            elif data_type == "territories":
                rows, warnings = excel_import.parse_territories_workbook(path)
                n = db.upsert_territories(rows)
            elif data_type == "invoices":
                rows, warnings = excel_import.parse_invoices_workbook(path)
                n = db.insert_van_sales_rows(rows)
            elif data_type == "sales_orders":
                rows, warnings = excel_import.parse_sales_orders_workbook(path)
                n = db.insert_pre_sales_rows(rows)
            elif data_type == "outlets":
                rows, warnings = excel_import.parse_outlets_workbook(path)
                n = db.insert_outlets_rows(rows)
            else:
                return {"ok": False, "message": f"Unknown data type '{data_type}'."}

            db.record_import(data_type, n)
            msg = f"Imported {n} row(s)."
            if warnings:
                msg += f" {len(warnings)} warning(s): " + "; ".join(warnings[:3])
                if len(warnings) > 3:
                    msg += f" (+{len(warnings) - 3} more)"
            return {"ok": True, "row_count": n, "message": msg, "warnings": warnings}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def import_sku_sales(self, expected_model, report_date):
        path = self._pick_excel_file()
        if not path:
            return {"ok": False, "message": "No file selected."}
        try:
            rows, warnings = excel_import.parse_sku_sales_workbook(path, expected_model, report_date)
            n = db.insert_product_sales_rows(rows)
            db.record_import("product_sales", n)
            msg = f"Imported {n} row(s) for {expected_model} on {report_date}."
            if warnings:
                msg += f" {len(warnings)} warning(s): " + "; ".join(warnings[:3])
                if len(warnings) > 3:
                    msg += f" (+{len(warnings) - 3} more)"
            return {"ok": True, "row_count": n, "message": msg, "warnings": warnings}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    # ------------------------------------------------------------
    # Real data import: native file picker -> excel_import parser ->
    # db insert -> record_import. This is what the Import Data screen's
    # module cards actually run when clicked.
    # ------------------------------------------------------------
    _MODULE_PARSERS = {
        "itinerary": (excel_import.parse_itinerary_workbook, db.insert_itinerary_rows),
        "territories": (excel_import.parse_territories_workbook, db.upsert_territories),
        "invoices": (excel_import.parse_invoices_workbook, db.insert_van_sales_rows),
        "sales_orders": (excel_import.parse_sales_orders_workbook, db.insert_pre_sales_rows),
        "outlets": (excel_import.parse_outlets_workbook, db.insert_outlets_rows),
    }

    def pick_excel_file(self):
        """Opens a native file picker. Returns the chosen path, or None if cancelled."""
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, file_types=("Excel Files (*.xlsx;*.xls)",)
        )
        return result[0] if result else None

    def import_module(self, data_type):
        """
        Handles Itinerary, Territories, Invoices, Sales Orders, and
        Outlet Master imports — the 5 module types that don't need extra
        parameters beyond the file itself. SKU Sales is handled
        separately by import_sku_sales, since that file needs a Sale
        Model and date supplied alongside it (the source file has
        neither column).
        """
        if data_type not in self._MODULE_PARSERS:
            return {"ok": False, "message": f"Unknown data type '{data_type}'."}

        path = self.pick_excel_file()
        if not path:
            return {"ok": False, "message": "No file selected."}

        parser, inserter = self._MODULE_PARSERS[data_type]
        try:
            rows = parser(path)
        except Exception as e:
            return {"ok": False, "message": f"Could not read file: {e}"}

        if not rows:
            return {"ok": False, "message": "No rows found — check the file matches the expected column format."}

        try:
            n = inserter(rows)
        except Exception as e:
            return {"ok": False, "message": f"Could not save to database: {e}"}

        row_count = n if isinstance(n, int) else len(rows)
        db.record_import(data_type, row_count)
        return {"ok": True, "message": f"Imported {row_count} rows.", "row_count": row_count}

    def import_sku_sales(self, sale_model, report_date):
        path = self.pick_excel_file()
        if not path:
            return {"ok": False, "message": "No file selected."}
        try:
            rows = excel_import.parse_sku_sales_workbook(path, sale_model, report_date)
        except Exception as e:
            return {"ok": False, "message": f"Could not read file: {e}"}

        if not rows:
            return {"ok": False, "message": "No rows found — check the file matches the expected column format."}

        try:
            n = db.insert_product_sales_rows(rows)
        except Exception as e:
            return {"ok": False, "message": f"Could not save to database: {e}"}

        row_count = n if isinstance(n, int) else len(rows)
        db.record_import("product_sales", row_count)
        return {"ok": True, "message": f"Imported {row_count} rows.", "row_count": row_count}

    # ------------------------------------------------------------
    # SFA connection settings + test.
    # ------------------------------------------------------------
    def get_sfa_config(self):
        config = sfa_config.load_config()
        # Never send the password back to the frontend for display —
        # only report whether one is saved, so the Settings screen can
        # show "•••• saved" instead of the plaintext value.
        return {
            "login_url": config.get("login_url", ""),
            "username": config.get("username", ""),
            "has_password": bool(config.get("password")),
            "browser": config.get("browser", "chrome"),
        }

    def save_sfa_config(self, login_url, username, password, browser):
        sfa_config.save_config(login_url, username, password, browser)
        return {"ok": True}

    def test_sfa_connection(self):
        result = sfa_connection.test_connection()
        if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
            with open(result["screenshot_path"], "rb") as f:
                result["screenshot_base64"] = base64.b64encode(f.read()).decode("ascii")
        else:
            result["screenshot_base64"] = None
        return result

    def test_sfa_navigate_sales_org(self):
        result = sfa_connection.test_navigate_sales_organization()
        if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
            with open(result["screenshot_path"], "rb") as f:
                result["screenshot_base64"] = base64.b64encode(f.read()).decode("ascii")
        else:
            result["screenshot_base64"] = None
        return result

    def test_sfa_navigate_itinerary(self):
        result = sfa_connection.test_navigate_itinerary()
        if result.get("screenshot_path") and os.path.exists(result["screenshot_path"]):
            with open(result["screenshot_path"], "rb") as f:
                result["screenshot_base64"] = base64.b64encode(f.read()).decode("ascii")
        else:
            result["screenshot_base64"] = None
        return result
