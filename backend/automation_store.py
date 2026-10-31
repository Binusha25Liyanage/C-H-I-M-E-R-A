"""
automation_store.py
Storage for the SFA download automation:

  * Report profiles — one per SFA report Chimera downloads: its URL, which
    Chimera input it feeds, the filters to fill, and the Search / Export
    button texts. Users can add, edit, duplicate, reorder and delete them,
    so a new report never needs new code.
  * Automation settings — run times, weekdays, pause / skip-today,
    parallel tabs, retries, what to generate afterwards.
  * Automation run history (per-tab outcome of every run).
  * A cross-process run lock, so a scheduled run and a manual run (or the
    app and Windows Task Scheduler) can never download at the same time.

Filter definition (stored as JSON on the profile):
    {"label": "Report Type",            # exactly as shown on the SFA page
     "type": "dropdown" | "multiselect" | "date" | "text" | "toggle",
     "value": "Van Selling",            # list for multiselect, "on"/"off" for toggle
     "date_mode": "report_date" | "today" | "yesterday" | "fixed",   # date only
     "required": true}
"""

import json
import os
import re
import time
from datetime import date, datetime, timedelta

import db

FEEDS = {
    "invoices": "Invoices (Van Selling)",
    "sales_orders": "Sales Orders (Pending)",
    "sku_van": "Outlet Wise SKU — Van Selling",
    "sku_pre": "Outlet Wise SKU — Pre Sales Order (Pending)",
    "unproductive": "Unproductive Visits",
    "download_only": "Download only (not used in reports)",
}
FILTER_TYPES = ("dropdown", "multiselect", "date", "text", "toggle")
MAX_PARALLEL_TABS = 12   # each tab is a full page of SFA; above ~8 needs a capable PC and SFA server
DATE_MODES = ("report_date", "today", "yesterday", "fixed")

DEFAULT_AUTOMATION = {
    "paused": "1",                       # start paused until the user has checked the profiles
    "run_times": "10:00,12:00,15:00,17:30",
    "weekdays": "1111110",               # Mon..Sun
    "skip_date": "",                     # 'YYYY-MM-DD' = skip scheduled runs that day
    "parallel_tabs": "5",
    "retries": "2",
    "headless": "1",                     # hide the browser during scheduled runs
    "generate_kinds": "sku,pivot,hourly",
    "use_task_scheduler": "0",
    "page_timeout_seconds": "60",
    "wa_auto_send": "0",                 # send to WhatsApp automatically once reports are saved
    "wa_show_browser": "1",              # show the WhatsApp Web window while sending
    "wa_style": "classic",               # which output style's images to send when 'both' are built
    "wa_chats_cache": "[]",              # chat names last read from WhatsApp Web
    "notif_seen_at": "",                 # notifications newer than this are unread
    "data_saver": "0",                   # don't load images/fonts/media during automated SFA runs
    "auto_sfa_downloads": "1",           # per-process switches (Schedules > Automations)
    "auto_build_reports": "1",
    "auto_master_sync": "1",
    "auto_visit_state": "1",
    "auto_sequence": "1",
    "auto_loadings": "1",                # Distributor Loadings: prepare on schedule (never confirms by itself)
    "loading_url": "https://lakmee.salespro.lk/it/loading",   # SFA View Loadings page
    "loading_tabs": "12",
    "clock_offset_s": "",                # SFA server time minus PC time, seconds (last check)
    "clock_source": "",
    "clock_checked_at": "",
}

LOCK_PATH = os.path.join(db.APP_DATA_DIR, "automation.lock")
LOCK_STALE_SECONDS = 2 * 60 * 60


def _date_filters():
    return [{"label": "Date From", "type": "date", "value": "", "date_mode": "report_date", "required": True},
            {"label": "Date To", "type": "date", "value": "", "date_mode": "report_date", "required": True}]


SEED_PROFILES = [
    {"name": "Invoices - Van Selling", "feeds_into": "invoices", "url": "",
     "filters": [{"label": "Invoice Type", "type": "dropdown", "value": "Van selling Invoice",
                  "date_mode": "", "required": True}] + _date_filters()},
    {"name": "Sales Orders - Pending", "feeds_into": "sales_orders", "url": "",
     "filters": [{"label": "Invoice Status", "type": "dropdown", "value": "Pending",
                  "date_mode": "", "required": True}] + _date_filters()},
    {"name": "Outlet Wise SKU - Van Selling", "feeds_into": "sku_van",
     "url": "https://lakmee.salespro.lk/it/reports/outlet_wise_sku_sales",
     "filters": [{"label": "Report Type", "type": "dropdown", "value": "Van Selling",
                  "date_mode": "", "required": True}] + _date_filters()},
    {"name": "Outlet Wise SKU - Pre Sales Order Pending", "feeds_into": "sku_pre",
     "url": "https://lakmee.salespro.lk/it/reports/outlet_wise_sku_sales",
     "filters": [{"label": "Report Type", "type": "dropdown", "value": "Pre Sales Order - Pending Only",
                  "date_mode": "", "required": True}] + _date_filters()},
    {"name": "Unproductive Visits", "feeds_into": "unproductive", "url": "", "filters": _date_filters()},
]


def init_schema():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sfa_profiles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            url          TEXT NOT NULL DEFAULT '',
            feeds_into   TEXT NOT NULL DEFAULT 'download_only',
            enabled      INTEGER NOT NULL DEFAULT 1,
            position     INTEGER NOT NULL DEFAULT 0,
            filters      TEXT NOT NULL DEFAULT '[]',
            search_text  TEXT NOT NULL DEFAULT 'SEARCH',
            export_text  TEXT NOT NULL DEFAULT 'Export',
            wait_seconds INTEGER NOT NULL DEFAULT 60,
            allow_empty  INTEGER NOT NULL DEFAULT 1,
            description  TEXT NOT NULL DEFAULT '',
            updated_at   TEXT
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS automation_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS automation_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            report_date   TEXT NOT NULL,
            trigger       TEXT NOT NULL,      -- manual | scheduled | test | cli
            slot          TEXT,               -- 'HH:MM' for scheduled runs
            status        TEXT NOT NULL,      -- running | success | partial | failed | cancelled
            lanes         TEXT,               -- JSON per-tab outcome
            report_run_id INTEGER,
            message       TEXT
        )""")
    cols = {r[1] for r in cur.execute("PRAGMA table_info(automation_runs)")}
    if "data_bytes" not in cols:
        cur.execute("ALTER TABLE automation_runs ADD COLUMN data_bytes INTEGER")
    if "blocked" not in cols:
        cur.execute("ALTER TABLE automation_runs ADD COLUMN blocked INTEGER")
    if "data_saver" not in cols:
        cur.execute("ALTER TABLE automation_runs ADD COLUMN data_saver INTEGER")
    if "log" not in cols:
        cur.execute("ALTER TABLE automation_runs ADD COLUMN log TEXT")
    for k, v in DEFAULT_AUTOMATION.items():
        cur.execute("INSERT OR IGNORE INTO automation_settings (key, value) VALUES (?, ?)", (k, v))
    if cur.execute("SELECT COUNT(*) FROM sfa_profiles").fetchone()[0] == 0:
        for i, p in enumerate(SEED_PROFILES):
            cur.execute("""INSERT INTO sfa_profiles (name, url, feeds_into, enabled, position, filters,
                           description, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (p["name"], p["url"], p["feeds_into"], 1, i, json.dumps(p["filters"]),
                         "Starter profile — check the URL and use 'Detect filters' to confirm the labels.",
                         datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
_COLS = ["id", "name", "url", "feeds_into", "enabled", "position", "filters", "search_text",
         "export_text", "wait_seconds", "allow_empty", "description", "updated_at"]


def _row_to_profile(r):
    p = dict(zip(_COLS, r))
    p["filters"] = json.loads(p["filters"] or "[]")
    p["enabled"] = bool(p["enabled"])
    p["allow_empty"] = bool(p["allow_empty"])
    p["feeds_label"] = FEEDS.get(p["feeds_into"], p["feeds_into"])
    p["problems"] = profile_problems(p)
    return p


def list_profiles(enabled_only=False):
    conn = db.get_connection()
    rows = conn.execute(f"SELECT {', '.join(_COLS)} FROM sfa_profiles "
                        + ("WHERE enabled=1 " if enabled_only else "") + "ORDER BY position, id").fetchall()
    conn.close()
    return [_row_to_profile(r) for r in rows]


def get_profile(pid):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_COLS)} FROM sfa_profiles WHERE id=?", (pid,)).fetchone()
    conn.close()
    return _row_to_profile(r) if r else None


def profile_problems(p):
    """Things that would stop a profile from running — shown in the UI, checked before runs."""
    out = []
    if not p.get("url"):
        out.append("Navigation URL is not set.")
    elif not re.match(r"^https?://", p["url"]):
        out.append("Navigation URL must start with http:// or https://")
    for f in p.get("filters", []):
        if f.get("required") and f.get("type") != "date" and f.get("value") in ("", None, []):
            out.append(f"Filter '{f.get('label')}' is required but has no value.")
        if f.get("type") == "date" and f.get("date_mode") == "fixed" and not re.match(
                r"^\d{4}-\d{2}-\d{2}$", str(f.get("value") or "")):
            out.append(f"Filter '{f.get('label')}' uses a fixed date but none is set.")
    return out


def _clean_profile(data):
    """Validates user input. Returns (clean_dict, error_message)."""
    name = str(data.get("name", "")).strip()
    if not name:
        return None, "Report name is required."
    url = str(data.get("url", "")).strip()
    if url and not re.match(r"^https?://", url):
        return None, "Navigation URL must start with http:// or https://"
    feeds = data.get("feeds_into", "download_only")
    if feeds not in FEEDS:
        return None, "Choose what this report feeds into."
    filters, seen = [], set()
    for i, f in enumerate(data.get("filters") or [], start=1):
        label = str(f.get("label", "")).strip()
        if not label:
            return None, f"Filter row {i} has no label."
        if label.lower() in seen:
            return None, f"Filter '{label}' is listed twice."
        seen.add(label.lower())
        ftype = f.get("type", "dropdown")
        if ftype not in FILTER_TYPES:
            return None, f"Filter '{label}' has an unknown type."
        value = f.get("value", "")
        if ftype == "multiselect":
            if isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            value = [str(v).strip() for v in value if str(v).strip()]
        elif ftype == "toggle":
            value = "on" if str(value).lower() in ("on", "true", "1", "yes") else "off"
        else:
            value = str(value or "").strip()
        mode = f.get("date_mode") or ("report_date" if ftype == "date" else "")
        if ftype == "date" and mode not in DATE_MODES:
            return None, f"Filter '{label}' has an unknown date option."
        if ftype == "date" and mode == "fixed" and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return None, f"Filter '{label}': pick the fixed date."
        options = [str(o)[:120] for o in (f.get("options") or []) if str(o).strip()][:200]
        filters.append({"label": label, "type": ftype, "value": value,
                        "date_mode": mode if ftype == "date" else "", "required": bool(f.get("required")),
                        "options": options})
    try:
        wait = int(data.get("wait_seconds", 60))
    except (TypeError, ValueError):
        return None, "Wait time must be a whole number of seconds."
    if not 5 <= wait <= 600:
        return None, "Wait time must be between 5 and 600 seconds."
    search = str(data.get("search_text", "SEARCH")).strip() or "SEARCH"
    export = str(data.get("export_text", "Export")).strip() or "Export"
    return {"name": name, "url": url, "feeds_into": feeds, "enabled": 1 if data.get("enabled", True) else 0,
            "filters": json.dumps(filters), "search_text": search, "export_text": export,
            "wait_seconds": wait, "allow_empty": 1 if data.get("allow_empty", True) else 0,
            "description": str(data.get("description", "")).strip()}, None


def save_profile(data):
    """Create (no id) or update (with id). Returns (ok, message, profile)."""
    clean, err = _clean_profile(data)
    if err:
        return False, err, None
    clean["updated_at"] = datetime.now().isoformat(timespec="seconds")
    conn = db.get_connection()
    try:
        pid = data.get("id")
        if pid:
            if not conn.execute("SELECT 1 FROM sfa_profiles WHERE id=?", (pid,)).fetchone():
                return False, "That profile no longer exists.", None
            sets = ", ".join(f"{k}=:{k}" for k in clean)
            conn.execute(f"UPDATE sfa_profiles SET {sets} WHERE id=:id", {**clean, "id": pid})
        else:
            pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM sfa_profiles").fetchone()[0]
            cols = list(clean) + ["position"]
            cur = conn.execute(f"INSERT INTO sfa_profiles ({', '.join(cols)}) VALUES "
                               f"({', '.join(':' + c for c in cols)})", {**clean, "position": pos})
            pid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return True, "Profile saved.", get_profile(pid)


def duplicate_profile(pid):
    p = get_profile(pid)
    if not p:
        return False, "That profile no longer exists.", None
    p = {**p, "id": None, "name": p["name"] + " (copy)", "enabled": False}
    return save_profile(p)


def delete_profile(pid):
    conn = db.get_connection()
    n = conn.execute("DELETE FROM sfa_profiles WHERE id=?", (pid,)).rowcount
    conn.commit()
    conn.close()
    return n > 0


def set_profile_enabled(pid, enabled):
    conn = db.get_connection()
    conn.execute("UPDATE sfa_profiles SET enabled=? WHERE id=?", (1 if enabled else 0, pid))
    conn.commit()
    conn.close()


def reorder_profiles(ids):
    conn = db.get_connection()
    conn.executemany("UPDATE sfa_profiles SET position=? WHERE id=?", [(i, pid) for i, pid in enumerate(ids)])
    conn.commit()
    conn.close()


def resolve_date(f, report_date):
    """The actual YYYY-MM-DD a date filter should get for this run."""
    mode = f.get("date_mode") or "report_date"
    rd = date.fromisoformat(report_date)
    if mode == "today":
        return date.today().isoformat()
    if mode == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    if mode == "fixed":
        return f.get("value") or report_date
    return rd.isoformat()


def expected_banner_checks(profile, report_date):
    """
    What the downloaded file's banner must say, derived from the profile's
    own filters: e.g. 'Report Type' = 'Van Selling', dates = report date.
    """
    checks = []
    for f in profile["filters"]:
        if f["type"] in ("dropdown", "text") and f["value"]:
            checks.append({"key": f["label"], "expect": f["value"]})
        if f["type"] == "date":
            checks.append({"key": f["label"], "expect": resolve_date(f, report_date), "is_date": True})
    return checks


# ---------------------------------------------------------------------------
# Automation settings
# ---------------------------------------------------------------------------
def get_automation_settings():
    conn = db.get_connection()
    rows = dict(conn.execute("SELECT key, value FROM automation_settings").fetchall())
    conn.close()
    s = {**DEFAULT_AUTOMATION, **rows}
    s["run_times_list"] = [t for t in s["run_times"].split(",") if t]
    s["generate_list"] = [k for k in s["generate_kinds"].split(",") if k]
    return s


def save_automation_settings(values):
    clean = {}
    for k, v in (values or {}).items():
        if k not in DEFAULT_AUTOMATION:
            continue
        v = str(v).strip() if not isinstance(v, list) else v
        if k == "run_times":
            items = v if isinstance(v, list) else [x for x in v.split(",") if x.strip()]
            times = set()
            for t in items:
                t = str(t).strip()
                m = re.match(r"^(\d{1,2}):(\d{2})$", t)
                if not m or not (0 <= int(m[1]) < 24 and 0 <= int(m[2]) < 60):
                    return False, f"'{t}' is not a valid time (use HH:MM, 24-hour)."
                times.add(f"{int(m[1]):02d}:{m[2]}")
            v = ",".join(sorted(times))
        elif k == "weekdays":
            if not re.match(r"^[01]{7}$", v):
                return False, "Weekdays must be 7 on/off values."
        elif k == "skip_date":
            if v and not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                return False, "Skip date must be YYYY-MM-DD."
        elif k == "clock_offset_s":
            if v and not re.match(r"^-?\d+$", v):
                return False, "Clock offset must be whole seconds."
        elif k == "wa_style":
            if v not in ("classic", "professional"):
                return False, "WhatsApp style must be classic or professional."
        elif k in ("wa_chats_cache", "notif_seen_at", "clock_source", "clock_checked_at"):
            pass
        elif k in ("paused", "headless", "use_task_scheduler", "wa_auto_send", "wa_show_browser", "data_saver",
                   "auto_sfa_downloads", "auto_build_reports", "auto_master_sync", "auto_visit_state", "auto_sequence",
                   "auto_loadings"):
            v = "1" if v.lower() in ("1", "true", "on", "yes") else "0"
        elif k == "loading_url":
            if not re.match(r"^https?://", v):
                return False, "The View Loadings URL must start with http:// or https://"
        elif k == "loading_tabs":
            if not v.isdigit() or not 1 <= int(v) <= MAX_PARALLEL_TABS:
                return False, f"Loading tabs must be between 1 and {MAX_PARALLEL_TABS}."
        elif k == "parallel_tabs":
            if not v.isdigit() or not 1 <= int(v) <= MAX_PARALLEL_TABS:
                return False, f"Parallel tabs must be between 1 and {MAX_PARALLEL_TABS}."
        elif k == "retries":
            if not v.isdigit() or not 0 <= int(v) <= 5:
                return False, "Retries must be between 0 and 5."
        elif k == "page_timeout_seconds":
            if not v.isdigit() or not 15 <= int(v) <= 600:
                return False, "Page timeout must be between 15 and 600 seconds."
        elif k == "generate_kinds":
            items = v if isinstance(v, list) else [x for x in v.split(",") if x]
            bad = [x for x in items if x not in ("sku", "pivot", "hourly")]
            if bad:
                return False, f"Unknown report(s): {', '.join(bad)}"
            v = ",".join(items)
        clean[k] = v
    conn = db.get_connection()
    conn.executemany("INSERT OR REPLACE INTO automation_settings (key, value) VALUES (?, ?)", clean.items())
    conn.commit()
    conn.close()
    return True, "Saved."


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------
def start_run_record(report_date, trigger, slot=None):
    conn = db.get_connection()
    cur = conn.execute("""INSERT INTO automation_runs (started_at, report_date, trigger, slot, status)
                          VALUES (?, ?, ?, ?, 'running')""",
                       (datetime.now().isoformat(timespec="seconds"), report_date, trigger, slot))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def finish_run_record(rid, status, lanes, report_run_id=None, message="", data_bytes=None, blocked=None,
                      data_saver=None):
    conn = db.get_connection()
    conn.execute("""UPDATE automation_runs SET finished_at=?, status=?, lanes=?, report_run_id=?, message=?,
                    data_bytes=?, blocked=?, data_saver=? WHERE id=?""",
                 (datetime.now().isoformat(timespec="seconds"), status, json.dumps(lanes), report_run_id,
                  message, data_bytes, blocked, None if data_saver is None else int(bool(data_saver)), rid))
    conn.commit()
    conn.close()


def list_runs(limit=20):
    conn = db.get_connection()
    rows = conn.execute("""SELECT id, started_at, finished_at, report_date, trigger, slot, status, lanes,
                           report_run_id, message, data_bytes, blocked, data_saver
                           FROM automation_runs ORDER BY id DESC LIMIT ?""",
                        (limit,)).fetchall()
    conn.close()
    keys = ["id", "started_at", "finished_at", "report_date", "trigger", "slot", "status", "lanes",
            "report_run_id", "message", "data_bytes", "blocked", "data_saver"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["lanes"] = json.loads(d["lanes"]) if d["lanes"] else []
        out.append(d)
    return out


def save_run_log(rid, text):
    """Keeps the run's log with its history entry (so it can be copied later)."""
    conn = db.get_connection()
    conn.execute("UPDATE automation_runs SET log=? WHERE id=?", (text[-200_000:], rid))
    conn.commit()
    conn.close()


def get_run_log(rid):
    conn = db.get_connection()
    r = conn.execute("SELECT log FROM automation_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    return (r[0] if r else None) or ""


def slot_already_ran(report_date, slot):
    conn = db.get_connection()
    n = conn.execute("""SELECT COUNT(*) FROM automation_runs WHERE report_date=? AND slot=?
                        AND trigger='scheduled'""", (report_date, slot)).fetchone()[0]
    conn.close()
    return n > 0


def mark_interrupted_runs():
    """Runs left 'running' by a crash/closed app are marked so the history stays honest."""
    conn = db.get_connection()
    conn.execute("""UPDATE automation_runs SET status='interrupted', finished_at=?,
                    message='Chimera was closed or stopped before this run finished.'
                    WHERE status='running'""", (datetime.now().isoformat(timespec="seconds"),))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Cross-process lock
# ---------------------------------------------------------------------------
def acquire_lock(owner):
    """Returns True if this process now holds the automation lock."""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                json.dump({"owner": owner, "pid": os.getpid(), "since": time.time()}, f)
            return True
        except FileExistsError:
            try:
                with open(LOCK_PATH) as f:
                    info = json.load(f)
                stale = (time.time() - float(info.get("since", 0)) > LOCK_STALE_SECONDS
                         or not pid_alive(int(info.get("pid", 0))))     # Chimera was closed mid-run
            except Exception:
                stale = True
            if not stale:
                return False
            try:
                os.remove(LOCK_PATH)
            except OSError:
                return False
    return False


def pid_alive(pid):
    """Is that process still running? (Windows-safe: never sends a signal there.)"""
    if not pid or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.GetLastError() == 5          # access denied = exists but not ours
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259                   # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def lock_holder():
    try:
        with open(LOCK_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def release_lock(only_if_mine=False):
    if only_if_mine:
        h = lock_holder()
        if h and h.get("pid") != os.getpid():
            return
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def data_usage_summary(today=None):
    """Internet data used by SFA automation runs (measured by Chimera), in bytes."""
    today = today or date.today()
    week_ago = (today - timedelta(days=6)).isoformat()
    conn = db.get_connection()
    last = conn.execute("""SELECT data_bytes, started_at, data_saver FROM automation_runs
                           WHERE data_bytes IS NOT NULL ORDER BY id DESC LIMIT 1""").fetchone()
    day = conn.execute("""SELECT COALESCE(SUM(data_bytes), 0), COUNT(data_bytes) FROM automation_runs
                          WHERE data_bytes IS NOT NULL AND substr(started_at, 1, 10) = ?""",
                       (today.isoformat(),)).fetchone()
    week = conn.execute("""SELECT COALESCE(SUM(data_bytes), 0), COUNT(data_bytes) FROM automation_runs
                           WHERE data_bytes IS NOT NULL AND substr(started_at, 1, 10) >= ?""",
                        (week_ago,)).fetchone()
    avg_on = conn.execute("""SELECT AVG(data_bytes) FROM automation_runs WHERE data_bytes IS NOT NULL
                             AND data_saver = 1""").fetchone()[0]
    avg_off = conn.execute("""SELECT AVG(data_bytes) FROM automation_runs WHERE data_bytes IS NOT NULL
                              AND data_saver = 0""").fetchone()[0]
    conn.close()
    return {"last_bytes": last[0] if last else None, "last_at": last[1] if last else None,
            "today_bytes": day[0], "today_runs": day[1], "week_bytes": week[0], "week_runs": week[1],
            "avg_with_saver": avg_on, "avg_without_saver": avg_off}
