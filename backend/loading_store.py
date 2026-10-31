"""
loading_store.py
Distributor Loadings: the distributors Chimera handles, and every prepare /
confirm run with the per-loading result.

How the SFA side works (see loading_automation.py):
  View Loadings page -> this distributor's filters -> SEARCH -> the pending
  loading (the ✓ in the CONFIRM column) -> CONFIRM LOADING page (read only)
  -> the user approves in Chimera -> Confirm -> "Are you sure?" -> Yes.

Item states (one row per distributor per prepare run):
  pending_approval  read from SFA, waiting for the user
  nothing_pending   SFA showed no pending loading for this distributor
  failed            couldn't read it (reason in message)
  confirming        approved, Chimera is confirming it now
  confirmed         confirmed in SFA and verified
  confirm_failed    approved but confirming didn't work (reason in message)
  changed           the loading in SFA differs from what was approved — not confirmed
  skipped           the user chose not to confirm it
  superseded        a newer prepare replaced this one
"""

import json
from datetime import datetime

import db

DEFAULT_URL = "https://lakmee.salespro.lk/it/loading"
FREQUENCIES = ("daily", "request")
MAX_TABS = 12
OPEN_STATES = ("pending_approval",)
FINAL_STATES = ("nothing_pending", "failed", "confirmed", "confirm_failed", "changed", "skipped", "superseded")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_schema():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS distributors (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            code       TEXT NOT NULL DEFAULT '',
            area       TEXT NOT NULL DEFAULT '',
            frequency  TEXT NOT NULL DEFAULT 'request',   -- daily | request
            filters    TEXT NOT NULL DEFAULT '[]',        -- View Loadings filters for this distributor
            notes      TEXT NOT NULL DEFAULT '',
            position   INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS loading_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL,                    -- prepare | confirm
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            trigger     TEXT NOT NULL,
            status      TEXT NOT NULL,                    -- running | done | failed | cancelled
            message     TEXT NOT NULL DEFAULT '',
            log         TEXT
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS loading_items (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id           INTEGER NOT NULL,
            distributor_id   INTEGER NOT NULL,
            distributor_name TEXT NOT NULL,
            state            TEXT NOT NULL,
            loading_number   TEXT NOT NULL DEFAULT '',
            rep              TEXT NOT NULL DEFAULT '',
            loading_date     TEXT NOT NULL DEFAULT '',
            loading_time     TEXT NOT NULL DEFAULT '',
            confirm_url      TEXT NOT NULL DEFAULT '',
            products         TEXT NOT NULL DEFAULT '[]',
            totals           TEXT NOT NULL DEFAULT '{}',
            screenshot       TEXT NOT NULL DEFAULT '',
            message          TEXT NOT NULL DEFAULT '',
            prepared_at      TEXT,
            decided_at       TEXT,
            confirmed_at     TEXT,
            confirm_run_id   INTEGER
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_loading_items_state ON loading_items(state)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Distributors
# ---------------------------------------------------------------------------
_DCOLS = ["id", "name", "code", "area", "frequency", "filters", "notes", "position", "updated_at"]


def _drow(r):
    d = dict(zip(_DCOLS, r))
    d["filters"] = json.loads(d["filters"] or "[]")
    d["problems"] = distributor_problems(d)
    return d


def distributor_problems(d):
    p = []
    if not d["filters"]:
        p.append("No filters yet — open Edit and use Detect filters.")
    elif not any(f["label"].lower() == "distributor" and f.get("value") for f in d["filters"]):
        p.append("The 'Distributor' filter isn't set — Chimera wouldn't know whose loading to open.")
    return p


def list_distributors():
    conn = db.get_connection()
    rows = conn.execute(f"SELECT {', '.join(_DCOLS)} FROM distributors ORDER BY position, name").fetchall()
    last = {r[0]: r[1:] for r in conn.execute("""
        SELECT distributor_id, state, loading_number, COALESCE(confirmed_at, decided_at, prepared_at), message
        FROM loading_items WHERE id IN (SELECT MAX(id) FROM loading_items GROUP BY distributor_id)""")}
    conn.close()
    out = []
    for r in rows:
        d = _drow(r)
        lt = last.get(d["id"])
        d["last"] = {"state": lt[0], "loading_number": lt[1], "at": lt[2], "message": lt[3]} if lt else None
        out.append(d)
    return out


def get_distributor(did):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_DCOLS)} FROM distributors WHERE id=?", (did,)).fetchone()
    conn.close()
    return _drow(r) if r else None


def _clean_filters(filters):
    import automation_store as AS
    clean, err = AS._clean_profile({"name": "x", "filters": filters or []})
    if err:
        return None, err
    return json.loads(clean["filters"]), None


def save_distributor(data):
    """Create (no id) or update. Returns (ok, message, distributor)."""
    name = str(data.get("name") or "").strip()
    if not name:
        return False, "Give the distributor a name.", None
    freq = data.get("frequency") or "request"
    if freq not in FREQUENCIES:
        return False, "Choose Daily or On request.", None
    filters, err = _clean_filters(data.get("filters"))
    if err:
        return False, err, None
    vals = {"name": name[:120], "code": str(data.get("code") or "").strip()[:60],
            "area": str(data.get("area") or "").strip()[:120], "frequency": freq,
            "filters": json.dumps(filters), "notes": str(data.get("notes") or "").strip()[:500],
            "updated_at": _now()}
    conn = db.get_connection()
    try:
        dup = conn.execute("SELECT id FROM distributors WHERE lower(name)=lower(?) AND id IS NOT ?",
                           (name, data.get("id"))).fetchone()
        if dup:
            return False, f"There is already a distributor called '{name}'.", None
        if data.get("id"):
            sets = ", ".join(f"{k}=:{k}" for k in vals)
            n = conn.execute(f"UPDATE distributors SET {sets} WHERE id=:id", {**vals, "id": data["id"]}).rowcount
            if not n:
                return False, "That distributor no longer exists.", None
            did = data["id"]
        else:
            vals["position"] = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM distributors").fetchone()[0]
            cur = conn.execute(f"INSERT INTO distributors ({', '.join(vals)}) VALUES ({', '.join(':' + k for k in vals)})",
                               vals)
            did = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return True, "Distributor saved.", get_distributor(did)


def set_frequency(ids, freq):
    if freq not in FREQUENCIES:
        return False
    conn = db.get_connection()
    conn.executemany("UPDATE distributors SET frequency=?, updated_at=? WHERE id=?", [(freq, _now(), i) for i in ids])
    conn.commit()
    conn.close()
    return True


def duplicate_distributor(did):
    d = get_distributor(did)
    if not d:
        return False, "That distributor no longer exists.", None
    base, n = f"{d['name']} (copy)", 2
    names = {x["name"].lower() for x in list_distributors()}
    name = base
    while name.lower() in names:
        name, n = f"{d['name']} (copy {n})", n + 1
    return save_distributor({**d, "id": None, "name": name})


def delete_distributor(did):
    conn = db.get_connection()
    conn.execute("DELETE FROM distributors WHERE id=?", (did,))
    conn.commit()
    conn.close()


def daily_ids():
    return [d["id"] for d in list_distributors() if d["frequency"] == "daily" and not d["problems"]]


# ---------------------------------------------------------------------------
# Runs and items
# ---------------------------------------------------------------------------
def start_run(kind, trigger):
    conn = db.get_connection()
    cur = conn.execute("INSERT INTO loading_runs (kind, started_at, trigger, status) VALUES (?, ?, ?, 'running')",
                       (kind, _now(), trigger))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def finish_run(rid, status, message="", log=""):
    conn = db.get_connection()
    conn.execute("UPDATE loading_runs SET finished_at=?, status=?, message=?, log=? WHERE id=?",
                 (_now(), status, message, log[-200_000:], rid))
    conn.commit()
    conn.close()


def list_runs(limit=30):
    conn = db.get_connection()
    rows = conn.execute("""SELECT id, kind, started_at, finished_at, trigger, status, message FROM loading_runs
                           ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    conn.close()
    return [dict(zip(["id", "kind", "started_at", "finished_at", "trigger", "status", "message"], r)) for r in rows]


def get_run_log(rid):
    conn = db.get_connection()
    r = conn.execute("SELECT log FROM loading_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    return (r[0] if r else "") or ""


def mark_interrupted():
    conn = db.get_connection()
    conn.execute("""UPDATE loading_runs SET status='interrupted', finished_at=?,
                    message='Chimera was closed before this finished.' WHERE status='running'""", (_now(),))
    conn.execute("""UPDATE loading_items SET state='confirm_failed', message='Chimera was closed while confirming —
                    check this loading in SFA.' WHERE state='confirming'""")
    conn.commit()
    conn.close()


_ICOLS = ["id", "run_id", "distributor_id", "distributor_name", "state", "loading_number", "rep", "loading_date",
          "loading_time", "confirm_url", "products", "totals", "screenshot", "message", "prepared_at",
          "decided_at", "confirmed_at", "confirm_run_id"]


def _irow(r):
    d = dict(zip(_ICOLS, r))
    d["products"] = json.loads(d["products"] or "[]")
    d["totals"] = json.loads(d["totals"] or "{}")
    return d


def add_item(run_id, distributor, state, **kw):
    """One distributor's result in a prepare run. A new pending item replaces older pending ones."""
    conn = db.get_connection()
    if state == "pending_approval":
        conn.execute("""UPDATE loading_items SET state='superseded', decided_at=? WHERE distributor_id=?
                        AND state='pending_approval'""", (_now(), distributor["id"]))
    vals = {"run_id": run_id, "distributor_id": distributor["id"], "distributor_name": distributor["name"],
            "state": state, "prepared_at": _now(),
            "loading_number": kw.get("loading_number", ""), "rep": kw.get("rep", ""),
            "loading_date": kw.get("loading_date", ""), "loading_time": kw.get("loading_time", ""),
            "confirm_url": kw.get("confirm_url", ""), "products": json.dumps(kw.get("products") or []),
            "totals": json.dumps(kw.get("totals") or {}), "screenshot": kw.get("screenshot", ""),
            "message": kw.get("message", "")}
    cur = conn.execute(f"INSERT INTO loading_items ({', '.join(vals)}) VALUES ({', '.join(':' + k for k in vals)})", vals)
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def update_item(iid, **kw):
    allowed = {"state", "message", "decided_at", "confirmed_at", "confirm_run_id", "screenshot", "totals"}
    vals = {k: (json.dumps(v) if k == "totals" else v) for k, v in kw.items() if k in allowed}
    if not vals:
        return
    conn = db.get_connection()
    conn.execute(f"UPDATE loading_items SET {', '.join(f'{k}=:{k}' for k in vals)} WHERE id=:id", {**vals, "id": iid})
    conn.commit()
    conn.close()


def get_item(iid):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_ICOLS)} FROM loading_items WHERE id=?", (iid,)).fetchone()
    conn.close()
    return _irow(r) if r else None


def list_items(run_id=None, states=None, limit=200):
    q, args = f"SELECT {', '.join(_ICOLS)} FROM loading_items", []
    where = []
    if run_id:
        where.append("run_id=?")
        args.append(run_id)
    if states:
        where.append(f"state IN ({', '.join('?' * len(states))})")
        args += list(states)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    conn = db.get_connection()
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return [_irow(r) for r in rows]


def pending_items():
    return list(reversed(list_items(states=OPEN_STATES)))


def skip_items(ids):
    for i in ids:
        it = get_item(i)
        if it and it["state"] == "pending_approval":
            update_item(i, state="skipped", decided_at=_now(), message="Not confirmed (skipped in Chimera).")


def today_summary(day=None):
    day = day or datetime.now().date().isoformat()
    conn = db.get_connection()
    rows = conn.execute("""SELECT state, COUNT(*) FROM loading_items WHERE substr(prepared_at, 1, 10)=?
                           OR substr(confirmed_at, 1, 10)=? GROUP BY state""", (day, day)).fetchall()
    conn.close()
    return dict(rows)
