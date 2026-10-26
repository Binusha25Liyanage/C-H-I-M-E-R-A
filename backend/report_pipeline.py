"""
report_pipeline.py
One function — run_reports() — that the UI, the command line, and (next)
the scheduler all call, so a scheduled run behaves exactly like a manual
one.

    import files (optional) -> check inputs -> build reports -> PDFs -> images -> log

Each run writes into its own timestamped folder, e.g.
    ~/Chimera/exports/SFA Reports/2026-10-26/1405/
so a failed or partial run can never overwrite an earlier good one, and
every run is recorded in the report_runs table (shown as run history).
"""

import json
import os
import traceback
from datetime import datetime

import db
import sfa_store
import daily_reports
import daily_reports_pdf

REPORT_KINDS = ("sku", "pivot", "hourly")
REPORT_LABELS = {"sku": "SKU Report", "pivot": "Pivot Report", "hourly": "Hourly BC & SV Report"}
REQUIRED_INPUTS = {
    "sku": ("sku_van", "sku_pre"),
    "pivot": ("invoices", "sales_orders"),
    "hourly": ("invoices", "sales_orders", "unproductive"),
}
DEFAULT_OUTPUT_ROOT = os.path.join(os.path.expanduser("~"), "Chimera", "exports", "SFA Reports")


def init_schema():
    conn = db.get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            report_date TEXT NOT NULL,
            trigger     TEXT NOT NULL,     -- 'manual' | 'scheduled' | 'cli'
            status      TEXT NOT NULL,     -- 'running' | 'success' | 'partial' | 'failed'
            folder      TEXT,
            summary     TEXT               -- JSON: per-report outcome, imports, notes
        )""")
    conn.commit()
    conn.close()


def _log_start(date, trigger):
    conn = db.get_connection()
    cur = conn.execute("INSERT INTO report_runs (started_at, report_date, trigger, status) VALUES (?, ?, ?, 'running')",
                       (datetime.now().isoformat(timespec="seconds"), date, trigger))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def _log_finish(run_id, status, folder, summary):
    conn = db.get_connection()
    conn.execute("UPDATE report_runs SET finished_at=?, status=?, folder=?, summary=? WHERE id=?",
                 (datetime.now().isoformat(timespec="seconds"), status, folder, json.dumps(summary), run_id))
    conn.commit()
    conn.close()


def get_runs(limit=30):
    conn = db.get_connection()
    rows = conn.execute("""SELECT id, started_at, finished_at, report_date, trigger, status, folder, summary
                           FROM report_runs ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    keys = ["id", "started_at", "finished_at", "report_date", "trigger", "status", "folder", "summary"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
        out.append(d)
    return out


def build_report(kind, date, settings=None):
    if kind == "sku":
        return daily_reports.build_sku_report(date, settings)
    if kind == "pivot":
        return daily_reports.build_pivot_report(date, settings)
    if kind == "hourly":
        return daily_reports.build_hourly_report(date, settings)
    raise ValueError(f"Unknown report '{kind}'.")


def run_reports(date, kinds=REPORT_KINDS, files=None, output_root=None, images=True,
                trigger="manual", allow_missing_inputs=False, style=None):
    """
    date: 'YYYY-MM-DD'. kinds: any of sku / pivot / hourly (one = single
    mode, several = bulk — same code path). style: 'classic' | 'professional'
    | 'both' (default: the saved output_style setting). files: optional raw SFA exports
    to import first. A report whose inputs aren't loaded for `date` is
    skipped with a reason unless allow_missing_inputs=True.

    Returns {"ok", "status", "run_id", "folder", "imports", "reports": [...]}.
    """
    output_root = output_root or DEFAULT_OUTPUT_ROOT
    run_id = _log_start(date, trigger)
    folder = os.path.join(output_root, date, datetime.now().strftime("%H%M%S"))
    summary = {"imports": [], "reports": []}
    try:
        if files:
            summary["imports"] = sfa_store.import_files(files)

        status = sfa_store.get_dataset_status(date)
        loaded = {d["dataset"] for d in status["datasets"] if d["loaded"]}
        has_history = status["history_bills"] > 0
        settings = sfa_store.get_settings()
        rotate = True
        chosen = style or settings.get("output_style", "classic")
        if chosen not in sfa_store.OUTPUT_STYLES:
            raise ValueError(f"Unknown output style '{chosen}'.")
        styles = list(daily_reports_pdf.STYLES) if chosen == "both" else [chosen]
        summary["style"] = chosen
        if not sfa_store.get_groups():
            summary["warning"] = ("No manager groups are set up, so every territory will appear under "
                                  "'Unassigned'. Import them in Settings -> Manager groups.")

        for kind in kinds:
            entry = {"kind": kind, "label": REPORT_LABELS[kind]}
            missing = [d for d in REQUIRED_INPUTS[kind] if d not in loaded]
            if missing and not allow_missing_inputs and not (has_history and kind != "sku"):
                entry.update(ok=False, reason="Missing input(s): " + ", ".join(
                    sfa_store.DATASET_LABELS[m] for m in missing))
                summary["reports"].append(entry)
                continue
            try:
                report = build_report(kind, date, settings)
                outputs = []
                for st in styles:
                    sub = folder if len(styles) == 1 else os.path.join(folder, st.capitalize())
                    pdf, pages = daily_reports_pdf.write_pdf(kind, report, sub, style=st)
                    imgs = daily_reports_pdf.pdf_to_images(pdf, pages, os.path.join(sub, "WhatsApp"),
                                                           rotate_wide=rotate) if images else []
                    outputs.append({"style": st, "pdf": pdf, "images": imgs})
                entry.update(ok=True, pdf=outputs[0]["pdf"], images=[i for o in outputs for i in o["images"]],
                             outputs=outputs, notes=report["notes"], missing_inputs=missing)
            except Exception as e:
                entry.update(ok=False, reason=f"{type(e).__name__}: {e}",
                             trace=traceback.format_exc(limit=3))
            summary["reports"].append(entry)

        oks = [r for r in summary["reports"] if r["ok"]]
        state = "success" if len(oks) == len(summary["reports"]) and oks else ("partial" if oks else "failed")
        _log_finish(run_id, state, folder if oks else None, summary)
        return {"ok": bool(oks), "status": state, "run_id": run_id, "folder": folder if oks else None,
                **summary}
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"
        _log_finish(run_id, "failed", None, summary)
        return {"ok": False, "status": "failed", "run_id": run_id, "folder": None, **summary}
