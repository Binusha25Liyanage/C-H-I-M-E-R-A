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


# Details read from SFA's Users page (User Type = DISTRIBUTOR) by distributor_sync.py
SFA_COLUMNS = [("sfa_status", "TEXT NOT NULL DEFAULT ''"),   # active | inactive | '' (added by hand / unknown)
               ("vacant", "INTEGER NOT NULL DEFAULT 0"),
               ("in_sfa", "INTEGER"),                       # 1 in the last SFA list, 0 gone, NULL never synced
               ("phone", "TEXT NOT NULL DEFAULT ''"), ("address", "TEXT NOT NULL DEFAULT ''"),
               ("territory", "TEXT NOT NULL DEFAULT ''"), ("region", "TEXT NOT NULL DEFAULT ''"),
               ("sfa_area", "TEXT NOT NULL DEFAULT ''"), ("sa", "TEXT NOT NULL DEFAULT ''"),
               ("rsm", "TEXT NOT NULL DEFAULT ''"), ("asm", "TEXT NOT NULL DEFAULT ''"), ("ase", "TEXT NOT NULL DEFAULT ''"),
               ("synced_at", "TEXT NOT NULL DEFAULT ''"),
               ("add_fields", "TEXT NOT NULL DEFAULT '{}'")]   # Add Loading: territory / distributor / sales_rep used


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
            kind        TEXT NOT NULL,                    -- prepare | confirm | sync
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
    have = {r[1] for r in cur.execute("PRAGMA table_info(distributors)")}
    for col, decl in SFA_COLUMNS:
        if col not in have:
            cur.execute(f"ALTER TABLE distributors ADD COLUMN {col} {decl}")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Distributors
# ---------------------------------------------------------------------------
_DCOLS = ["id", "name", "code", "area", "frequency", "filters", "notes", "position", "updated_at"] + [c for c, _ in SFA_COLUMNS]


def _drow(r):
    d = dict(zip(_DCOLS, r))
    d["filters"] = json.loads(d["filters"] or "[]")
    d["vacant"] = bool(d.get("vacant"))
    try:
        d["add_fields"] = json.loads(d.get("add_fields") or "{}")
    except ValueError:
        d["add_fields"] = {}
    d["blocked"] = d.get("sfa_status") == "inactive" or d.get("in_sfa") == 0
    d["problems"] = distributor_problems(d)
    return d


def distributor_problems(d):
    p = []
    if d.get("sfa_status") == "inactive":
        p.append("Inactive in SFA — loadings can't be prepared or confirmed.")
    if d.get("in_sfa") == 0:
        p.append("Not in SFA's distributor list any more.")
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
    """Returns (ok, [names refused]) — only distributors active in SFA can be Daily."""
    if freq not in FREQUENCIES:
        return False, []
    refused = []
    if freq == "daily":
        blocked = {d["id"]: d["name"] for d in list_distributors() if d["blocked"]}
        refused = [blocked[i] for i in ids if i in blocked]
        ids = [i for i in ids if i not in blocked]
    conn = db.get_connection()
    conn.executemany("UPDATE distributors SET frequency=?, updated_at=? WHERE id=?", [(freq, _now(), i) for i in ids])
    conn.commit()
    conn.close()
    return True, refused


# ---------------------------------------------------------------------------
# The distributor list from SFA (Users page, User Type = DISTRIBUTOR)
# ---------------------------------------------------------------------------
def apply_sfa_list(rows, complete=True):
    """rows: [{code, name, status ('active'|'inactive'), phone, address, territory, region, area, sa, rsm, asm, ase}].
    Matches by SFA user code (then by name for distributors added by hand). New ones are added as
    'On request' with their Distributor filter set to the SFA name. complete=True: distributors that
    have a code but aren't in the list are marked 'not in SFA' (never deleted).
    Returns counts and names for the summary."""
    now = _now()
    out = {"added": [], "updated": 0, "renamed": [], "now_inactive": [], "now_active": [], "gone": [], "total": 0}
    merged = {}     # SFA can list the same user code twice (e.g. once without a territory): keep the filled-in details
    for x in rows:
        code = str(x.get("code") or "").strip().upper()
        if not code:
            continue
        if code not in merged:
            merged[code] = dict(x)
            continue
        m = merged[code]
        for k, v in x.items():
            if v and not m.get(k):
                m[k] = v
        if x.get("status") == "active":
            m["status"] = "active"
    rows = list(merged.values())
    conn = db.get_connection()
    try:
        existing = conn.execute("SELECT id, name, code, filters, sfa_status FROM distributors").fetchall()
        by_code = {r[2].strip().upper(): r for r in existing if r[2].strip()}
        by_name = {r[1].strip().lower(): r for r in existing if not r[2].strip()}
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM distributors").fetchone()[0]
        seen = set()
        for x in rows:
            code = str(x.get("code") or "").strip().upper()
            name = str(x.get("name") or "").strip()
            if not code or not name or code in seen:
                continue
            seen.add(code)
            out["total"] += 1
            status = x.get("status") or ""
            det = {"sfa_status": status, "vacant": 1 if "vacant" in name.lower() else 0, "in_sfa": 1,
                   "phone": str(x.get("phone") or "")[:40], "address": str(x.get("address") or "")[:200],
                   "territory": str(x.get("territory") or "")[:80], "region": str(x.get("region") or "")[:80],
                   "sfa_area": str(x.get("area") or "")[:80], "sa": str(x.get("sa") or "")[:80],
                   "rsm": str(x.get("rsm") or "")[:80], "asm": str(x.get("asm") or "")[:80],
                   "ase": str(x.get("ase") or "")[:80], "synced_at": now}
            r = by_code.get(code) or by_name.get(name.lower())
            if r is None:
                filters = [{"label": "Distributor", "type": "dropdown", "value": name, "options": []}]
                vals = {"name": name[:120], "code": code, "area": det["territory"], "frequency": "request",
                        "filters": json.dumps(filters), "notes": "", "position": pos, "updated_at": now, **det}
                conn.execute(f"INSERT INTO distributors ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                             list(vals.values()))
                pos += 1
                out["added"].append(name)
                continue
            did, old_name, _old_code, filters_json, old_status = r
            filters = json.loads(filters_json or "[]")
            if old_name != name:
                for f in filters:   # the Distributor filter follows SFA's name unless it was changed by hand
                    if f.get("label", "").lower() == "distributor" and f.get("value") == old_name:
                        f["value"] = name
                out["renamed"].append(f"{old_name} → {name}")
            if old_status != status and status == "inactive":
                out["now_inactive"].append(name)
            if status == "inactive":     # only active distributors can be Daily
                conn.execute("UPDATE distributors SET frequency='request' WHERE id=? AND frequency='daily'", (did,))
            if old_status == "inactive" and status == "active":
                out["now_active"].append(name)
            vals = {"name": name[:120], "code": code, "filters": json.dumps(filters), **det}
            conn.execute(f"UPDATE distributors SET {', '.join(f'{k}=?' for k in vals)} WHERE id=?",
                         [*vals.values(), did])
            out["updated"] += 1
        if complete and seen:
            for did, name, code, _f, _s in existing:
                if code.strip() and code.strip().upper() not in seen:
                    n = conn.execute("UPDATE distributors SET in_sfa=0 WHERE id=? AND COALESCE(in_sfa, 1)=1",
                                     (did,)).rowcount
                    if n:
                        out["gone"].append(name)
        conn.commit()
    finally:
        conn.close()
    return out


def sync_summary_text(r):
    parts = [f"{r['total']} distributors in SFA"]
    if r["added"]:
        parts.append(f"{len(r['added'])} new")
    if r["renamed"]:
        parts.append(f"{len(r['renamed'])} renamed")
    if r["now_inactive"]:
        parts.append(f"{len(r['now_inactive'])} became inactive ({', '.join(r['now_inactive'][:5])})")
    if r["now_active"]:
        parts.append(f"{len(r['now_active'])} active again")
    if r["gone"]:
        parts.append(f"{len(r['gone'])} no longer listed ({', '.join(r['gone'][:5])})")
    return ", ".join(parts) + "."


def last_sync():
    conn = db.get_connection()
    r = conn.execute("SELECT id, started_at, finished_at, trigger, status, message FROM loading_runs "
                     "WHERE kind='sync' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(zip(["id", "started_at", "finished_at", "trigger", "status", "message"], r)) if r else None


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
def set_add_fields(did, fields):
    """Remembers the Add Loading fields that worked (or the ones the user typed) for this distributor."""
    vals = {k: str(v or "").strip()[:120] for k, v in (fields or {}).items() if k in ("territory", "distributor", "sales_rep")}
    conn = db.get_connection()
    conn.execute("UPDATE distributors SET add_fields=? WHERE id=?", (json.dumps(vals), int(did)))
    conn.commit()
    conn.close()


def record_add(d, res, dry_run):
    """An Add Loading attempt, kept in the runs list (kind 'add')."""
    rid = start_run("add", "manual")
    if dry_run:
        msg = f"{d['name']}: dry run — page filled, stopped before SAVE (loading number {res.get('number') or '?'})."
    else:
        msg = (f"{d['name']}: loading {res.get('number') or '?'} saved in SFA"
               + ("; pending on View Loadings ✓" if res.get("verified") else "; not yet seen on View Loadings") + ".")
    finish_run(rid, "done", msg, "")
    return rid


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
