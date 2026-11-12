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
import threading
import webview
import db
import excel_import
import combined_report
import combined_report_pdf
import last_visit_summary
import sfa_config
import sfa_connection
import sfa_store
import report_pipeline
import automation_store
import schedule_store
import loading_store
import outlet_store
import scheduler
import notifications
import whatsapp_sender


class Api:
    def __init__(self):
        db.init_db()
        sfa_store.init_schema()
        report_pipeline.init_schema()
        automation_store.init_schema()
        automation_store.mark_interrupted_runs()
        schedule_store.init_schema()
        loading_store.init_schema()
        loading_store.mark_interrupted()
        outlet_store.init_schema()
        outlet_store.mark_interrupted()
        import sequence_store
        sequence_store.init_schema()
        import reminders
        reminders.init_schema()
        import rep_attendance
        rep_attendance.init_schema()
        rep_attendance.mark_interrupted()
        sequence_store.mark_interrupted()
        whatsapp_sender.init_schema()
        # IMPORTANT: anything that is not an API method must start with "_".
        # pywebview walks every public attribute of this object to build
        # window.pywebview.api — walking into the native window or the
        # scheduler thread breaks that and makes EVERY button fail with
        # "... is not a function". Underscored attributes are skipped.
        self._win = None          # set by app.py after the window is created
        self._scheduler = None    # set by app.py

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
    # Real data import: native file picker -> excel_import parser ->
    # db insert -> record_import. This is what the Import Data screen's
    # module cards actually run when clicked.
    #
    # Every parser returns a tuple whose FIRST element is the row list
    # and whose LAST element is the warnings list (itinerary also returns
    # territories/year_months in between), so we unpack positionally
    # rather than passing the whole tuple to the inserter.
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
        window = self._win or (webview.windows[0] if webview.windows else None)
        if window is None:
            return None
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Excel files (*.xlsx;*.xls)", "All files (*.*)"),
        )
        return result[0] if result else None

    @staticmethod
    def _format_import_message(row_count, warnings, suffix=""):
        msg = f"Imported {row_count} row(s){suffix}."
        if warnings:
            msg += f" {len(warnings)} warning(s): " + "; ".join(warnings[:3])
            if len(warnings) > 3:
                msg += f" (+{len(warnings) - 3} more)"
        return msg

    def _run_import(self, data_type, parse, insert, log_key, suffix=""):
        """Shared import path: pick file -> parse -> insert -> log."""
        path = self.pick_excel_file()
        if not path:
            return {"ok": False, "message": "No file selected."}

        try:
            parsed = parse(path)
        except Exception as e:
            return {"ok": False, "message": f"Could not read file: {e}"}
        rows, warnings = parsed[0], parsed[-1]

        if not rows:
            msg = "No rows found — check the file matches the expected column format."
            if warnings:
                msg += " " + "; ".join(warnings[:3])
            return {"ok": False, "message": msg, "warnings": warnings}

        try:
            n = insert(rows)
        except Exception as e:
            return {"ok": False, "message": f"Could not save to database: {e}", "warnings": warnings}

        # sqlite's rowcount can be -1 for executemany; fall back to the
        # number of parsed rows so the log never shows a nonsense count.
        row_count = n if isinstance(n, int) and n >= 0 else len(rows)
        db.record_import(log_key, row_count)
        return {
            "ok": True,
            "row_count": row_count,
            "message": self._format_import_message(row_count, warnings, suffix),
            "warnings": warnings,
        }

    def import_module(self, data_type):
        """
        Handles Itinerary, Territories, Invoices, Sales Orders, and
        Outlet Master imports. SKU Sales is handled by import_sku_sales,
        since that file needs a Sale Model and date supplied alongside it.
        """
        if data_type not in self._MODULE_PARSERS:
            return {"ok": False, "message": f"Unknown data type '{data_type}'."}
        parser, inserter = self._MODULE_PARSERS[data_type]
        return self._run_import(data_type, parser, inserter, data_type)

    def import_sku_sales(self, sale_model, report_date):
        return self._run_import(
            "product_sales",
            lambda path: excel_import.parse_sku_sales_workbook(path, sale_model, report_date),
            db.insert_product_sales_rows,
            "product_sales",
            suffix=f" for {sale_model} on {report_date}",
        )

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

    # ------------------------------------------------------------
    # Daily SFA Reports (SKU / Pivot / Hourly) — built from RAW SFA
    # exports. The same report_pipeline.run_reports() is used by the
    # command line and (next) the scheduler.
    # ------------------------------------------------------------
    def _window(self):
        return self._win or (webview.windows[0] if webview.windows else None)

    def get_daily_status(self, date):
        status = sfa_store.get_dataset_status(date)
        groups = sfa_store.get_groups()
        status["groups"] = {g: [{"territory": t, "model": m} for t, m in v] for g, v in groups.items()}
        status["output_root"] = report_pipeline.output_root()
        return status

    def import_sfa_exports(self):
        """Multi-select: pick any number of raw SFA exports; each is detected and imported on its own."""
        window = self._window()
        if window is None:
            return {"ok": False, "message": "Window not ready."}
        paths = window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=("Excel files (*.xlsx)", "All files (*.*)"))
        if not paths:
            return {"ok": False, "message": "No files selected.", "results": []}
        results = sfa_store.import_files(list(paths))
        return {"ok": any(r["ok"] for r in results), "results": results}

    def generate_daily_reports(self, date, kinds, images=True, style=None, review=None):
        kinds = [k for k in kinds if k in report_pipeline.REPORT_KINDS] or list(report_pipeline.REPORT_KINDS)
        if style not in sfa_store.OUTPUT_STYLES:
            style = None  # fall back to the saved setting
        result = report_pipeline.run_reports(date, kinds=kinds, images=images, trigger="manual", style=style,
                                             review=review)
        for r in result["reports"]:
            r.pop("trace", None)
        return result

    def preview_daily_report(self, kind, date):
        try:
            return report_pipeline.build_report(kind, date)
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def get_report_runs(self, limit=15):
        return report_pipeline.get_runs(limit)

    def open_path(self, path):
        """Opens a folder or file in Windows Explorer / its default app."""
        if not path or not os.path.exists(path):
            return {"ok": False, "message": "That file or folder no longer exists."}
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def reports_location(self):
        """Where saved reports are: the main folder and the newest saved run's folder."""
        root = report_pipeline.output_root()
        latest = next((r for r in report_pipeline.get_runs(200)
                       if r.get("folder") and r["status"] != "pending_review" and "_pending" not in r["folder"]
                       and os.path.isdir(r["folder"])), None)
        return {"root": root, "latest": latest["folder"] if latest else None,
                "latest_label": f"{latest['report_date']} · {os.path.basename(latest['folder'])}" if latest else None}

    def choose_output_folder(self):
        """Settings > Save location: pick a folder with Windows' folder picker."""
        window = self._window()
        if window is None:
            return {"ok": False, "message": "Window not ready."}
        paths = window.create_file_dialog(webview.FOLDER_DIALOG, directory=report_pipeline.output_root())
        if not paths:
            return {"ok": False, "message": "No folder chosen.", "cancelled": True}
        folder = paths[0] if isinstance(paths, (list, tuple)) else paths
        ok, msg = sfa_store.save_settings({"output_folder": folder})
        return {"ok": ok, "message": msg if not ok else f"Reports will be saved in {folder}", "folder": folder}

    def reset_output_folder(self):
        sfa_store.save_settings({"output_folder": ""})
        return {"ok": True, "folder": report_pipeline.DEFAULT_OUTPUT_ROOT,
                "message": f"Back to the default folder: {report_pipeline.DEFAULT_OUTPUT_ROOT}"}

    def open_reports_folder(self, which="latest"):
        loc = self.reports_location()
        path = loc["latest"] if which == "latest" and loc["latest"] else loc["root"]
        os.makedirs(path, exist_ok=True)
        return self.open_path(path)

    # Settings > Manager Groups editing
    def save_territory_group(self, territory, group, model="Van", old_territory=None):
        ok, msg = sfa_store.save_territory(territory, group, model, old_territory)
        return {"ok": ok, "message": msg}

    def delete_territory_group(self, territory):
        ok = sfa_store.delete_territory(territory)
        return {"ok": ok, "message": "Removed." if ok else "That territory isn't in any group."}

    def rename_manager_group(self, old, new):
        ok, msg, merged = sfa_store.rename_group(old, new)
        if ok and old != new:
            n = whatsapp_sender.rename_group_in_rules(str(old).strip(), str(new).strip())
            if n:
                msg += f" {n} WhatsApp delivery rule(s) updated to {str(new).strip()}."
        return {"ok": ok, "message": msg}

    def get_report_settings(self):
        s = dict(sfa_store.get_settings())
        from datetime import date as _d
        s["approved_today"] = report_pipeline.approved_on(_d.today().isoformat())
        return s

    def save_report_settings(self, values):
        ok, message = sfa_store.save_settings(values or {})
        return {"ok": ok, "message": message}

    def import_salespulse_database(self, include_history=True):
        window = self._window()
        if window is None:
            return {"ok": False, "message": "Window not ready."}
        paths = window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("SalesPulse database (*.db)", "All files (*.*)"))
        if not paths:
            return {"ok": False, "message": "No file selected."}
        try:
            r = sfa_store.import_salespulse_db(paths[0], include_history=include_history)
        except Exception as e:
            return {"ok": False, "message": f"Could not import: {e}"}
        return {"ok": True, "message": (f"Imported {r['groups']} territory-group rows, {r['settings']} settings, "
                                        f"{r['history_bills']} historical bills and "
                                        f"{r['history_unproductive']} historical unproductive visits."), **r}

    # ------------------------------------------------------------
    # SFA Automation: report profiles, runs, schedule & control.
    # Long work (runs, test runs) happens in a background thread; the
    # UI polls get_live_run() once a second to draw the lanes.
    # ------------------------------------------------------------
    def get_automation_overview(self):
        import sfa_automation
        settings = automation_store.get_automation_settings()
        nxt_o = scheduler.next_occurrence(settings=settings)
        nxt = nxt_o["at"] if nxt_o else None
        cfg = sfa_config.load_config()
        runs = automation_store.list_runs(1)
        return {
            "profiles": automation_store.list_profiles(),
            "settings": settings,
            "next_run": nxt.strftime("%a %d %b, %H:%M") if nxt else None,
            "next_job": nxt_o["job"]["name"] if nxt_o else None,
            "last_run": runs[0] if runs else None,
            "live_active": sfa_automation.LIVE.snapshot().get("active", False),
            "login_ready": bool(cfg.get("login_url") and cfg.get("username") and cfg.get("password")),
            "feeds": automation_store.FEEDS,
            "filter_types": list(automation_store.FILTER_TYPES),
            "date_modes": list(automation_store.DATE_MODES),
            "is_windows": os.name == "nt",
            "task_command": scheduler.scheduled_command(),
        }

    def save_automation_profile(self, data):
        ok, message, profile = automation_store.save_profile(data or {})
        return {"ok": ok, "message": message, "profile": profile}

    def duplicate_automation_profile(self, pid):
        ok, message, profile = automation_store.duplicate_profile(pid)
        return {"ok": ok, "message": message, "profile": profile}

    def delete_automation_profile(self, pid):
        ok = automation_store.delete_profile(pid)
        return {"ok": ok, "message": "Profile deleted." if ok else "That profile no longer exists."}

    def set_automation_profile_enabled(self, pid, enabled):
        automation_store.set_profile_enabled(pid, enabled)
        return {"ok": True}

    def reorder_automation_profiles(self, ids):
        automation_store.reorder_profiles(ids)
        return {"ok": True}

    def detect_profile_filters(self, url, show_browser=False):
        import sfa_automation
        if sfa_automation.LIVE.snapshot().get("active"):
            return {"ok": False, "message": "Wait for the current run to finish first."}
        return sfa_automation.detect_filters(url, headless=not show_browser)

    def start_automation_run(self, report_date=None, profile_ids=None, show_browser=False, style=None, review=None):
        import sfa_automation
        return sfa_automation.start_background(report_date=report_date or None, profile_ids=profile_ids or None,
                                               trigger="manual", headless=not show_browser, style=style or None,
                                               review=review)

    def start_profile_test(self, profile_id, report_date=None, show_browser=True):
        import sfa_automation
        return sfa_automation.start_background(report_date=report_date or None, profile_ids=[profile_id],
                                               trigger="test", headless=not show_browser, test=True,
                                               generate=False)

    def get_live_run(self):
        import sfa_automation
        return sfa_automation.LIVE.snapshot()

    def cancel_automation_run(self):
        import sfa_automation
        sfa_automation.LIVE.cancel()
        return {"ok": True}

    def get_run_log_text(self, run_id=None):
        """Plain-text log: the live/last run, or a past run from the history."""
        import sfa_automation
        s = sfa_automation.LIVE.snapshot()
        if run_id is None or s.get("run_id") == run_id:
            return sfa_automation.log_text(s)
        return automation_store.get_run_log(int(run_id)) or "No log was kept for this run (runs before this update)."

    def copy_text(self, text):
        """Clipboard fallback when the window's own clipboard isn't available."""
        try:
            import subprocess
            if os.name == "nt":
                subprocess.run(["clip"], input=(text or "").encode("utf-16"), check=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return {"ok": True}
            import shutil
            for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["wl-copy"]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, input=(text or "").encode(), check=True)
                    return {"ok": True}
            return {"ok": False, "message": "No clipboard tool found."}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def get_automation_runs(self, limit=20):
        return automation_store.list_runs(limit)

    def save_automation_settings(self, values):
        before = automation_store.get_automation_settings()
        ok, message = automation_store.save_automation_settings(values or {})
        if not ok:
            return {"ok": False, "message": message}
        after = automation_store.get_automation_settings()
        task_keys = ("paused", "use_task_scheduler")
        sync = None
        if os.name == "nt" and (after["use_task_scheduler"] == "1" or before["use_task_scheduler"] == "1") \
                and any(before[k] != after[k] for k in task_keys):
            sync = scheduler.sync_windows_tasks(after)
        nxt = scheduler.next_run(settings=after)
        return {"ok": True, "message": message, "task_sync": sync,
                "next_run": nxt.strftime("%a %d %b, %H:%M") if nxt else None, "settings": after}

    def sync_windows_tasks(self):
        return scheduler.sync_windows_tasks()

    # ------------------------------------------------------------
    # Schedules: jobs, holiday calendar, clock
    # ------------------------------------------------------------
    @staticmethod
    def _fmt_dt(dt):
        return dt.strftime("%a %d %b, %H:%M") if dt else None

    def _sync_tasks_if_used(self):
        s = automation_store.get_automation_settings()
        if os.name == "nt" and s["use_task_scheduler"] == "1":
            return scheduler.sync_windows_tasks(s)
        return None

    def get_schedule_overview(self, week_offset=0):
        from datetime import date, datetime, timedelta
        settings = automation_store.get_automation_settings()
        per_job = scheduler.next_run_per_job(settings=settings)
        jobs = schedule_store.list_jobs()
        fires = schedule_store.last_fires(200)
        for j in jobs:
            j["next_at"] = per_job.get(j["id"]).isoformat() if per_job.get(j["id"]) else None
            j["next_label"] = self._fmt_dt(per_job.get(j["id"]))
            last = next((f for f in fires if f["job_id"] == j["id"]), None)
            j["last_fire"] = last
        start = datetime.combine(date.today() - timedelta(days=date.today().weekday())
                                 + timedelta(weeks=int(week_offset or 0)), datetime.min.time())
        week = schedule_store.occurrences(start, start + timedelta(days=7, seconds=-1), include_skipped=True)
        hol = {h["day"]: h["name"] for h in schedule_store.list_holidays()}
        nxt = scheduler.next_occurrence(settings=settings)
        this_year = date.today().year
        return {
            "jobs": jobs,
            "job_types": schedule_store.JOB_TYPES,
            "week_start": start.date().isoformat(),
            "week": [{"job_id": o["job"]["id"], "job": o["job"]["name"], "type": o["job"]["job_type"],
                      "at": o["at"].isoformat(), "slot": o["slot"], "holiday": o["holiday"],
                      "switched_off": not schedule_store.type_on(o["job"], settings),
                      "skipped_today": settings["skip_date"] == o["at"].date().isoformat()} for o in week],
            "week_holidays": {d: n for d, n in hol.items() if start.date().isoformat() <= d
                              <= (start.date() + timedelta(days=6)).isoformat()},
            "holidays": [h for h in schedule_store.list_holidays() if int(h["day"][:4]) >= this_year - 1],
            "today": date.today().isoformat(),
            "now": datetime.now().isoformat(timespec="seconds"),
            "next": {"job": nxt["job"]["name"], "at": nxt["at"].isoformat(), "label": self._fmt_dt(nxt["at"])}
                    if nxt else None,
            "controls": {k: settings[k] for k in ("paused", "skip_date", "use_task_scheduler")},
            "switches": {k: settings.get(k, "1") for k in ("auto_sfa_downloads", "auto_build_reports", "wa_auto_send",
                                                           "auto_loadings", "auto_outlet_updates", "auto_distributor_sync",
                                                           "auto_master_sync")},
            "clock": scheduler.clock_status(settings),
            "task_times": scheduler.wanted_task_times(),
            "is_windows": os.name == "nt",
            "profiles": [{"id": p["id"], "name": p["name"], "enabled": p["enabled"]}
                         for p in automation_store.list_profiles()],
            "fires": fires[:30],
        }

    def save_schedule_job(self, job):
        ok, msg, saved = schedule_store.save_job(job or {})
        if not ok:
            return {"ok": False, "message": msg}
        return {"ok": True, "message": f"Saved — {saved['summary']}.", "job": saved,
                "task_sync": self._sync_tasks_if_used()}

    def set_schedule_job_enabled(self, job_id, enabled):
        schedule_store.set_job_enabled(int(job_id), bool(enabled))
        return {"ok": True, "task_sync": self._sync_tasks_if_used()}

    def delete_schedule_job(self, job_id):
        schedule_store.delete_job(int(job_id))
        return {"ok": True, "task_sync": self._sync_tasks_if_used()}

    def run_schedule_job_now(self, job_id):
        """Runs a job immediately, exactly as its schedule would (doesn't use up a scheduled slot)."""
        from datetime import date, timedelta
        job = schedule_store.get_job(int(job_id))
        if not job:
            return {"ok": False, "message": "That job no longer exists."}
        if job["job_type"] != "sfa_downloads":
            return {"ok": False, "message": f"{job['type_label']} isn't available yet."}
        import sfa_automation
        day = date.today() - timedelta(days=1 if job["options"].get("report_day") == "yesterday" else 0)
        return sfa_automation.start_background(report_date=day.isoformat(), trigger="manual",
                                               profile_ids=job["options"].get("profile_ids") or None)

    def add_holidays(self, text):
        n, problems = schedule_store.add_holidays(text or "")
        msg = f"{n} holiday(s) saved." if n else "No holidays added."
        return {"ok": n > 0 and not problems, "added": n, "problems": problems,
                "message": msg + (f" {len(problems)} line(s) skipped." if problems else "")}

    def delete_holiday(self, day):
        schedule_store.delete_holiday(day)
        return {"ok": True}

    def check_clock(self):
        return scheduler.check_clock_now()

    def get_clock(self):
        """Small and cheap: polled by the top-bar clock."""
        settings = automation_store.get_automation_settings()
        nxt = scheduler.next_occurrence(settings=settings)
        c = scheduler.clock_status(settings)
        return {"now": c["now"], "level": c["level"], "message": c["message"],
                "paused": settings["paused"] == "1",
                "next": {"job": nxt["job"]["name"], "at": nxt["at"].isoformat(), "label": self._fmt_dt(nxt["at"])}
                        if nxt else None}

    def emergency_stop(self, pause=False):
        """
        'Stop now': closes the SFA / WhatsApp browser of anything running in
        this window at once, stops a background (Task Scheduler) Chimera that
        holds the run lock, clears a lock left by a closed window, and — by
        ticked — pauses scheduled runs so nothing starts again by itself.
        """
        import sfa_automation
        import loading_automation
        done = []
        if sfa_automation.force_stop():
            done.append("Stopped the SFA run in this window.")
        if loading_automation.force_stop():
            done.append("Stopped the Distributor Loadings run (nothing more will be confirmed).")
        import itinerary_automation
        if itinerary_automation.force_stop():
            done.append("Stopped the itinerary read (Outlet Updates).")
        import sequence_automation
        if sequence_automation.force_stop():
            done.append("Stopped the rep-sheet run (outlets not saved yet are saved next time).")
        import rep_attendance_automation
        if rep_attendance_automation.force_stop():
            done.append("Stopped the rep attendance run.")
        if whatsapp_sender.force_stop():
            done.append("Closed the WhatsApp sending window.")
        holder = automation_store.lock_holder()
        if holder:
            pid = int(holder.get("pid") or 0)
            if pid and pid != os.getpid() and automation_store.pid_alive(pid):
                killed = self._kill_process_tree(pid)
                done.append(f"Stopped the background Chimera run ({holder.get('owner', 'scheduled')})." if killed
                            else f"Couldn't stop the background run (process {pid}) — close it in Task Manager.")
                if killed:
                    automation_store.release_lock()
                    automation_store.mark_interrupted_runs()
            elif not sfa_automation.LIVE.snapshot().get("active") and not loading_automation.LIVE.snapshot().get("active") \
                    and not itinerary_automation.LIVE.snapshot().get("active") \
                    and not sequence_automation.LIVE.snapshot().get("active"):
                automation_store.release_lock()
                automation_store.mark_interrupted_runs()
                done.append("Cleared a run that was left unfinished when Chimera was closed.")
        if pause:
            automation_store.save_automation_settings({"paused": "1"})
            self._sync_tasks_if_used()
            done.append("Scheduled runs are paused — switch them back on in Schedules.")
        if not done:
            done.append("Nothing was running.")
        return {"ok": True, "message": " ".join(done), "steps": done}

    @staticmethod
    def _kill_process_tree(pid):
        import subprocess
        try:
            if os.name == "nt":
                r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return r.returncode == 0
            import signal
            os.kill(pid, signal.SIGTERM)
            return True
        except Exception:
            return False

    def _on_window_closing(self):
        """Closing Chimera stops its own runs cleanly and frees the run lock (not exposed to JS)."""
        try:
            import sfa_automation
            sfa_automation.force_stop(timeout=5)
            whatsapp_sender.force_stop(timeout=5)
            import loading_automation
            loading_automation.force_stop(timeout=5)
            import itinerary_automation
            itinerary_automation.force_stop(timeout=5)
            import sequence_automation
            sequence_automation.force_stop(timeout=5)
        except Exception:
            pass
        automation_store.release_lock(only_if_mine=True)

    # ------------------------------------------------------------
    # Outlet Updates — working territories, itineraries, routes board
    # ------------------------------------------------------------
    def get_outlet_updates_overview(self, day=None):
        import itinerary_automation as IA
        from datetime import date as _date, timedelta as _td
        st = IA.settings()
        target = outlet_store.target_day(None, st["prepare_time"])
        day = day or target.isoformat()
        terrs = outlet_store.list_territories()
        working = [t for t in terrs if t["enabled"]]
        d0 = _date.fromisoformat(day)
        months = sorted({outlet_store.month_key(d0), outlet_store.month_key(target),
                         outlet_store.month_key((d0.replace(day=28) + _td(days=5)))})
        board = outlet_store.board(day)
        _tday, due = outlet_store.due_territories(None, st["prepare_time"])
        counts = {}
        for b in board:
            counts[b["state"]] = counts.get(b["state"], 0) + 1
        settings = automation_store.get_automation_settings()
        cfg = sfa_config.load_config()
        return {"day": day, "target_day": target.isoformat(), "today": _date.today().isoformat(),
                "board": board, "counts": counts, "due": due,
                "territories": terrs, "working_count": len(working),
                "months": months, "month_status": outlet_store.month_status_map([t["territory"] for t in terrs], months),
                "settings": {k: settings.get(k, automation_store.DEFAULT_AUTOMATION.get(k, ""))
                             for k in ("auto_outlet_updates", "ou_itinerary_url", "ou_outlets_url", "ou_territory_url",
                                       "ou_prepare_time", "ou_tabs", "paused")},
                "live_active": IA.LIVE.snapshot().get("active", False),
                "login_ready": bool(cfg.get("login_url") and cfg.get("username") and cfg.get("password")),
                "runs": outlet_store.list_runs(12)}

    def get_itinerary_month(self, territory, month):
        return outlet_store.get_month(territory, month)

    def detect_outlet_territories(self):
        import itinerary_automation as IA
        return IA.detect_territories()

    def add_outlet_territory(self, name):
        ok, msg = outlet_store.add_territory(name)
        return {"ok": ok, "message": msg}

    def set_outlet_territories_enabled(self, names, on):
        outlet_store.set_enabled([str(n) for n in names or []], bool(on))
        return {"ok": True}

    def update_outlet_territory(self, name, values):
        ok, msg = outlet_store.update_territory(name, values or {})
        return {"ok": ok, "message": msg}

    def remove_outlet_territory(self, name):
        outlet_store.remove_territory(name)
        return {"ok": True}

    def save_outlet_settings(self, values):
        vals = {k: v for k, v in (values or {}).items()
                if k in ("auto_outlet_updates", "ou_itinerary_url", "ou_outlets_url", "ou_territory_url", "ou_prepare_time",
                         "ou_tabs")}
        ok, msg = automation_store.save_automation_settings(vals)
        return {"ok": ok, "message": msg}

    def start_itinerary_read(self, territories=None, month=None, show_browser=False):
        """Read now. territories None = every working territory; month 'YYYY-MM' (default: the day being prepared)."""
        import itinerary_automation as IA
        months = None
        if month:
            y, m = str(month).split("-")[:2]
            months = [(int(y), int(m))]
        return IA.start_background(territories=list(territories) if territories else None, months=months,
                                   trigger="manual", headless=False if show_browser else None)

    def get_itinerary_live(self):
        import itinerary_automation as IA
        s = IA.LIVE.snapshot()
        s["step_labels"] = IA.STEP_LABELS
        return s

    def get_itinerary_log_text(self, run_id=None):
        import itinerary_automation as IA
        return outlet_store.get_run_log(int(run_id)) if run_id else IA.log_text()

    # ------------------------------------------------------------
    # Outlet Updates — rep sheets (visit sequence + Visit State)
    # ------------------------------------------------------------
    def get_rep_sheets_overview(self):
        import sequence_store as SS
        import sequence_automation as SQ
        st = SQ.settings()
        sheets = SS.list_sheets(limit=150)
        counts = {}
        for x in sheets:
            counts[x["state"]] = counts.get(x["state"], 0) + 1
        live = SQ.LIVE.snapshot()
        return {"sheets": sheets, "counts": counts, "routes": SS.routes_info(),
                "settings": {"ou_routes_url": st["routes_url"], "ou_outlets_url": st["outlets_url"],
                             "seq_need_approval": "1" if st["need_approval"] else "0",
                             "seq_dry_run": "1" if st["dry_run"] else "0"},
                "live_active": bool(live.get("active")), "live_kind": live.get("kind"),
                "runs": SS.list_runs(12)}

    def get_rep_sheet(self, sheet_id):
        import sequence_store as SS
        s = SS.get_sheet(int(sheet_id))
        return {"ok": bool(s), "sheet": s, "message": "" if s else "That sheet no longer exists."}

    def add_rep_sheets(self):
        """Pick one or more filled route sheets (Excel). A copy is kept in Chimera's data folder."""
        import shutil
        import sequence_automation as SQ
        window = self._window()
        if window is None:
            return {"ok": False, "message": "Window not ready."}
        paths = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                          file_types=("Excel files (*.xlsx)", "All files (*.*)"))
        if not paths:
            return {"ok": False, "message": "No file selected."}
        return self._add_rep_files(list(paths) if isinstance(paths, (list, tuple)) else [paths], "upload")

    def _add_rep_files(self, paths, source, sender=""):
        import shutil
        from datetime import datetime as _dt
        import sequence_automation as SQ
        folder = os.path.join(db.APP_DATA_DIR, "rep_sheets", _dt.now().strftime("%Y-%m"))
        os.makedirs(folder, exist_ok=True)
        kept = []
        for p in paths:
            dst = os.path.join(folder, f"{_dt.now():%Y%m%d_%H%M%S}_{os.path.basename(p)}")
            try:
                shutil.copy2(p, dst)
                kept.append(dst)
            except OSError:
                kept.append(p)
        res = SQ.add_files(kept, source=source, sender=sender)
        ok = [r for r in res if r["ok"]]
        auto = None
        if ok and automation_store.get_automation_settings().get("paused") != "1":
            auto = SQ.start_background(trigger=source)        # read SFA for the preview straight away
        return {"ok": bool(ok), "results": res, "started": bool(auto and auto.get("ok")),
                "message": (f"{len(ok)} route sheet(s) added" + (" — reading SFA for the preview now." if auto and auto.get("ok")
                            else ". Click 'Read SFA' to make the preview." if ok else ".")) if ok
                           else "No usable route sheet in what you picked."}

    def set_rep_sheet_states(self, sheet_id, line_ids, state):
        import sequence_store as SS
        ok, msg = SS.set_states(int(sheet_id), list(line_ids or []), str(state))
        return {"ok": ok, "message": msg}

    def approve_rep_sheet(self, sheet_ids, run_now=True):
        import sequence_store as SS
        import sequence_automation as SQ
        ids = [int(i) for i in (sheet_ids if isinstance(sheet_ids, (list, tuple)) else [sheet_ids])]
        errs = []
        for i in ids:
            ok, msg = SS.approve(i, by="you")
            if not ok:
                errs.append(f"#{i}: {msg}")
        done = len(ids) - len(errs)
        started = SQ.start_background(sheet_ids=ids, trigger="manual") if done and run_now else None
        msg = (f"{done} sheet(s) approved" + (" — saving in SFA now." if started and started.get("ok") else
               f". {started['message']} They'll be saved on the next run." if started else ".")) if done else ""
        return {"ok": done > 0, "message": (msg + (" " if msg and errs else "") + " ".join(errs)).strip(),
                "started": bool(started and started.get("ok"))}

    def discard_rep_sheet(self, sheet_id):
        import sequence_store as SS
        ok, msg = SS.discard(int(sheet_id))
        return {"ok": ok, "message": msg}

    def reread_rep_sheet(self, sheet_id):
        """Read SFA's current values again (e.g. after the preview got old, or a read failed)."""
        import sequence_store as SS
        import sequence_automation as SQ
        s = SS.get_sheet(int(sheet_id), lines=False)
        if not s or s["state"] not in ("ready", "failed", "received"):
            return {"ok": False, "message": "Only a sheet waiting for approval or one that failed can be read again."}
        if any(p["level"] == "error" and not p.get("from_sfa") for p in s["problems"]):
            return {"ok": False, "message": "This sheet's file has a problem — fix the file and send it again."}
        SS.set_sheet(int(sheet_id), state="received", message="")
        return SQ.start_background(sheet_ids=[int(sheet_id)], trigger="manual")

    def start_rep_sheets_run(self, show_browser=False):
        import sequence_automation as SQ
        return SQ.start_background(trigger="manual", headless=False if show_browser else None)

    def start_routes_sync(self, show_browser=False):
        import sequence_automation as SQ
        return SQ.start_background(target="routes", trigger="manual", headless=False if show_browser else None)

    def import_routes_excel(self):
        import sequence_automation as SQ
        import sequence_reader as SR
        window = self._window()
        if window is None:
            return {"ok": False, "message": "Window not ready."}
        paths = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                          file_types=("Excel files (*.xlsx)", "All files (*.*)"))
        if not paths:
            return {"ok": False, "message": "No file selected."}
        try:
            return SQ.import_routes_excel(paths[0] if isinstance(paths, (list, tuple)) else paths)
        except SR.ReadError as e:
            return {"ok": False, "message": str(e)}

    def list_sfa_routes(self, territory=None):
        import sequence_store as SS
        return SS.list_routes(territory or None)

    def save_rep_sheet_settings(self, values):
        vals = {k: v for k, v in (values or {}).items()
                if k in ("ou_routes_url", "ou_outlets_url", "seq_need_approval", "seq_dry_run")}
        ok, msg = automation_store.save_automation_settings(vals)
        return {"ok": ok, "message": msg}

    def get_rep_sheets_live(self):
        import sequence_automation as SQ
        s = SQ.LIVE.snapshot()
        s["step_labels"] = SQ.STEP_LABELS
        return s

    def get_rep_sheets_log_text(self, run_id=None):
        import sequence_store as SS
        import sequence_automation as SQ
        s = SQ.LIVE.snapshot()
        if run_id is None or s.get("run_id") == run_id:
            return SQ.log_text(s)
        return SS.get_run_log(int(run_id))

    def export_rep_sheet(self, sheet_id):
        """The sheet's preview / results as an Excel file."""
        import sequence_report
        try:
            path = sequence_report.export_sheet(int(sheet_id))
        except Exception as e:
            return {"ok": False, "message": f"Couldn't export: {e}"}
        return {"ok": True, "path": path, "folder": os.path.dirname(path)}

    # ------------------------------------------------------------
    # Rep Attendance
    # ------------------------------------------------------------
    def get_rep_attendance(self, day=None):
        import rep_attendance as R
        import rep_attendance_automation as RA
        from datetime import date as _d
        day = (day or _d.today().isoformat())[:10]
        info = R.get_day(day)
        st = RA.settings()
        runs = R.list_runs(10, day)
        latest = next((r for r in runs if r["images"]), None)
        live = RA.LIVE.snapshot()
        return {"ok": True, **info, "runs": runs, "latest": latest, "days": R.days_list(45),
                "settings": {"att_url": st["url"], "att_style": st["style"], "att_send_style": st["send_style"],
                             "att_chats": st["chats"], "att_send_pdf": "1" if st["send_pdf"] else "0"},
                "live_active": bool(live.get("active")), "today": _d.today().isoformat(),
                "pending": [r for r in R.list_runs(20) if r["state"] == "pending_review"]}

    def start_rep_attendance(self, day=None, send=False, show_browser=False):
        import rep_attendance_automation as RA
        return RA.start_background(day=day or None, trigger="manual", send=bool(send),
                                   headless=False if show_browser else None)

    def import_rep_attendance(self):
        import rep_attendance_automation as RA
        window = self._window()
        if window is None:
            return {"ok": False, "message": "Window not ready."}
        paths = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                          file_types=("Excel files (*.xlsx)", "All files (*.*)"))
        if not paths:
            return {"ok": False, "message": "No file selected."}
        res = [RA.import_file(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
        ok = [r for r in res if r["ok"]]
        return {"ok": bool(ok), "results": res, "day": ok[-1]["day"] if ok else None,
                "message": " ".join(r["message"] for r in res)}

    def edit_rep_attendance(self, day, territory, values):
        import rep_attendance as R
        v = values or {}
        if v.get("undo"):
            ok, msg = R.undo_edit(day, territory)
        else:
            ok, msg = R.edit_row(day, territory, rep=v.get("rep"), in_time=v.get("in_time"), out_time=v.get("out_time"))
        return {"ok": ok, "message": msg}

    def rebuild_rep_attendance(self, day):
        import rep_attendance_automation as RA
        return RA.rebuild(day)

    def send_rep_attendance(self, run_id):
        """Send a run's images now (also approves a run waiting for approval)."""
        import rep_attendance_automation as RA
        return RA.send(int(run_id), wait=False)

    def discard_rep_attendance(self, run_id):
        import rep_attendance as R
        R.set_run(int(run_id), state="not_sent", message="Not sent — you chose not to.")
        return {"ok": True}

    def get_rep_attendance_history(self, date_from, date_to, search=""):
        import rep_attendance as R
        return R.history(date_from[:10], date_to[:10], search)

    def export_rep_attendance(self, date_from, date_to, search=""):
        import rep_attendance as R
        try:
            path = R.export_history(date_from[:10], date_to[:10], search)
        except Exception as e:
            return {"ok": False, "message": f"Couldn't export: {e}"}
        return {"ok": True, "path": path, "folder": os.path.dirname(path)}

    def save_rep_attendance_settings(self, values):
        vals = {k: v for k, v in (values or {}).items()
                if k in ("att_url", "att_style", "att_send_style", "att_chats", "att_send_pdf")}
        ok, msg = automation_store.save_automation_settings(vals)
        return {"ok": ok, "message": msg}

    def get_rep_attendance_live(self):
        import rep_attendance_automation as RA
        s = RA.LIVE.snapshot()
        s["step_labels"] = RA.STEP_LABELS
        return s

    def get_rep_attendance_log(self, run_id=None):
        import rep_attendance as R
        import rep_attendance_automation as RA
        s = RA.LIVE.snapshot()
        if run_id is None or s.get("run_id") == run_id:
            return RA.log_text(s)
        return R.get_run_log(int(run_id))

    # ------------------------------------------------------------
    # Running now (every process, live)
    # ------------------------------------------------------------
    def get_running_now(self):
        import running_now
        return running_now.overview()

    def stop_process(self, pid):
        """Stops one running process right away (the others keep going)."""
        import running_now
        return running_now.stop(pid)

    # ------------------------------------------------------------
    # WhatsApp reminders
    # ------------------------------------------------------------
    def get_reminders(self):
        import reminders as R
        import schedule_store
        plan = R.plan_day()
        return {"reminders": R.list_reminders(), "default_text": R.DEFAULT_TEXT,
                "jobs": [{"id": j["id"], "name": j["name"], "times": j["times_list"], "enabled": j["enabled"],
                          "type": j["job_type"]} for j in schedule_store.list_jobs()],
                "today": [{**x, "at": x["at"].strftime("%H:%M"), "report_label": R.fmt_time(x["report_time"])}
                          for x in plan],
                "recent": R.recent_sends(30)}

    def save_reminder(self, data):
        import reminders as R
        ok, msg, rid = R.save_reminder(data or {})
        if ok:
            self._sync_tasks_quietly()
        return {"ok": ok, "message": msg, "id": rid}

    def set_reminder_enabled(self, rid, enabled):
        import reminders as R
        R.set_enabled(rid, enabled)
        self._sync_tasks_quietly()
        return {"ok": True}

    def delete_reminder(self, rid):
        import reminders as R
        R.delete_reminder(rid)
        self._sync_tasks_quietly()
        return {"ok": True}

    def send_reminder_test(self, rid):
        import reminders as R
        return R.send_test(rid)

    def preview_reminder(self, text, report_time=""):
        import reminders as R
        return R.render(str(text or ""), str(report_time or ""))

    def _sync_tasks_quietly(self):
        """Windows must also wake Chimera at reminder times (when it's set to use Task Scheduler)."""
        try:
            if automation_store.get_automation_settings().get("use_task_scheduler") == "1":
                threading.Thread(target=scheduler.sync_windows_tasks, daemon=True).start()
        except Exception:
            pass

    # ------------------------------------------------------------
    # Windows notifications
    # ------------------------------------------------------------
    def get_desktop_notify(self):
        import desktop_notify
        st = automation_store.get_automation_settings()
        return {"settings": {k: st.get(k, "1") for k in ("notify_desktop", "notify_started", "notify_approval",
                                                         "notify_finished", "notify_failed")},
                **desktop_notify.status()}

    def save_desktop_notify(self, values):
        vals = {k: ("1" if str(v) in ("1", "true", "True") else "0") for k, v in (values or {}).items()
                if k in ("notify_desktop", "notify_started", "notify_approval", "notify_finished", "notify_failed")}
        ok, msg = automation_store.save_automation_settings(vals)
        return {"ok": ok, "message": msg}

    def test_desktop_notify(self):
        import desktop_notify
        ok, msg = desktop_notify.test()
        return {"ok": ok, "message": msg}

    def take_open_request(self):
        """A Chimera notification was clicked while the window was open: which screen to show."""
        import desktop_notify
        return desktop_notify.take_open_request()

    # ------------------------------------------------------------
    # Activity Monitor
    # ------------------------------------------------------------
    def get_activity(self, date_from=None, date_to=None, process="all", search=""):
        import activity
        try:
            return {"ok": True, **activity.overview(date_from, date_to, process, search)}
        except Exception as e:
            return {"ok": False, "message": f"Couldn't read the activity: {e}"}

    def get_activity_detail(self, source, ref_id):
        import activity
        try:
            return {"ok": True, "text": activity.detail(source, ref_id) or "No further details were recorded."}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def export_activity(self, date_from=None, date_to=None, process="all", search=""):
        import activity
        try:
            path = activity.export_excel(date_from, date_to, process, search)
        except Exception as e:
            return {"ok": False, "message": f"Couldn't export: {e}"}
        return {"ok": True, "path": path, "folder": os.path.dirname(path)}

    # ------------------------------------------------------------
    # Distributor Loadings
    # ------------------------------------------------------------
    def get_loadings_overview(self):
        import loading_automation as LA
        settings = automation_store.get_automation_settings()
        ds = loading_store.list_distributors()
        pend = loading_store.pending_items()
        today = loading_store.today_summary()
        cfg = sfa_config.load_config()
        return {"distributors": ds, "pending_count": len(pend),
                "stats": {"total": len(ds), "daily": sum(d["frequency"] == "daily" for d in ds),
                          "pending": len(pend), "confirmed_today": today.get("confirmed", 0),
                          "prepared_today": sum(today.values()), "nothing_today": today.get("nothing_pending", 0)},
                "settings": {"loading_url": settings.get("loading_url") or loading_store.DEFAULT_URL,
                             "loading_tabs": settings.get("loading_tabs") or "12",
                             "auto_loadings": settings.get("auto_loadings", "1"),
                             "users_url": settings.get("users_url") or "https://lakmee.salespro.lk/it/users",
                             "auto_distributor_sync": settings.get("auto_distributor_sync", "1"),
                             "add_loading_url": settings.get("add_loading_url") or "https://lakmee.salespro.lk/it/loading/create",
                             "add_loading_for": settings.get("add_loading_for", "Today"),
                             "add_loading_company": settings.get("add_loading_company", ""),
                             "add_loading_dry_run": settings.get("add_loading_dry_run", "1")},
                "jobs": [{"id": j["id"], "name": j["name"], "summary": j["summary"], "enabled": j["enabled"]}
                         for j in schedule_store.list_jobs() if j["job_type"] == "loadings"],
                "sync_jobs": [{"id": j["id"], "name": j["name"], "summary": j["summary"], "enabled": j["enabled"],
                               }
                              for j in schedule_store.list_jobs() if j["job_type"] == "distributors"],
                "last_sync": loading_store.last_sync(),
                "live_active": LA.LIVE.snapshot().get("active", False),
                "login_ready": bool(cfg.get("login_url") and cfg.get("username") and cfg.get("password")),
                "filter_types": list(automation_store.FILTER_TYPES), "date_modes": list(automation_store.DATE_MODES),
                "runs": loading_store.list_runs(15)}

    def save_distributor(self, data):
        ok, msg, d = loading_store.save_distributor(data or {})
        return {"ok": ok, "message": msg, "distributor": d}

    def delete_distributor(self, did):
        loading_store.delete_distributor(int(did))
        return {"ok": True}

    def duplicate_distributor(self, did):
        ok, msg, d = loading_store.duplicate_distributor(int(did))
        return {"ok": ok, "message": msg, "distributor": d}

    def set_distributor_frequency(self, ids, frequency):
        ok, refused = loading_store.set_frequency([int(i) for i in ids or []], frequency)
        return {"ok": ok, "refused": refused,
                "message": (f"Not set to Daily (inactive in SFA or no longer listed): {', '.join(refused)}." if refused else "")}

    def save_loading_settings(self, values):
        vals = {k: v for k, v in (values or {}).items()
                if k in ("loading_url", "loading_tabs", "auto_loadings", "users_url", "auto_distributor_sync",
                         "add_loading_url", "add_loading_for", "add_loading_company", "add_loading_dry_run")}
        ok, msg = automation_store.save_automation_settings(vals)
        return {"ok": ok, "message": msg}

    def detect_loading_filters(self, url=None):
        import loading_automation as LA
        return LA.detect_filters(url or None)

    def start_loading_prepare(self, distributor_ids=None, show_browser=False):
        import loading_automation as LA
        return LA.start_background(LA.run_prepare, distributor_ids=[int(i) for i in distributor_ids] if distributor_ids
                                   else None, trigger="manual", headless=not show_browser)

    def start_loading_confirm(self, item_ids, show_browser=False):
        import loading_automation as LA
        ids = [int(i) for i in item_ids or []]
        if not ids:
            return {"ok": False, "message": "Choose at least one loading to confirm."}
        return LA.start_background(LA.run_confirm, item_ids=ids, trigger="manual", headless=not show_browser)

    def get_pending_loadings(self):
        return {"items": sorted(loading_store.pending_items(), key=lambda x: x["distributor_name"].lower())}

    def get_loading_items(self, run_id):
        return {"items": loading_store.list_items(run_id=int(run_id))}

    def skip_loadings(self, item_ids):
        loading_store.skip_items([int(i) for i in item_ids or []])
        return {"ok": True}

    # Add Loading (creates a loading in SFA; dry run until switched off in Settings)
    def get_add_loading_fields(self, distributor_id):
        import loading_create
        d = loading_store.get_distributor(int(distributor_id))
        if not d:
            return {"ok": False, "message": "That distributor no longer exists."}
        st = loading_create.settings()
        return {"ok": True, "fields": loading_create.fields_for(d), "dry_run": st["dry_run"],
                "loading_for": st["loading_for"], "company": st["company"], "blocked": d["blocked"],
                "problem": d["problems"][0] if d["blocked"] else ""}

    def read_add_loading(self, distributor_id, fields=None, show_browser=False):
        import loading_create
        return loading_create.read_form(int(distributor_id), fields or {}, headless=False if show_browser else None)

    def create_add_loading(self, distributor_id, quantities, fields=None, show_browser=False):
        import loading_create
        return loading_create.create(int(distributor_id), quantities or {}, fields or {},
                                     headless=False if show_browser else None)

    def start_distributor_sync(self, show_browser=False):
        """Distributor Loadings › Update distributor list now (from SFA's Users page)."""
        import distributor_sync
        return distributor_sync.start_background(trigger="manual", headless=False if show_browser else None)

    def import_distributor_excel(self):
        """A User Profile export picked by hand (Users page › Excel)."""
        import distributor_sync
        window = self._window()
        if window is None:
            return {"ok": False, "message": "Window not ready."}
        paths = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                          file_types=("Excel files (*.xlsx)", "All files (*.*)"))
        if not paths:
            return {"ok": False, "message": "No file selected."}
        try:
            return distributor_sync.import_excel(paths[0] if isinstance(paths, (list, tuple)) else paths)
        except distributor_sync.SyncError as e:
            return {"ok": False, "message": e.message}

    def get_loading_live(self):
        import loading_automation as LA
        return LA.LIVE.snapshot()

    def get_loading_log_text(self, run_id=None):
        import loading_automation as LA
        s = LA.LIVE.snapshot()
        if run_id is None or s.get("run_id") == run_id:
            return LA.log_text(s)
        return loading_store.get_run_log(int(run_id)) or "No log kept for that run."

    def check_dependencies(self):
        """Python packages Chimera needs that aren't installed on this PC (shown as a banner)."""
        import importlib.util
        needed = {"pandas": "pandas", "openpyxl": "openpyxl", "reportlab": "reportlab",
                  "playwright": "playwright", "pypdfium2": "pypdfium2", "PIL": "pillow"}
        missing = [pkg for mod, pkg in needed.items() if importlib.util.find_spec(mod) is None]
        return {"missing": missing, "command": "py -m pip install -r requirements.txt" if missing else ""}

    def get_data_usage(self):
        """Settings > Network & Data: data saver state + measured usage."""
        out = automation_store.data_usage_summary()
        out["data_saver"] = automation_store.get_automation_settings().get("data_saver") == "1"
        return out

    # ------------------------------------------------------------
    # Notifications (bell), Overview feed, report previews, WhatsApp, About
    # ------------------------------------------------------------
    def get_notifications(self):
        return notifications.summary()

    def mark_notifications_read(self):
        notifications.mark_read()
        return {"ok": True}

    def get_overview_feed(self):
        return {"events": notifications.events(8), "health": notifications.data_health()}

    def list_pending_reports(self):
        return report_pipeline.list_pending()

    def get_report_preview(self, run_id):
        run = report_pipeline.get_run(run_id)
        if not run:
            return {"ok": False, "message": "That run no longer exists."}
        return {"ok": True, "run": run, "items": report_pipeline.image_items(run),
                "has_rules": bool([r for r in whatsapp_sender.list_rules() if r["enabled"]])}

    def get_image_b64(self, path):
        """Only serves images inside Chimera's own export folder."""
        roots = [os.path.realpath(os.path.join(os.path.expanduser("~"), "Chimera")),
                 os.path.realpath(report_pipeline.output_root())]          # a save location chosen in Settings
        real = os.path.realpath(path or "")
        if not any(real.startswith(r + os.sep) for r in roots) or not real.lower().endswith(".png") or not os.path.exists(real):
            return None
        with open(real, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    def approve_report_run(self, run_id, send_whatsapp=False):
        r = report_pipeline.approve_run(run_id)
        if r.get("ok") and send_whatsapp:
            r["whatsapp"] = whatsapp_sender.deliver_run(run_id)
        return r

    def rebuild_report_run(self, run_id, style):
        """Review pop-up: build the same reports again in another style (still waiting for approval)."""
        run = report_pipeline.get_run(int(run_id))
        if not run:
            return {"ok": False, "message": "That report run no longer exists."}
        if style not in sfa_store.OUTPUT_STYLES:
            return {"ok": False, "message": "Choose Classic, Professional or Both."}
        kinds = [r["kind"] for r in run["summary"].get("reports", [])] or list(report_pipeline.REPORT_KINDS)
        if run["status"] == "pending_review":
            report_pipeline.discard_run(run["id"])
        new = report_pipeline.run_reports(run["report_date"], kinds=kinds, trigger="manual", style=style, review=True)
        for r in new.get("reports", []):
            r.pop("trace", None)
        return {"ok": new.get("ok", False), "run_id": new.get("run_id"), "status": new.get("status"),
                "message": "" if new.get("ok") else (new.get("error") or "The reports couldn't be built.")}

    def discard_report_run(self, run_id):
        return report_pipeline.discard_run(run_id)

    def get_whatsapp_overview(self):
        st = whatsapp_sender.linked_status()
        settings = automation_store.get_automation_settings()
        return {"rules": whatsapp_sender.list_rules(), "deliveries": whatsapp_sender.list_deliveries(20),
                "profile_exists": st["profile_exists"], "chats": st["chats"],
                "groups": sorted(sfa_store.get_groups().keys()),
                "settings": {k: settings[k] for k in ("wa_auto_send", "wa_show_browser", "wa_style")},
                "live": whatsapp_sender.WA.snapshot(),
                "last_saved_run": next((r for r in report_pipeline.get_runs(30)
                                        if r["status"] in ("success", "partial")), None)}

    def get_whatsapp_chat_list(self):
        """The chat picker: names, last message, time and small profile photos (base64 JPEG)."""
        out = []
        for c in whatsapp_sender.cached_chats():
            pic = None
            p = c.get("avatar") or ""
            real = os.path.realpath(p) if p else ""
            if real and real.startswith(os.path.realpath(whatsapp_sender.AVATAR_DIR) + os.sep) and os.path.exists(real):
                with open(real, "rb") as f:
                    pic = base64.b64encode(f.read()).decode("ascii")
            out.append({**{k: c[k] for k in ("name", "time", "preview", "unread", "group", "icon")}, "photo": pic})
        return {"chats": out}

    def save_whatsapp_rule(self, data):
        ok, msg = whatsapp_sender.save_rule(data or {})
        return {"ok": ok, "message": msg}

    def delete_whatsapp_rule(self, rid):
        whatsapp_sender.delete_rule(rid)
        return {"ok": True}

    def whatsapp_link(self):
        return whatsapp_sender.link_whatsapp()

    def whatsapp_load_chats(self):
        return whatsapp_sender.load_chats()

    def whatsapp_send_test(self, chat_name):
        return whatsapp_sender.send_test(chat_name)

    def whatsapp_send_run(self, run_id):
        return whatsapp_sender.deliver_run(run_id)

    def whatsapp_plan(self, run_id):
        run = report_pipeline.get_run(run_id)
        return whatsapp_sender.plan_delivery(run) if run else []

    def get_whatsapp_log_text(self):
        """WhatsApp log as text, plus the page-details files it mentions (for diagnosis)."""
        import re as _re
        s = whatsapp_sender.WA.snapshot()
        lines = [f"Chimera WhatsApp — {s.get('action') or ''} — started {s.get('started_at') or ''}"]
        if s.get("result"):
            lines.append(f"Result: {s['result'].get('message', '')}")
        lines.append("")
        lines += [f"{x['t']} {x['level'].upper():5} {x['msg']}" for x in s.get("log", [])]
        seen = []
        for x in s.get("log", []):
            for path in _re.findall(r'[A-Za-z]:\\(?:[^\\\n:*?"<>|]+\\)*[^\\\n:*?"<>|]+?\.txt|/[^\s]*?\.txt', x["msg"]):
                if path not in seen and os.path.exists(path):
                    seen.append(path)
        for path in seen[-4:]:
            try:
                with open(path, encoding="utf-8") as f:
                    lines += ["", f"===== {os.path.basename(path)} =====", f.read().strip()]
            except OSError:
                pass
        return "\n".join(lines)

    def get_whatsapp_live(self):
        return whatsapp_sender.WA.snapshot()
