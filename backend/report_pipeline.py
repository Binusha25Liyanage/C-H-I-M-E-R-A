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
import shutil
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


def _page_group(kind, page_name):
    """'SKU Report - Asela' -> 'Asela'; the hourly report covers every group."""
    if kind == "hourly":
        return "*"
    return page_name.split(" - ", 1)[1] if " - " in page_name else page_name


def run_reports(date, kinds=REPORT_KINDS, files=None, output_root=None, images=True,
                trigger="manual", allow_missing_inputs=False, style=None, review=None):
    """
    date: 'YYYY-MM-DD'. kinds: any of sku / pivot / hourly (one = single
    mode, several = bulk — same code path). style: 'classic' | 'professional'
    | 'both' (default: the saved output_style setting). files: optional raw SFA exports
    to import first. A report whose inputs aren't loaded for `date` is
    skipped with a reason unless allow_missing_inputs=True.

    review: True = "preview before saving": outputs go to a pending folder
    and the run waits (status 'pending_review') until approve_run() or
    discard_run(). Default comes from the delivery_mode setting.

    Returns {"ok", "status", "run_id", "folder", "imports", "reports": [...]}.
    """
    output_root = output_root or DEFAULT_OUTPUT_ROOT
    if review is None:
        review = sfa_store.get_settings().get("delivery_mode") == "preview"
    run_id = _log_start(date, trigger)
    final_folder = os.path.join(output_root, date, datetime.now().strftime("%H%M%S"))
    folder = os.path.join(output_root, "_pending", f"run_{run_id}") if review else final_folder
    images = images or review          # previews are the page images
    summary = {"imports": [], "reports": [], "review": bool(review), "final_folder": final_folder,
               "output_root": output_root}
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
                    turned = []
                    imgs = daily_reports_pdf.pdf_to_images(pdf, pages, os.path.join(sub, "WhatsApp"),
                                                           rotate_wide=rotate, rotated_out=turned) if images else []
                    outputs.append({"style": st, "pdf": pdf, "images": imgs,
                                    "items": [{"path": im, "page": pg, "group": _page_group(kind, pg), "rotated": rt}
                                              for im, pg, rt in zip(imgs, pages, turned)]})
                entry.update(ok=True, pdf=outputs[0]["pdf"], images=[i for o in outputs for i in o["images"]],
                             outputs=outputs, notes=report["notes"], missing_inputs=missing)
            except Exception as e:
                entry.update(ok=False, reason=f"{type(e).__name__}: {e}",
                             trace=traceback.format_exc(limit=3))
            summary["reports"].append(entry)

        oks = [r for r in summary["reports"] if r["ok"]]
        state = "success" if len(oks) == len(summary["reports"]) and oks else ("partial" if oks else "failed")
        summary["final_status"] = state
        if review and oks:
            state = "pending_review"
        elif review and os.path.isdir(folder):
            shutil.rmtree(folder, ignore_errors=True)
        _log_finish(run_id, state, folder if oks else None, summary)
        return {"ok": bool(oks), "status": state, "run_id": run_id, "folder": folder if oks else None,
                **summary}
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"
        _log_finish(run_id, "failed", None, summary)
        return {"ok": False, "status": "failed", "run_id": run_id, "folder": None, **summary}


# ---------------------------------------------------------------------------
# Preview before saving: approve / discard a pending run
# ---------------------------------------------------------------------------
def get_run(run_id):
    return next((r for r in get_runs(500) if r["id"] == run_id), None)


def list_pending():
    return [r for r in get_runs(100) if r["status"] == "pending_review"]


def _rewrite_paths(obj, old, new):
    if isinstance(obj, str):
        return new + obj[len(old):] if obj.startswith(old) else obj
    if isinstance(obj, list):
        return [_rewrite_paths(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {k: _rewrite_paths(v, old, new) for k, v in obj.items()}
    return obj


def approve_run(run_id):
    """Moves a previewed run's files to the normal output folder and marks it saved."""
    run = get_run(run_id)
    if not run:
        return {"ok": False, "message": "That run no longer exists."}
    if run["status"] != "pending_review":
        return {"ok": False, "message": f"This run is already {run['status'].replace('_', ' ')}."}
    old, summary = run["folder"], run["summary"]
    new = summary.get("final_folder") or os.path.join(DEFAULT_OUTPUT_ROOT, run["report_date"],
                                                       datetime.now().strftime("%H%M%S"))
    if not old or not os.path.isdir(old):
        return {"ok": False, "message": "The preview files are missing (were they deleted?)."}
    if os.path.exists(new):
        new = new + "_" + datetime.now().strftime("%S%f")[:4]
    os.makedirs(os.path.dirname(new), exist_ok=True)
    shutil.move(old, new)
    summary = _rewrite_paths(summary, old, new)
    state = summary.get("final_status", "success")
    conn = db.get_connection()
    conn.execute("UPDATE report_runs SET status=?, folder=?, summary=? WHERE id=?",
                 (state, new, json.dumps(summary), run_id))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Reports saved.", "folder": new, "status": state, "run_id": run_id}


def discard_run(run_id):
    run = get_run(run_id)
    if not run:
        return {"ok": False, "message": "That run no longer exists."}
    if run["status"] != "pending_review":
        return {"ok": False, "message": f"This run is already {run['status'].replace('_', ' ')}."}
    if run["folder"] and os.path.isdir(run["folder"]):
        shutil.rmtree(run["folder"], ignore_errors=True)
    conn = db.get_connection()
    conn.execute("UPDATE report_runs SET status='discarded', folder=NULL WHERE id=?", (run_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Preview discarded."}


def image_items(run, style=None):
    """[{path, kind, label, group, page, style}] for a saved/pending run — used for previews and WhatsApp."""
    out = []
    for rep in run["summary"].get("reports", []):
        if not rep.get("ok"):
            continue
        for o in rep.get("outputs", []):
            if style and o["style"] != style:
                continue
            for it in o.get("items", []):
                out.append({**it, "kind": rep["kind"], "label": rep["label"], "style": o["style"]})
    return out
