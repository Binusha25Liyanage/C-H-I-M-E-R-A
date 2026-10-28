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
import sfa_store
import report_pipeline
import automation_store
import schedule_store
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
        status["output_root"] = report_pipeline.DEFAULT_OUTPUT_ROOT
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

    def get_report_settings(self):
        return sfa_store.get_settings()

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

    def start_automation_run(self, report_date=None, profile_ids=None, show_browser=False):
        import sfa_automation
        return sfa_automation.start_background(report_date=report_date or None, profile_ids=profile_ids or None,
                                               trigger="manual", headless=not show_browser)

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
                                                           "auto_master_sync", "auto_visit_state", "auto_sequence")},
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
        done = []
        if sfa_automation.force_stop():
            done.append("Stopped the SFA run in this window.")
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
            elif not sfa_automation.LIVE.snapshot().get("active"):
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
        except Exception:
            pass
        automation_store.release_lock(only_if_mine=True)

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
        root = os.path.realpath(os.path.join(os.path.expanduser("~"), "Chimera"))
        real = os.path.realpath(path or "")
        if not real.startswith(root + os.sep) or not real.lower().endswith(".png") or not os.path.exists(real):
            return None
        with open(real, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")

    def approve_report_run(self, run_id, send_whatsapp=False):
        r = report_pipeline.approve_run(run_id)
        if r.get("ok") and send_whatsapp:
            r["whatsapp"] = whatsapp_sender.deliver_run(run_id)
        return r

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

    def get_whatsapp_live(self):
        return whatsapp_sender.WA.snapshot()
