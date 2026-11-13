"""
rep_attendance.py
Rep Attendance: SFA's REP ATTENDANCE REPORT (/it/repattendancereport2)
-> who logged in / out each day -> two images (not logged out yet / logged
out) in two looks, plus a PDF, and a searchable history.

The export (one row per rep who logged in):
    row 8  groups:  DETAILS | ATTENDANCE | TOUR START | SELFI | TEAM | CREW | ASM ATTENDANCE | SALES | BILLS
    row 9  columns: REGION, AREA, SA, TERRITORY, SALES REP CODE, SALES REP, SALES REP PHONE NUMBER,
                    REP TYPE, … LOGGED IN DATE, LOGGED IN, …, LOGGED OUT DATE, LOGGED OUT, … ASM …
The rep's own LOGGED IN / LOGGED OUT are the ones under ATTENDANCE — the
second pair (under ASM ATTENDANCE) is the ASM's and is never used for reps.

Looks:
    classic       the structure from Lakmee Muster: maroon "LOGGED IN DATE MM/DD/YYYY" title,
                  TERRITORY | LOGGED IN | LOGGED OUT, cream zebra rows — one image per group
    professional  Chimera layout: logo header, KPI strip, Territory · Sales Rep · Type · times,
                  and on the "not logged out" image the working territories with no login

Your corrections (a time, a name) are kept when the day is read again — unless SFA's
own value changed since (e.g. the rep logged out later), then SFA's new value wins.
"""

import json
import os
import re
from datetime import date, datetime

import db

DEFAULT_URL = "https://lakmee.salespro.lk/it/repattendancereport2"
STYLES = ("classic", "professional")
GROUPS = ("not_logged_out", "logged_out")
GROUP_TITLES = {"not_logged_out": "Not logged out yet", "logged_out": "Logged out"}
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


class ReadError(Exception):
    pass


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _s(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "strftime"):                      # datetime.time
        return v.strftime("%H:%M:%S")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def squash(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def norm_time(t):
    """'8:5' / '08:05' / '8.05' / '08:05:09' -> 'HH:MM:SS'; '' stays ''; None if not a time."""
    t = str(t or "").strip().replace(".", ":")
    if not t or t in ("-", "–"):
        return ""
    m = TIME_RE.match(t)
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if h > 23 or mi > 59 or se > 59:
        return None
    return f"{h:02d}:{mi:02d}:{se:02d}"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
_ROW_COLS = ["day", "territory", "rep_code", "rep", "rep_type", "phone", "region", "area", "sa", "asm", "distributor",
             "in_time", "out_date", "out_time", "location", "orig_rep", "orig_in", "orig_out", "edited", "updated_at"]


def init_schema():
    conn = db.get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS att_days (
            day          TEXT PRIMARY KEY,
            source       TEXT NOT NULL,          -- sfa | file
            file_name    TEXT NOT NULL DEFAULT '',
            file_path    TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL DEFAULT '', -- 'Generated at' in the export
            read_at      TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS att_rows (
            day         TEXT NOT NULL,
            territory   TEXT NOT NULL,
            rep_code    TEXT NOT NULL DEFAULT '',
            rep         TEXT NOT NULL DEFAULT '',
            rep_type    TEXT NOT NULL DEFAULT '',
            phone       TEXT NOT NULL DEFAULT '',
            region      TEXT NOT NULL DEFAULT '',
            area        TEXT NOT NULL DEFAULT '',
            sa          TEXT NOT NULL DEFAULT '',
            asm         TEXT NOT NULL DEFAULT '',
            distributor TEXT NOT NULL DEFAULT '',
            in_time     TEXT NOT NULL DEFAULT '',
            out_date    TEXT NOT NULL DEFAULT '',
            out_time    TEXT NOT NULL DEFAULT '',
            location    TEXT NOT NULL DEFAULT '',
            orig_rep    TEXT NOT NULL DEFAULT '',  -- SFA's values, to tell your corrections from SFA's changes
            orig_in     TEXT NOT NULL DEFAULT '',
            orig_out    TEXT NOT NULL DEFAULT '',
            edited      INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (day, territory)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS att_rows_day ON att_rows(day)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS att_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            day         TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            trigger     TEXT NOT NULL DEFAULT 'manual',
            slot        TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'running',   -- running | done | failed | cancelled | busy
            state       TEXT NOT NULL DEFAULT '',          -- built | pending_review | sent | partly_sent | send_failed | not_sent
            style       TEXT NOT NULL DEFAULT '',
            images      TEXT NOT NULL DEFAULT '[]',        -- [{style, group, path}]
            pdf         TEXT NOT NULL DEFAULT '',
            counts      TEXT NOT NULL DEFAULT '{}',
            message     TEXT NOT NULL DEFAULT '',
            log         TEXT NOT NULL DEFAULT '',
            sent_at     TEXT
        )""")
    if "left_out" not in {r[1] for r in conn.execute("PRAGMA table_info(att_runs)")}:   # rows unticked for this send
        conn.execute("ALTER TABLE att_runs ADD COLUMN left_out TEXT NOT NULL DEFAULT '[]'")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Reading SFA's export
# ---------------------------------------------------------------------------
def read_excel(path):
    """-> {day, generated_at, rows: [...]}; raises ReadError if it isn't a RepAttendance export."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as e:
        raise ReadError(f"That file isn't an Excel workbook Chimera can open ({type(e).__name__}).")
    ws = wb.active
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    hi = None
    for i, r in enumerate(grid[:40]):
        names = [_s(c).upper() for c in r]
        if "TERRITORY" in names and "LOGGED IN" in names:
            hi = i
            break
    if hi is None:
        raise ReadError("This doesn't look like SFA's Rep Attendance export — no TERRITORY / LOGGED IN header row.")
    head = [_s(c).upper() for c in grid[hi]]
    group, cur = [], ""
    above = grid[hi - 1] if hi > 0 else []
    for k in range(len(head)):
        g = _s(above[k]).upper() if k < len(above) else ""
        cur = g or cur
        group.append(cur)

    def col(name, in_group=None, first=True):
        hits = [k for k, h in enumerate(head) if h == name and (in_group is None or group[k] == in_group)]
        return (hits[0] if first else hits[-1]) if hits else None

    att = "ATTENDANCE" if "ATTENDANCE" in group else None
    c = {"territory": col("TERRITORY"), "rep_code": col("SALES REP CODE"), "rep": col("SALES REP"),
         "phone": col("SALES REP PHONE NUMBER"), "rep_type": col("REP TYPE"), "region": col("REGION"),
         "area": col("AREA"), "sa": col("SA"), "asm": col("ASM", "TEAM") if "TEAM" in group else col("ASM"),
         "distributor": col("DISTRIBUTOR"), "in_date": col("LOGGED IN DATE", att), "in_time": col("LOGGED IN", att),
         "out_date": col("LOGGED OUT DATE", att), "out_time": col("LOGGED OUT", att),
         "location": col("LOGGED IN LOCATION", att)}
    meta = {}
    for r in grid[:hi]:
        cells = [_s(x) for x in r]
        for k in range(len(cells) - 1):
            if cells[k] in ("Date From", "Date To", "Generated at"):
                meta[cells[k]] = cells[k + 1]
    rows, days = [], set()
    for r in grid[hi + 1:]:
        get = lambda key: _s(r[c[key]]) if c.get(key) is not None and c[key] < len(r) else ""
        terr = get("territory")
        if not terr:
            continue
        tin = norm_time(get("in_time"))
        tout = norm_time(get("out_time"))
        if get("in_date"):
            days.add(get("in_date")[:10])
        rows.append({"territory": terr, "rep_code": get("rep_code"), "rep": get("rep"), "rep_type": get("rep_type"),
                     "phone": get("phone"), "region": get("region"), "area": get("area"), "sa": get("sa"),
                     "asm": get("asm"), "distributor": get("distributor"),
                     "in_time": tin if tin is not None else get("in_time"),
                     "out_date": get("out_date")[:10], "out_time": tout if tout is not None else get("out_time"),
                     "location": get("location")})
    day = (meta.get("Date From") or "")[:10] or (sorted(days)[0] if days else "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise ReadError("Couldn't tell which day this export is for (no 'Date From' or LOGGED IN DATE).")
    if meta.get("Date To") and meta["Date To"][:10] != day:
        raise ReadError(f"This export covers {day} to {meta['Date To'][:10]} — export one day at a time.")
    return {"day": day, "generated_at": meta.get("Generated at", ""), "rows": rows}


def save_day(parsed, source, file_path=""):
    """Stores a day's export. Keeps your corrections unless SFA's own value changed since."""
    day = parsed["day"]
    now = _now()
    conn = db.get_connection()
    old = {r[0]: dict(zip(_ROW_COLS[1:], r)) for r in conn.execute(
        f"SELECT {', '.join(_ROW_COLS[1:])} FROM att_rows WHERE day=?", (day,))}
    old = {squash(k): v for k, v in old.items()}
    kept, seen = 0, set()
    for r in parsed["rows"]:
        key = squash(r["territory"])
        if key in seen:
            continue
        seen.add(key)
        vals = dict(r)
        vals.update(orig_rep=r["rep"], orig_in=r["in_time"], orig_out=r["out_time"], edited=0)
        o = old.get(key)
        if o and o["edited"]:
            for f, of in (("rep", "orig_rep"), ("in_time", "orig_in"), ("out_time", "orig_out")):
                if o[of] == r[f] and o[f] != r[f]:       # SFA still says the same: your correction stands
                    vals[f] = o[f]
                    vals["edited"] = 1
            kept += vals["edited"]
        conn.execute(f"""INSERT OR REPLACE INTO att_rows ({', '.join(_ROW_COLS)}) VALUES ({','.join('?' * len(_ROW_COLS))})""",
                     tuple([day] + [vals.get(k, "") for k in _ROW_COLS[1:-1]] + [now]))
    gone = [k for k in old if k not in seen]
    for k in gone:                                     # no longer in SFA's export for that day
        conn.execute("DELETE FROM att_rows WHERE day=? AND territory=?", (day, old[k]["territory"]))
    conn.execute("INSERT OR REPLACE INTO att_days (day, source, file_name, file_path, generated_at, read_at) "
                 "VALUES (?,?,?,?,?,?)", (day, source, os.path.basename(file_path), file_path,
                                          parsed.get("generated_at", ""), now))
    conn.commit()
    conn.close()
    return {"day": day, "reps": len(seen), "kept_corrections": kept}


def always_left_out():
    """Territories you never want on the images (test territories etc.) — Rep Attendance › Settings."""
    try:
        import automation_store as AS
        return [str(x) for x in json.loads(AS.get_automation_settings().get("att_exclude") or "[]") if str(x).strip()]
    except Exception:
        return []


def get_day(day, skip=()):
    """The day as it goes on the images. Rows on the always-leave-out list (and `skip`, the rows you unticked
    when reviewing a send) are moved to 'left_out' and don't count anywhere."""
    conn = db.get_connection()
    rows = [dict(zip(_ROW_COLS, r)) for r in conn.execute(
        f"SELECT {', '.join(_ROW_COLS)} FROM att_rows WHERE day=? ORDER BY CASE WHEN in_time='' THEN 1 ELSE 0 END, "
        "in_time, territory", (day,))]
    meta = conn.execute("SELECT source, file_name, generated_at, read_at FROM att_days WHERE day=?", (day,)).fetchone()
    conn.close()
    always = {squash(t) for t in always_left_out()}
    skipped = {squash(t) for t in skip or ()}
    kept, left = [], []
    for r in rows:
        r["edited"] = bool(r["edited"])
        r["group"] = "logged_out" if r["out_time"] else "not_logged_out"
        k = squash(r["territory"])
        r["left_out"] = "always" if k in always else "this_send" if k in skipped else ""
        (left if r["left_out"] else kept).append(r)
    working = [t for t in working_territories() if squash(t) not in always]
    have = {squash(r["territory"]) for r in rows}
    missing = [t for t in working if squash(t) not in have]
    lo = [r for r in kept if r["group"] == "logged_out"]
    return {"day": day, "rows": kept, "left_out": left,
            "meta": dict(zip(["source", "file_name", "generated_at", "read_at"], meta)) if meta else None,
            "not_logged_in": missing, "working_count": len(working),
            "counts": {"logged_in": len(kept), "not_logged_out": len(kept) - len(lo), "logged_out": len(lo),
                       "not_logged_in": len(missing), "working": len(working), "edited": sum(r["edited"] for r in kept),
                       "left_out": len(left)}}


def last_seen(territories, day):
    """{territory: {asm, rep, rep_type}} from the most recent earlier day each territory logged in — for
    the territories that didn't log in today (SFA's export only lists those who did)."""
    if not territories:
        return {}
    want = {squash(t): t for t in territories}
    conn = db.get_connection()
    rows = conn.execute("SELECT territory, asm, rep, rep_type FROM att_rows WHERE day<=? ORDER BY day DESC", (day,)).fetchall()
    conn.close()
    out = {}
    for terr, asm, rep, typ in rows:
        k = squash(terr)
        if k in want and want[k] not in out and (asm or "").strip():
            out[want[k]] = {"asm": asm.strip(), "rep": rep, "rep_type": typ}
    return out


def all_territories():
    """Every territory Chimera knows — Outlet Updates' list (working ones flagged) plus any seen in rep
    attendance — for the leave-out picker. [{territory, working}] working first, then A-Z."""
    out = {}
    try:
        import outlet_store
        for t in outlet_store.list_territories():
            out[squash(t["territory"])] = {"territory": t["territory"], "working": bool(t["enabled"])}
    except Exception:
        pass
    conn = db.get_connection()
    for (terr,) in conn.execute("SELECT DISTINCT territory FROM att_rows"):
        out.setdefault(squash(terr), {"territory": terr, "working": False})
    conn.close()
    return sorted(out.values(), key=lambda t: (not t["working"], t["territory"].lower()))


def set_always_left_out(territory, on):
    """Adds / removes a territory on the always-leave-out list."""
    import automation_store as AS
    cur = always_left_out()
    keys = {squash(t) for t in cur}
    if on and squash(territory) not in keys:
        cur.append(str(territory).strip())
    if not on:
        cur = [t for t in cur if squash(t) != squash(territory)]
    return AS.save_automation_settings({"att_exclude": cur})


def working_territories():
    """Territories switched on in Outlet Updates › Working Territories."""
    try:
        import outlet_store
        return [t["territory"] for t in outlet_store.list_territories(enabled_only=True)]
    except Exception:
        return []


def edit_row(day, territory, rep=None, in_time=None, out_time=None):
    """Your correction to one row (the original export is never changed)."""
    conn = db.get_connection()
    r = conn.execute("SELECT rep, in_time, out_time FROM att_rows WHERE day=? AND territory=?", (day, territory)).fetchone()
    if not r:
        conn.close()
        return False, "That row no longer exists — read the day again."
    rep_v, in_v, out_v = r
    if rep is not None:
        rep_v = str(rep).strip()
    for name, v in (("Logged in", in_time), ("Logged out", out_time)):
        if v is not None and norm_time(v) is None:
            conn.close()
            return False, f"{name} '{v}' isn't a time (use HH:MM or HH:MM:SS)."
    if in_time is not None:
        in_v = norm_time(in_time)
    if out_time is not None:
        out_v = norm_time(out_time)
    if not in_v:
        conn.close()
        return False, "Logged in can't be empty — this rep is in SFA's list because they logged in."
    if out_v and out_v < in_v:
        conn.close()
        return False, "Logged out is earlier than logged in."
    conn.execute("UPDATE att_rows SET rep=?, in_time=?, out_time=?, edited=1, updated_at=? WHERE day=? AND territory=?",
                 (rep_v, in_v, out_v, _now(), day, territory))
    conn.execute("UPDATE att_rows SET edited=0 WHERE day=? AND territory=? AND rep=orig_rep AND in_time=orig_in "
                 "AND out_time=orig_out", (day, territory))
    conn.commit()
    conn.close()
    return True, ""


def undo_edit(day, territory):
    conn = db.get_connection()
    conn.execute("UPDATE att_rows SET rep=orig_rep, in_time=orig_in, out_time=orig_out, edited=0 WHERE day=? AND territory=?",
                 (day, territory))
    conn.commit()
    conn.close()
    return True, ""


def days_list(limit=60):
    conn = db.get_connection()
    rows = conn.execute("SELECT d.day, d.source, d.read_at, COUNT(r.territory), SUM(r.out_time<>'') FROM att_days d "
                        "LEFT JOIN att_rows r ON r.day=d.day GROUP BY d.day ORDER BY d.day DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [{"day": a, "source": b, "read_at": c, "reps": n or 0, "logged_out": lo or 0} for a, b, c, n, lo in rows]


def history(d_from, d_to, q=""):
    """Every row in a period, plus a per-territory summary (days logged in, average login, days not logged out)."""
    conn = db.get_connection()
    rows = [dict(zip(_ROW_COLS, r)) for r in conn.execute(
        f"SELECT {', '.join(_ROW_COLS)} FROM att_rows WHERE day BETWEEN ? AND ? ORDER BY day DESC, in_time",
        (d_from, d_to))]
    days = [r[0] for r in conn.execute("SELECT day FROM att_days WHERE day BETWEEN ? AND ? ORDER BY day", (d_from, d_to))]
    conn.close()
    always = {squash(t) for t in always_left_out()}
    for r in rows:
        r["left_out"] = squash(r["territory"]) in always
    ql = (q or "").strip().lower()
    if ql:
        rows = [r for r in rows if ql in f"{r['territory']} {r['rep']} {r['rep_code']} {r['asm']} {r['region']}".lower()]
    summary = {}
    for r in [r for r in rows if not r["left_out"]]:
        s = summary.setdefault(r["territory"], {"territory": r["territory"], "rep": r["rep"], "asm": r["asm"], "days": 0,
                                                "not_out": 0, "_mins": []})
        s["days"] += 1
        s["not_out"] += 0 if r["out_time"] else 1
        t = norm_time(r["in_time"])
        if t:
            s["_mins"].append(int(t[:2]) * 60 + int(t[3:5]))
    out = []
    for s in summary.values():
        m = s.pop("_mins")
        avg = round(sum(m) / len(m)) if m else None
        s["avg_in"] = f"{avg // 60:02d}:{avg % 60:02d}" if avg is not None else ""
        s["first_in"] = f"{min(m) // 60:02d}:{min(m) % 60:02d}" if m else ""
        s["missing_days"] = max(0, len(days) - s["days"])
        out.append(s)
    out.sort(key=lambda s: (-s["days"], s["territory"]))
    return {"from": d_from, "to": d_to, "days": days, "rows": rows, "summary": out}


def export_history(d_from, d_to, q="", folder=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    h = history(d_from, d_to, q)
    folder = folder or os.path.join(os.path.expanduser("~"), "Chimera", "exports")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"Rep_Attendance_{d_from}_to_{d_to}_{datetime.now():%H%M%S}.xlsx")
    wb = Workbook()
    fill = PatternFill("solid", fgColor="B5121B")

    def sheet(ws, cols, rows, widths):
        ws.append(cols)
        for cell in ws[1]:
            cell.font, cell.fill, cell.alignment = Font(bold=True, color="FFFFFF"), fill, Alignment(vertical="center")
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Daily"
    sheet(ws, ["Date", "Territory", "Sales rep", "Rep code", "Rep type", "Logged in", "Logged out", "Region", "Area", "ASM",
               "Distributor", "Phone", "Corrected", "Left out"],
          [[r["day"], r["territory"], r["rep"], r["rep_code"], r["rep_type"], r["in_time"], r["out_time"] or "-",
            r["region"], r["area"], r["asm"], r["distributor"], r["phone"], "yes" if r["edited"] else "",
            "always (test territory)" if r["left_out"] else ""] for r in h["rows"]],
          [11, 20, 30, 10, 12, 10, 10, 26, 16, 24, 30, 14, 9, 20])
    ws2 = wb.create_sheet("By territory")
    sheet(ws2, ["Territory", "Sales rep", "ASM", "Days logged in", f"Days without login (of {len(h['days'])})",
                "Average login", "Earliest login", "Days not logged out"],
          [[s["territory"], s["rep"], s["asm"], s["days"], s["missing_days"], s["avg_in"], s["first_in"], s["not_out"]]
           for s in h["summary"]], [20, 30, 24, 14, 22, 13, 13, 18])
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# Runs (download / build / send)
# ---------------------------------------------------------------------------
_RUN_COLS = ["id", "day", "started_at", "finished_at", "trigger", "slot", "status", "state", "style", "images", "pdf",
             "counts", "message", "sent_at", "left_out"]


def start_run(day, trigger, slot=""):
    conn = db.get_connection()
    rid = conn.execute("INSERT INTO att_runs (day, started_at, trigger, slot) VALUES (?,?,?,?)",
                       (day, _now(), trigger, slot)).lastrowid
    conn.commit()
    conn.close()
    return rid


def set_run(rid, **kw):
    for k in ("images", "counts", "left_out"):
        if k in kw and not isinstance(kw[k], str):
            kw[k] = json.dumps(kw[k])
    conn = db.get_connection()
    conn.execute(f"UPDATE att_runs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", list(kw.values()) + [rid])
    conn.commit()
    conn.close()


def _run_row(r):
    x = dict(zip(_RUN_COLS, r))
    x["images"] = json.loads(x["images"] or "[]")
    x["counts"] = json.loads(x["counts"] or "{}")
    x["left_out"] = json.loads(x["left_out"] or "[]")
    return x


def get_run(rid):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_RUN_COLS)} FROM att_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    return _run_row(r) if r else None


def list_runs(limit=20, day=None):
    conn = db.get_connection()
    q = f"SELECT {', '.join(_RUN_COLS)} FROM att_runs" + (" WHERE day=?" if day else "") + " ORDER BY id DESC LIMIT ?"
    rows = [_run_row(r) for r in conn.execute(q, ((day, limit) if day else (limit,)))]
    conn.close()
    return rows


def get_run_log(rid):
    conn = db.get_connection()
    r = conn.execute("SELECT log FROM att_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    return (r[0] if r else "") or ""


def mark_interrupted():
    conn = db.get_connection()
    conn.execute("UPDATE att_runs SET status='interrupted', finished_at=? WHERE status='running'", (_now(),))
    conn.commit()
    conn.close()
