"""
sfa_store.py
Storage for the daily SFA data that feeds the SKU / Pivot / Hourly reports.

Snapshot model: every SFA export is a full picture of its day at the moment
it was downloaded. Importing a newer export of the same dataset for the
same date REPLACES the older one (in a single transaction), so running the
pipeline several times a day always reflects the latest SFA state —
including orders that were cancelled or invoiced since the last run — and
never double-counts.

Manager groups live in the existing `territories` table
(territory, group_name, sale_model) — the same Group / Territory / Type
list SalesPulse used — so there is one source of truth for it.
"""

import json
import sqlite3
from collections import defaultdict
from datetime import datetime

import db
import sfa_exports as X

DEFAULT_SETTINGS = {
    "first_bill_time": "09:00",     # Pivot: first bill gap is measured from this time
    "gap_red_minutes": "30",        # Pivot: gaps >= this are highlighted red
    "pivot_bill_count": "3",        # Pivot: how many bills per territory to show
    "small_bill_lines": "4",        # SKU: "Bills < N Items"
    "focus_product": "Lakposha 200g",  # SKU: bills containing this product are counted
    "hourly_start_hour": "8",       # Hourly: first bucket starts at this hour
    "hourly_end_hour": "18",        # Hourly: last hourly bucket ends here; later = "After"
    "separate_early_bucket": "1",   # Hourly: 1 = own "Before 8AM" column; 0 = fold into first bucket (SalesPulse style)
    "van_bc_target": "20",          # Hourly: daily targets (territory turns highlighted
    "van_sv_target": "100000",      #   when BOTH bills and value targets are met)
    "pre_bc_target": "30",
    "pre_sv_target": "125000",
    "output_style": "classic",      # classic (SalesPulse look) | professional (Chimera) | both
}

OUTPUT_STYLES = ("classic", "professional", "both")

DATASET_LABELS = {k: v["label"] for k, v in X.DATASETS.items()}


def init_schema():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sfa_bills (
            bill_no      TEXT PRIMARY KEY,
            dataset      TEXT NOT NULL,      -- 'invoices' | 'sales_orders' | 'salespulse_history'
            bill_date    TEXT NOT NULL,
            kind         TEXT NOT NULL,      -- 'INV' | 'SO'
            so_number    TEXT,
            territory    TEXT,
            rep          TEXT,
            route        TEXT,
            customer     TEXT,
            outlet_code  TEXT,
            amount       REAL NOT NULL,
            created_time TEXT,               -- 'HH:MM'
            minutes      INTEGER,
            status       TEXT,
            territory_source TEXT            -- NULL = from file; otherwise how it was resolved
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sfa_bills_date ON sfa_bills(bill_date, territory)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sfa_sku_lines (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset      TEXT NOT NULL,      -- 'sku_van' | 'sku_pre'
            line_date    TEXT NOT NULL,
            model        TEXT NOT NULL,      -- 'van' | 'pre'
            territory    TEXT,
            asm          TEXT,
            rep          TEXT,
            outlet_code  TEXT,
            outlet       TEXT,
            bill_no      TEXT NOT NULL,
            product_code TEXT,
            product      TEXT,
            units        REAL,
            value        REAL
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sfa_sku_date ON sfa_sku_lines(line_date, dataset)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sfa_unproductive (
            unproductive_no TEXT PRIMARY KEY,
            dataset      TEXT NOT NULL,
            visit_date   TEXT NOT NULL,
            territory    TEXT,
            rep          TEXT,
            rep_model    TEXT,
            route        TEXT,
            customer     TEXT,
            reason       TEXT,
            visit_time   TEXT,
            minutes      INTEGER
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sfa_unprod_date ON sfa_unproductive(visit_date, territory)")
    # One row per (dataset, date): what's loaded, from which file, when SFA generated it.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sfa_datasets (
            dataset      TEXT NOT NULL,
            data_date    TEXT NOT NULL,
            rows         INTEGER NOT NULL,
            source_file  TEXT,
            generated_at TEXT,
            imported_at  TEXT NOT NULL,
            filters      TEXT,
            warnings     TEXT,
            PRIMARY KEY (dataset, data_date)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
    for k, v in DEFAULT_SETTINGS.items():
        cur.execute("INSERT OR IGNORE INTO report_settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_settings():
    conn = db.get_connection()
    rows = dict(conn.execute("SELECT key, value FROM report_settings").fetchall())
    conn.close()
    return {**DEFAULT_SETTINGS, **rows}


def save_settings(values):
    """Validates and saves. Returns (ok, message)."""
    clean = {}
    for k, v in values.items():
        if k not in DEFAULT_SETTINGS:
            continue
        v = str(v).strip()
        if k == "first_bill_time":
            if not X.to_hhmm(v):
                return False, f"First bill time '{v}' is not a valid time (use HH:MM)."
            v = X.to_hhmm(v)
        elif k == "focus_product":
            if not v:
                return False, "Focus product can't be empty."
        elif k == "output_style":
            if v not in OUTPUT_STYLES:
                return False, f"Output style must be one of: {', '.join(OUTPUT_STYLES)}."
        else:
            try:
                n = float(v)
            except ValueError:
                return False, f"{k.replace('_', ' ')} must be a number."
            if n < 0:
                return False, f"{k.replace('_', ' ')} can't be negative."
            v = str(int(n)) if n == int(n) else str(n)
        clean[k] = v
    s = {**get_settings(), **clean}
    if not (0 <= int(float(s["hourly_start_hour"])) < int(float(s["hourly_end_hour"])) <= 24):
        return False, "Hourly start hour must be before the end hour (0-24)."
    conn = db.get_connection()
    conn.executemany("INSERT OR REPLACE INTO report_settings (key, value) VALUES (?, ?)", clean.items())
    conn.commit()
    conn.close()
    return True, "Settings saved."


# ---------------------------------------------------------------------------
# Manager groups (territories table)
# ---------------------------------------------------------------------------
def get_groups():
    """{group_name: [(territory, 'Van'|'Pre'), ...]} sorted, from the territories table."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT group_name, territory, sale_model FROM territories ORDER BY group_name, territory").fetchall()
    conn.close()
    groups = defaultdict(list)
    for g, t, m in rows:
        groups[g].append((t, "Pre" if (m or "").lower().startswith("pre") else "Van"))
    return dict(groups)


def import_salespulse_db(path, include_history=True):
    """
    One-time migration from SalesPulse's sales_data.db: manager groups,
    report settings, and (optionally) bill + unproductive-visit history.
    Never overwrites dates Chimera already has from raw SFA exports.
    """
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "territory_groups" not in tables:
            raise ValueError("This doesn't look like a SalesPulse database (no territory_groups table).")
        groups = src.execute("SELECT group_name, territory, selling_type FROM territory_groups").fetchall()
        sp_settings = dict(src.execute("SELECT key, value FROM settings").fetchall()) if "settings" in tables else {}
        bills = src.execute(
            "SELECT invoice_date, territory, customer, invoice_number, amount, created_time, time_minutes "
            "FROM invoices").fetchall() if include_history and "invoices" in tables else []
        unprod = src.execute(
            "SELECT visit_date, territory, customer, unproductive_no, visit_time, time_minutes "
            "FROM unproductive_visits").fetchall() if include_history and "unproductive_visits" in tables else []
    finally:
        src.close()

    conn = db.get_connection()
    cur = conn.cursor()
    cur.executemany(
        """INSERT INTO territories (territory, group_name, sale_model) VALUES (?, ?, ?)
           ON CONFLICT(territory) DO UPDATE SET group_name=excluded.group_name, sale_model=excluded.sale_model""",
        [(t.strip(), g.strip(), "Pre Selling" if (st or "").lower().startswith("pre") else "Van Selling")
         for g, t, st in groups if t and g])

    mapping = {"first_bill_time": "first_bill_time", "van_bc_threshold": "van_bc_target",
               "van_sv_threshold": "van_sv_target", "pre_bc_threshold": "pre_bc_target",
               "pre_sv_threshold": "pre_sv_target"}
    for sk, ck in mapping.items():
        if sk in sp_settings:
            v = sp_settings[sk]
            try:
                f = float(v)
                v = str(int(f)) if f == int(f) else str(f)
            except ValueError:
                pass
            cur.execute("INSERT OR REPLACE INTO report_settings (key, value) VALUES (?, ?)", (ck, v))

    have_bill_dates = {r[0] for r in cur.execute(
        "SELECT DISTINCT bill_date FROM sfa_bills WHERE dataset != 'salespulse_history'")}
    have_unprod_dates = {r[0] for r in cur.execute(
        "SELECT DISTINCT visit_date FROM sfa_unproductive WHERE dataset != 'salespulse_history'")}
    nb = nu = 0
    for d, t, cust, no, amt, ctime, mins in bills:
        if not no or d in have_bill_dates:
            continue
        cur.execute("""INSERT OR IGNORE INTO sfa_bills (bill_no, dataset, bill_date, kind, territory, customer,
                       amount, created_time, minutes) VALUES (?, 'salespulse_history', ?, ?, ?, ?, ?, ?, ?)""",
                    (no, d, "SO" if str(no).upper().startswith("SO") else "INV", t, cust, amt or 0, ctime, mins))
        nb += cur.rowcount
    for d, t, cust, no, vtime, mins in unprod:
        if not no or d in have_unprod_dates:
            continue
        cur.execute("""INSERT OR IGNORE INTO sfa_unproductive (unproductive_no, dataset, visit_date, territory,
                       customer, visit_time, minutes) VALUES (?, 'salespulse_history', ?, ?, ?, ?, ?)""",
                    (no, d, t, cust, vtime, mins))
        nu += cur.rowcount
    conn.commit()
    conn.close()
    return {"groups": len(groups), "settings": len([k for k in mapping if k in sp_settings]),
            "history_bills": nb, "history_unproductive": nu}


# ---------------------------------------------------------------------------
# Importing raw exports
# ---------------------------------------------------------------------------
def _resolve_missing_territories(cur, bills, date):
    """
    Legacy hand-merged sheets lost TERRITORY on sales orders. Recover it from
    the same sales rep's other records that day (SKU lines / bills), and
    record HOW it was resolved, so nothing is silently guessed.
    """
    rep_terr = defaultdict(set)
    for rep, terr in cur.execute(
            "SELECT rep, territory FROM sfa_sku_lines WHERE line_date=? AND territory!='' "
            "UNION SELECT rep, territory FROM sfa_bills WHERE bill_date=? AND territory!='' AND rep!=''",
            (date, date)):
        rep_terr[rep].add(terr)
    for b in bills:
        if b["territory"]:
            rep_terr[b["rep"]].add(b["territory"])
    unresolved = []
    for b in bills:
        if b["territory"]:
            continue
        cands = rep_terr.get(b["rep"], set())
        if len(cands) == 1:
            b["territory"] = next(iter(cands))
            b["territory_source"] = "resolved from sales rep"
        else:
            unresolved.append(b["bill_no"])
    return unresolved


def import_export_file(path):
    """
    Reads one raw export and stores it, replacing any earlier snapshot of
    the same dataset+date. Returns a status dict for the UI / run log.
    """
    export = X.read_export(path)
    ds = export["dataset"]
    warnings = list(export["warnings"])

    if ds in ("invoices", "sales_orders", "legacy_inv_pre"):
        rows, w = X.normalise_bills(export)
        warnings += w
        # Legacy merged sheets carry both kinds — store each under its own dataset.
        parts = defaultdict(list)
        for r in rows:
            target = ds if ds != "legacy_inv_pre" else ("invoices" if r["kind"] == "INV" else "sales_orders")
            parts[(target, r["bill_date"])].append(r)
    elif ds.startswith("sku"):
        rows, w = X.normalise_sku(export)
        warnings += w
        parts = defaultdict(list)
        for r in rows:
            parts[("sku_" + r["model"], r["line_date"])].append(r)
    elif ds == "unproductive":
        rows, w = X.normalise_unproductive(export)
        warnings += w
        parts = defaultdict(list)
        for r in rows:
            parts[("unproductive", r["visit_date"])].append(r)
    else:
        raise X.ExportError(f"Unsupported dataset '{ds}'.")

    if not parts:
        raise X.ExportError(f"{export['path']}: no data rows found (the export may be empty for that date).")

    conn = db.get_connection()
    cur = conn.cursor()
    stored = []
    try:
        for (target, date), recs in sorted(parts.items()):
            unresolved = []
            if target in ("invoices", "sales_orders"):
                unresolved = _resolve_missing_territories(cur, recs, date)
                kind = "INV" if target == "invoices" else "SO"
                cur.execute("DELETE FROM sfa_bills WHERE bill_date=? AND "
                            "(dataset=? OR (dataset='salespulse_history' AND kind=?))", (date, target, kind))
                cur.executemany(
                    """INSERT OR REPLACE INTO sfa_bills (bill_no, dataset, bill_date, kind, so_number, territory,
                       rep, route, customer, outlet_code, amount, created_time, minutes, status, territory_source)
                       VALUES (:bill_no, :dataset, :bill_date, :kind, :so_number, :territory, :rep, :route,
                       :customer, :outlet_code, :amount, :created_time, :minutes, :status, :territory_source)""",
                    [{**r, "dataset": target, "territory_source": r.get("territory_source")} for r in recs])
            elif target.startswith("sku_"):
                cur.execute("DELETE FROM sfa_sku_lines WHERE dataset=? AND line_date=?", (target, date))
                cur.executemany(
                    """INSERT INTO sfa_sku_lines (dataset, line_date, model, territory, asm, rep, outlet_code,
                       outlet, bill_no, product_code, product, units, value)
                       VALUES (:dataset, :line_date, :model, :territory, :asm, :rep, :outlet_code, :outlet,
                       :bill_no, :product_code, :product, :units, :value)""",
                    [{**r, "dataset": target} for r in recs])
            else:
                cur.execute("DELETE FROM sfa_unproductive WHERE dataset IN ('unproductive', 'salespulse_history') "
                            "AND visit_date=?", (date,))
                cur.executemany(
                    """INSERT OR REPLACE INTO sfa_unproductive (unproductive_no, dataset, visit_date, territory,
                       rep, rep_model, route, customer, reason, visit_time, minutes)
                       VALUES (:unproductive_no, 'unproductive', :visit_date, :territory, :rep, :rep_model,
                       :route, :customer, :reason, :visit_time, :minutes)""", recs)

            part_warn = list(warnings)
            if unresolved:
                part_warn.append(f"{len(unresolved)} bill(s) have no territory and couldn't be resolved: "
                                 + ", ".join(unresolved[:5]))
            resolved = sum(1 for r in recs if r.get("territory_source"))
            if resolved:
                part_warn.append(f"{resolved} bill(s) had a blank TERRITORY (hand-merged sheet); "
                                 "territory was recovered from the same sales rep's other records.")
            cur.execute(
                """INSERT OR REPLACE INTO sfa_datasets (dataset, data_date, rows, source_file, generated_at,
                   imported_at, filters, warnings) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (target, date, len(recs), export["path"], export["generated_at"],
                 datetime.now().isoformat(timespec="seconds"), json.dumps(export["banner"]),
                 json.dumps(part_warn)))
            stored.append({"dataset": target, "label": DATASET_LABELS.get(target, target),
                           "date": date, "rows": len(recs), "warnings": part_warn,
                           "generated_at": export["generated_at"]})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"file": export["path"], "detected": export["dataset"], "stored": stored}


def import_files(paths):
    """Bulk + single through one path: each file succeeds or fails on its own."""
    results = []
    for p in paths:
        try:
            r = import_export_file(p)
            results.append({"ok": True, **r})
        except (X.ExportError, ValueError) as e:
            results.append({"ok": False, "file": p, "message": str(e)})
        except Exception as e:  # unexpected — still isolate it to this file
            results.append({"ok": False, "file": p, "message": f"Unexpected error: {e}"})
    return results


def get_dataset_status(date):
    """Which of the 5 required datasets are loaded for `date`, and how fresh."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT dataset, rows, generated_at, imported_at, source_file, warnings FROM sfa_datasets WHERE data_date=?",
        (date,)).fetchall()
    hist = conn.execute("SELECT COUNT(*) FROM sfa_bills WHERE bill_date=? AND dataset='salespulse_history'",
                        (date,)).fetchone()[0]
    conn.close()
    have = {r[0]: r for r in rows}
    out = []
    for key in ("invoices", "sales_orders", "sku_van", "sku_pre", "unproductive"):
        r = have.get(key)
        out.append({
            "dataset": key, "label": DATASET_LABELS[key], "loaded": bool(r),
            "rows": r[1] if r else 0, "generated_at": r[2] if r else "",
            "imported_at": r[3] if r else "", "source_file": r[4] if r else "",
            "warnings": json.loads(r[5]) if r and r[5] else [],
        })
    return {"date": date, "datasets": out, "history_bills": hist}
