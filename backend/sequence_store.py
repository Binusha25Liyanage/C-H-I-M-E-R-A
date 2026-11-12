"""
sequence_store.py
Outlet Updates steps 2-3: visit sequence and Visit State changes that reps
send back on their route sheets. No browser here — storage and the rules:

  * SFA's active routes (View Route export): code, name, territory...;
  * every route sheet received (a "sheet"): where it came from, which route
    it is for, what the rep filled in, and what happened to it;
  * one line per outlet: rep's sequence / Top 15 mark / remark / demand
    product, SFA's current sequence and Visit State (read before the
    preview), the new sequence and Visit State, and the outcome.

Rules (agreed with the user):
  * Top 15: any mark counts — a rank number, ✓, Y, Yes, 1 … Those outlets
    become Mandatory by default; the user can change any outlet in the
    preview (one or many). Other outlets keep the Visit State they have.
  * New sequence: the outlets the rep numbered, in the rep's order, then
    the ones left blank in their current SFA order, then outlets that are on
    the route in SFA but missing from the sheet — numbered 1..N.
  * Only outlets whose sequence or Visit State actually changes are saved.

A sheet's state:
  received   read from the file; SFA's current values not read yet
  ready      preview complete — waiting for approval
  approved   approved (or approval not required) — waiting to be applied
  applying   being saved in SFA
  done       every change saved (or nothing needed changing)
  partial    some outlets saved, some failed
  failed     reading SFA or saving failed for the whole sheet
  rejected   the file can't be used (reason in message)
  discarded  thrown away by the user
  superseded a newer sheet for the same route arrived before this one was applied
"""

import hashlib
import json
import re
from datetime import datetime

import db

DEFAULT_ROUTES_URL = "https://lakmee.salespro.lk/it/level-seven"      # Sales Organization > Route > View Route
TOP_LIMIT = 15
OPEN_STATES = ("received", "ready", "approved")
VISIT_STATES = ("Mandatory", "Optional")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def squash(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def init_schema():
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sfa_routes (
            code            TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            territory       TEXT NOT NULL DEFAULT '',
            region          TEXT NOT NULL DEFAULT '',
            area            TEXT NOT NULL DEFAULT '',
            supervisor_area TEXT NOT NULL DEFAULT '',
            visit_frequency TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'ACTIVE',
            active          INTEGER NOT NULL DEFAULT 1,      -- 0 = no longer in SFA's active list
            synced_at       TEXT NOT NULL
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seq_sheets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at  TEXT NOT NULL,
            source       TEXT NOT NULL,                  -- upload | whatsapp | gsheet
            sender       TEXT NOT NULL DEFAULT '',
            file_name    TEXT NOT NULL DEFAULT '',
            file_path    TEXT NOT NULL DEFAULT '',
            file_hash    TEXT NOT NULL DEFAULT '',
            format       TEXT NOT NULL DEFAULT '',       -- rep | chimera
            territory    TEXT NOT NULL DEFAULT '',
            route_code   TEXT NOT NULL DEFAULT '',
            route_name   TEXT NOT NULL DEFAULT '',
            rep          TEXT NOT NULL DEFAULT '',
            distributor  TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL,
            problems     TEXT NOT NULL DEFAULT '[]',     -- [{level: error|warn, text}]
            message      TEXT NOT NULL DEFAULT '',
            read_at      TEXT,                           -- SFA's current values read
            decided_at   TEXT,
            decided_by   TEXT NOT NULL DEFAULT '',       -- you | auto
            applied_at   TEXT,
            dry_run      INTEGER NOT NULL DEFAULT 0,
            run_id       INTEGER
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seq_lines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_id    INTEGER NOT NULL,
            line        INTEGER NOT NULL,                -- row in the file (0 = only in SFA)
            outlet_code TEXT NOT NULL,
            outlet_name TEXT NOT NULL DEFAULT '',
            rep_seq     TEXT NOT NULL DEFAULT '',        -- as the rep wrote it
            top_mark    TEXT NOT NULL DEFAULT '',
            top         INTEGER NOT NULL DEFAULT 0,
            top_rank    INTEGER,
            remark      TEXT NOT NULL DEFAULT '',
            demand      TEXT NOT NULL DEFAULT '',
            in_sfa      INTEGER,                         -- NULL until read; 1 on the route in SFA; 0 not found
            edit_id     TEXT NOT NULL DEFAULT '',
            cur_seq     TEXT NOT NULL DEFAULT '',
            cur_state   TEXT NOT NULL DEFAULT '',
            new_seq     INTEGER,
            new_state   TEXT NOT NULL DEFAULT '',
            state_by    TEXT NOT NULL DEFAULT '',        -- top | you | '' (kept)
            outcome     TEXT NOT NULL DEFAULT '',        -- '' | unchanged | updated | would_update | failed | not_found | skipped
            message     TEXT NOT NULL DEFAULT '',
            phone_fixes TEXT NOT NULL DEFAULT '',
            done_at     TEXT
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS seq_lines_sheet ON seq_lines(sheet_id)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS seq_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL,                   -- routes | read | apply
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            trigger     TEXT NOT NULL DEFAULT 'manual',
            status      TEXT NOT NULL DEFAULT 'running',
            message     TEXT NOT NULL DEFAULT '',
            log         TEXT NOT NULL DEFAULT ''
        )""")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
_RCOLS = ["code", "name", "territory", "region", "area", "supervisor_area", "visit_frequency", "status", "active",
          "synced_at"]


def apply_routes(rows, complete=True):
    """Merges a View Route export (active routes). complete=True: routes not in it are marked no longer active."""
    now = _now()
    conn = db.get_connection()
    old = {r[0]: r for r in conn.execute("SELECT code, name, active FROM sfa_routes")}
    added, renamed, gone = [], [], []
    seen = set()
    for r in rows:
        code = (r.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        if code not in old:
            added.append(f"{r['name']} ({code})")
        elif old[code][1] != r["name"]:
            renamed.append(f"{old[code][1]} → {r['name']}")
        conn.execute("""INSERT INTO sfa_routes (code, name, territory, region, area, supervisor_area, visit_frequency,
                        status, active, synced_at) VALUES (?,?,?,?,?,?,?,?,1,?)
                        ON CONFLICT(code) DO UPDATE SET name=excluded.name, territory=excluded.territory,
                        region=excluded.region, area=excluded.area, supervisor_area=excluded.supervisor_area,
                        visit_frequency=excluded.visit_frequency, status=excluded.status, active=1,
                        synced_at=excluded.synced_at""",
                     (code, r["name"], r.get("territory", ""), r.get("region", ""), r.get("area", ""),
                      r.get("supervisor_area", ""), str(r.get("visit_frequency", "")), r.get("status") or "ACTIVE", now))
    if complete:
        for code, (_c, name, active) in old.items():
            if code not in seen and active:
                gone.append(f"{name} ({code})")
                conn.execute("UPDATE sfa_routes SET active=0 WHERE code=?", (code,))
    conn.commit()
    conn.close()
    return {"total": len(seen), "added": added, "renamed": renamed, "gone": gone}


def routes_summary_text(res):
    parts = [f"{res['total']} active routes"]
    if res["added"]:
        parts.append(f"{len(res['added'])} new")
    if res["renamed"]:
        parts.append(f"{len(res['renamed'])} renamed")
    if res["gone"]:
        parts.append(f"{len(res['gone'])} no longer active")
    return ", ".join(parts) + "."


def list_routes(territory=None, active_only=True):
    conn = db.get_connection()
    q = f"SELECT {', '.join(_RCOLS)} FROM sfa_routes WHERE 1=1"
    args = []
    if active_only:
        q += " AND active=1"
    if territory:
        q += " AND territory=?"
        args.append(territory)
    rows = conn.execute(q + " ORDER BY territory, name", args).fetchall()
    conn.close()
    return [dict(zip(_RCOLS, r)) | {"active": bool(r[8])} for r in rows]


def get_route(code):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_RCOLS)} FROM sfa_routes WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(zip(_RCOLS, r)) | {"active": bool(r[8])} if r else None


def find_route(code="", name="", territory=""):
    """By route code first (exact, any case); else by name (+ territory), spaces ignored."""
    if code:
        conn = db.get_connection()
        r = conn.execute("SELECT code FROM sfa_routes WHERE UPPER(code)=UPPER(?)", (code.strip(),)).fetchone()
        conn.close()
        if r:
            return get_route(r[0])
    if name:
        hits = [x for x in list_routes(active_only=False) if squash(x["name"]) == squash(name)
                and (not territory or squash(x["territory"]) == squash(territory))]
        hits.sort(key=lambda x: not x["active"])
        if len(hits) == 1 or (hits and hits[0]["active"] and not (len(hits) > 1 and hits[1]["active"])):
            return hits[0]
    return None


def routes_info():
    conn = db.get_connection()
    n, last = conn.execute("SELECT COUNT(*), MAX(synced_at) FROM sfa_routes WHERE active=1").fetchone()
    terrs = conn.execute("SELECT COUNT(DISTINCT territory) FROM sfa_routes WHERE active=1").fetchone()[0]
    conn.close()
    return {"routes": n, "territories": terrs, "synced_at": last}


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------
_SCOLS = ["id", "received_at", "source", "sender", "file_name", "file_path", "file_hash", "format", "territory",
          "route_code", "route_name", "rep", "distributor", "state", "problems", "message", "read_at", "decided_at",
          "decided_by", "applied_at", "dry_run", "run_id"]
_LCOLS = ["id", "sheet_id", "line", "outlet_code", "outlet_name", "rep_seq", "top_mark", "top", "top_rank", "remark",
          "demand", "in_sfa", "edit_id", "cur_seq", "cur_state", "new_seq", "new_state", "state_by", "outcome",
          "message", "phone_fixes", "done_at"]


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_by_hash(h):
    conn = db.get_connection()
    r = conn.execute("SELECT id FROM seq_sheets WHERE file_hash=? AND state NOT IN ('discarded') ORDER BY id DESC",
                     (h,)).fetchone()
    conn.close()
    return r[0] if r else None


def _srow(r):
    s = dict(zip(_SCOLS, r))
    s["problems"] = json.loads(s["problems"] or "[]")
    s["dry_run"] = bool(s["dry_run"])
    return s


def _lrow(r):
    l = dict(zip(_LCOLS, r))
    l["top"] = bool(l["top"])
    l["in_sfa"] = None if l["in_sfa"] is None else bool(l["in_sfa"])
    return l


def add_sheet(parsed, source, file_name, file_path="", fhash="", sender=""):
    """Stores a parsed sheet (sequence_reader.read_sheet result). Rejected if it has errors."""
    errors = [p for p in parsed["problems"] if p["level"] == "error"]
    state = "rejected" if errors else "received"
    msg = errors[0]["text"] if errors else ""
    conn = db.get_connection()
    cur = conn.execute(f"""INSERT INTO seq_sheets (received_at, source, sender, file_name, file_path, file_hash, format,
                           territory, route_code, route_name, rep, distributor, state, problems, message)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (_now(), source, sender, file_name, file_path, fhash, parsed.get("format", ""),
                        parsed.get("territory", ""), parsed.get("route_code", ""), parsed.get("route_name", ""),
                        parsed.get("rep", ""), parsed.get("distributor", ""), state, json.dumps(parsed["problems"]), msg))
    sid = cur.lastrowid
    for ln in parsed.get("lines", []):
        conn.execute("""INSERT INTO seq_lines (sheet_id, line, outlet_code, outlet_name, rep_seq, top_mark, top,
                        top_rank, remark, demand) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (sid, ln["line"], ln["code"], ln.get("name", ""), ln.get("rep_seq", ""), ln.get("top_mark", ""),
                      1 if ln.get("top") else 0, ln.get("top_rank"), ln.get("remark", ""), ln.get("demand", "")))
    if state == "received":
        # an older, not yet applied sheet for the same route is replaced by this one
        conn.execute("""UPDATE seq_sheets SET state='superseded', message=? WHERE route_code=? AND id<>?
                        AND state IN ('received','ready','approved')""",
                     (f"Replaced by a newer sheet (#{sid}).", parsed.get("route_code", ""), sid))
    conn.commit()
    conn.close()
    return sid


def get_sheet(sid, lines=True):
    conn = db.get_connection()
    r = conn.execute(f"SELECT {', '.join(_SCOLS)} FROM seq_sheets WHERE id=?", (sid,)).fetchone()
    if not r:
        conn.close()
        return None
    s = _srow(r)
    if lines:
        s["lines"] = [_lrow(x) for x in conn.execute(f"SELECT {', '.join(_LCOLS)} FROM seq_lines WHERE sheet_id=? "
                                                      "ORDER BY COALESCE(new_seq, 100000), line, id", (sid,))]
    conn.close()
    if lines:
        s["summary"] = summarize(s)
    return s


def list_sheets(states=None, limit=200, since=None):
    conn = db.get_connection()
    q = f"SELECT {', '.join(_SCOLS)} FROM seq_sheets WHERE 1=1"
    args = []
    if states:
        q += f" AND state IN ({','.join('?' * len(states))})"
        args += list(states)
    if since:
        q += " AND received_at >= ?"
        args.append(since)
    rows = [_srow(r) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit])]
    counts = {}
    for sid, n, ch, top, upd, fail in conn.execute(f"""
            SELECT sheet_id, COUNT(*),
                   SUM(CASE WHEN in_sfa=1 AND (CAST(new_seq AS TEXT)<>cur_seq OR new_state<>cur_state) THEN 1 ELSE 0 END),
                   SUM(top), SUM(outcome IN ('updated','would_update')), SUM(outcome='failed')
            FROM seq_lines WHERE sheet_id IN ({','.join(str(r['id']) for r in rows) or '0'}) GROUP BY sheet_id"""):
        counts[sid] = {"outlets": n, "changes": ch or 0, "top": top or 0, "updated": upd or 0, "failed": fail or 0}
    conn.close()
    for r in rows:
        r["counts"] = counts.get(r["id"], {"outlets": 0, "changes": 0, "top": 0, "updated": 0, "failed": 0})
    return rows


def set_sheet(sid, **kw):
    if not kw:
        return
    if "problems" in kw:
        kw["problems"] = json.dumps(kw["problems"])
    if "dry_run" in kw:
        kw["dry_run"] = 1 if kw["dry_run"] else 0
    conn = db.get_connection()
    conn.execute(f"UPDATE seq_sheets SET {', '.join(k + '=?' for k in kw)} WHERE id=?", list(kw.values()) + [sid])
    conn.commit()
    conn.close()


def set_line(lid, **kw):
    conn = db.get_connection()
    conn.execute(f"UPDATE seq_lines SET {', '.join(k + '=?' for k in kw)} WHERE id=?", list(kw.values()) + [lid])
    conn.commit()
    conn.close()


def summarize(s):
    ls = s.get("lines") or []
    on = [l for l in ls if l["in_sfa"]]
    seq = [l for l in on if l["new_seq"] is not None and str(l["new_seq"]) != (l["cur_seq"] or "")]
    vs = [l for l in on if l["new_state"] and l["new_state"] != l["cur_state"]]
    return {"outlets": len(ls), "in_file": sum(1 for l in ls if l["line"] > 0), "top": sum(1 for l in ls if l["top"]),
            "not_found": sum(1 for l in ls if l["in_sfa"] is False), "sfa_only": sum(1 for l in ls if l["line"] == 0),
            "seq_changes": len(seq), "state_changes": len(vs),
            "changes": len({l["id"] for l in seq} | {l["id"] for l in vs}),
            "mandatory": sum(1 for l in on if (l["new_state"] or l["cur_state"]) == "Mandatory"),
            "updated": sum(1 for l in ls if l["outcome"] == "updated"),
            "would_update": sum(1 for l in ls if l["outcome"] == "would_update"),
            "failed": sum(1 for l in ls if l["outcome"] == "failed"),
            "remarks": sum(1 for l in ls if l["remark"] or l["demand"])}


# ---------------------------------------------------------------------------
# The preview: SFA's current values -> new sequence / Visit State
# ---------------------------------------------------------------------------
def _int(v):
    try:
        f = float(str(v).strip())
        return int(f) if f == int(f) else None
    except (TypeError, ValueError):
        return None


def build_preview(sid, sfa_rows):
    """sfa_rows: outlets on the sheet's route in SFA [{code, name, edit_id, seq, state}]. Fills current values and
    works out the new sequence / Visit State. Keeps Visit States the user already chose."""
    s = get_sheet(sid)
    by_code = {}
    for r in sfa_rows:
        by_code.setdefault(r["code"].strip().upper(), r)
    conn = db.get_connection()
    conn.execute("DELETE FROM seq_lines WHERE sheet_id=? AND line=0", (sid,))     # SFA-only lines are recomputed
    lines = [l for l in s["lines"] if l["line"] > 0]
    problems = [p for p in s["problems"] if not p.get("from_sfa")]
    seen = set()
    for l in lines:
        r = by_code.get(l["outlet_code"].upper())
        seen.add(l["outlet_code"].upper())
        if r:
            conn.execute("UPDATE seq_lines SET in_sfa=1, edit_id=?, cur_seq=?, cur_state=?, outlet_name=COALESCE(NULLIF(outlet_name,''), ?) "
                         "WHERE id=?", (r.get("edit_id", ""), str(r.get("seq") or ""), r.get("state") or "", r.get("name", ""), l["id"]))
            l.update(in_sfa=True, cur_seq=str(r.get("seq") or ""), cur_state=r.get("state") or "")
        else:
            conn.execute("UPDATE seq_lines SET in_sfa=0, cur_seq='', cur_state='', new_seq=NULL, new_state='', "
                         "outcome='not_found', message=? WHERE id=?",
                         ("Not on this route in SFA — skipped.", l["id"]))
            l.update(in_sfa=False)
    extra = [r for c, r in by_code.items() if c not in seen]
    for r in extra:
        cur = conn.execute("""INSERT INTO seq_lines (sheet_id, line, outlet_code, outlet_name, in_sfa, edit_id, cur_seq,
                              cur_state, message) VALUES (?,0,?,?,1,?,?,?,?)""",
                           (sid, r["code"], r.get("name", ""), r.get("edit_id", ""), str(r.get("seq") or ""),
                            r.get("state") or "", "On the route in SFA but not in the rep's sheet — placed at the end."))
        lines.append({"id": cur.lastrowid, "line": 0, "outlet_code": r["code"], "rep_seq": "", "top": False,
                      "in_sfa": True, "cur_seq": str(r.get("seq") or ""), "cur_state": r.get("state") or "",
                      "state_by": "", "new_state": ""})
    on = [l for l in lines if l["in_sfa"]]
    numbered = sorted([l for l in on if l["line"] > 0 and _int(l["rep_seq"]) is not None],
                      key=lambda l: (_int(l["rep_seq"]), l["line"]))
    blank = [l for l in on if l["line"] > 0 and _int(l["rep_seq"]) is None]
    cur_key = lambda l: (_int(l["cur_seq"]) if _int(l["cur_seq"]) is not None else 10 ** 6, l["line"] or 10 ** 5)
    order = numbered + sorted(blank, key=cur_key) + sorted([l for l in on if l["line"] == 0], key=cur_key)
    for n, l in enumerate(order, 1):
        if l.get("state_by") == "you" and l.get("new_state"):
            new_state, by = l["new_state"], "you"
        elif l.get("top"):
            new_state, by = "Mandatory", "top"
        else:
            new_state, by = l["cur_state"], ""
        conn.execute("UPDATE seq_lines SET new_seq=?, new_state=?, state_by=?, outcome='' WHERE id=?",
                     (n, new_state, by, l["id"]))
    nf = [l["outlet_code"] for l in lines if l["in_sfa"] is False]
    if nf:
        problems.append({"level": "warn", "from_sfa": True,
                         "text": f"{len(nf)} outlet(s) in the sheet aren't on this route in SFA and will be skipped: "
                                 + ", ".join(nf[:8]) + ("…" if len(nf) > 8 else "")})
    if extra:
        problems.append({"level": "warn", "from_sfa": True,
                         "text": f"{len(extra)} outlet(s) are on the route in SFA but not in the sheet — they go after "
                                 "the rep's outlets, in their current order."})
    if not on:
        problems.append({"level": "error", "from_sfa": True,
                         "text": "None of the sheet's outlets were found on this route in SFA."})
    conn.execute("UPDATE seq_sheets SET problems=?, read_at=? WHERE id=?", (json.dumps(problems), _now(), sid))
    conn.commit()
    conn.close()
    return get_sheet(sid)


def set_states(sid, line_ids, state):
    """The user sets Visit State for one or more outlets ('Mandatory' / 'Optional', or 'reset' = back to the rule)."""
    s = get_sheet(sid)
    if not s or s["state"] not in ("ready", "received"):
        return False, "This sheet can't be changed any more."
    if state not in VISIT_STATES + ("reset",):
        return False, "Visit State must be Mandatory or Optional."
    ids = {int(i) for i in line_ids}
    conn = db.get_connection()
    for l in s["lines"]:
        if l["id"] not in ids or not l["in_sfa"]:
            continue
        if state == "reset":
            ns, by = ("Mandatory", "top") if l["top"] else (l["cur_state"], "")
        else:
            ns, by = state, "you"
        conn.execute("UPDATE seq_lines SET new_state=?, state_by=? WHERE id=?", (ns, by, l["id"]))
    conn.commit()
    conn.close()
    return True, ""


def changes(sid):
    """Lines to save in SFA: on the route, with a different sequence or Visit State, not saved yet
    (cur_seq / cur_state keep the values SFA had before, for the record)."""
    s = get_sheet(sid)
    return [l for l in s["lines"] if l["in_sfa"] and l["edit_id"] and l["outcome"] not in ("updated", "unchanged") and
            ((l["new_seq"] is not None and str(l["new_seq"]) != (l["cur_seq"] or "")) or
             (l["new_state"] and l["new_state"] != l["cur_state"]))]


def approve(sid, by="you"):
    s = get_sheet(sid, lines=False)
    if not s or s["state"] != "ready":
        return False, "Only a sheet waiting for approval can be approved."
    if any(p["level"] == "error" for p in s["problems"]):
        return False, "This sheet has a problem that stops it being applied."
    set_sheet(sid, state="approved", decided_at=_now(), decided_by=by)
    return True, ""


def discard(sid):
    s = get_sheet(sid, lines=False)
    if not s or s["state"] in ("applying",):
        return False, "This sheet is being saved in SFA right now."
    if s["state"] in ("done", "partial"):
        return False, "This sheet was already applied."
    set_sheet(sid, state="discarded", decided_at=_now(), decided_by="you", message="Discarded by you.")
    return True, ""


def finish_sheet(sid, dry_run, run_id):
    s = get_sheet(sid)
    sm = s["summary"]
    if dry_run:
        state, msg = "ready", f"Dry run: {sm['would_update']} outlet(s) would be saved" + \
                              (f", {sm['failed']} would fail" if sm["failed"] else "") + " — nothing was saved in SFA."
    elif sm["failed"] and sm["updated"]:
        state, msg = "partial", f"{sm['updated']} outlet(s) saved in SFA, {sm['failed']} failed."
    elif sm["failed"]:
        state, msg = "failed", f"No outlet could be saved ({sm['failed']} failed)."
    else:
        state, msg = "done", (f"{sm['updated']} outlet(s) saved in SFA." if sm["updated"] else
                              "Nothing needed changing — SFA already matches the sheet.")
    set_sheet(sid, state=state, message=msg, applied_at=None if dry_run else _now(), dry_run=dry_run, run_id=run_id)
    return state, msg


def mark_interrupted():
    conn = db.get_connection()
    conn.execute("UPDATE seq_runs SET status='interrupted', finished_at=? WHERE status='running'", (_now(),))
    conn.execute("UPDATE seq_sheets SET state='approved', message='Chimera closed while saving — it will continue "
                 "with the outlets not saved yet.' WHERE state='applying'")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def start_run(kind, trigger):
    conn = db.get_connection()
    cur = conn.execute("INSERT INTO seq_runs (kind, started_at, trigger, status) VALUES (?,?,?,'running')",
                       (kind, _now(), trigger))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def finish_run(rid, status, message="", log=""):
    conn = db.get_connection()
    conn.execute("UPDATE seq_runs SET finished_at=?, status=?, message=?, log=? WHERE id=?",
                 (_now(), status, message, log[-200_000:], rid))
    conn.commit()
    conn.close()


def list_runs(limit=15):
    conn = db.get_connection()
    rows = conn.execute("SELECT id, kind, started_at, finished_at, trigger, status, message FROM seq_runs "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(zip(["id", "kind", "started_at", "finished_at", "trigger", "status", "message"], r)) for r in rows]


def get_run_log(rid):
    conn = db.get_connection()
    r = conn.execute("SELECT log FROM seq_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    return (r[0] if r else "") or ""
