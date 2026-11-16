"""
overview_kpis.py
The four boxes at the top of the Overview, from the SFA data Chimera reads by
itself (nothing to load by hand):

  logged_in     territories logged in to SFA today, of your working territories
                (Rep Attendance report)
  distributors  distributors active in SFA (Distributor Loadings › distributor list)
  invoices      invoices today (the SFA report downloads), with their value
  last_sync     the last time Chimera read anything from SFA, and what

When today has nothing yet, the most recent day with data is shown and marked.
"""

from datetime import date, datetime

import db


def _q(sql, args=()):
    conn = db.get_connection()
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:          # a table that doesn't exist yet on an older install
        return []
    finally:
        conn.close()


def _day_label(d, today):
    if d == today:
        return "today"
    try:
        x = date.fromisoformat(d)
        return f"{x.day} {x:%b}"
    except (TypeError, ValueError):
        return d or ""


def logged_in(today):
    import rep_attendance as R
    r = _q("SELECT MAX(day) FROM att_days WHERE day<=?", (today,))
    day = r[0][0] if r and r[0][0] else None
    if not day:
        return {"value": None, "of": None, "day": None, "foot": "No rep attendance read yet"}
    d = R.get_day(day)
    c = d["counts"]
    when = (d["meta"] or {}).get("read_at", "")[11:16]
    return {"value": c["logged_in"], "of": c["working"] or None, "day": day, "is_today": day == today,
            "not_logged_in": c["not_logged_in"] if c["working"] else None,
            "foot": (f"{c['not_logged_in']} not logged in · " if c["working"] else "")
                    + (f"as of {when}" if day == today else f"latest: {_day_label(day, today)}")}


def distributors():
    rows = _q("SELECT name FROM distributors")
    if not rows:
        return {"value": None, "foot": "Distributor list not read from SFA yet"}
    try:
        import loading_store as L
        ds = L.list_distributors()
        active = sum(1 for d in ds if not d["blocked"])
        daily = sum(1 for d in ds if d.get("frequency") == "daily" and not d["blocked"])
        last = L.last_sync()
    except Exception:
        active, daily, last = len(rows), 0, None
    when = ""
    if last and last.get("finished_at"):
        w = last["finished_at"]
        when = f" · synced {w[11:16]}" if w[:10] == date.today().isoformat() else f" · synced {_day_label(w[:10], '')}"
    return {"value": active, "foot": f"{daily} with daily loadings{when}" if daily else f"active in SFA{when}"}


def _money(v):
    return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{v:,.0f}"


def invoices(today):
    r = _q("SELECT MAX(bill_date) FROM sfa_bills WHERE kind='INV' AND bill_date<=?", (today,))
    day = r[0][0] if r and r[0][0] else None
    if not day:
        return {"value": None, "foot": "No invoices downloaded from SFA yet"}
    n, amount, terr = _q("SELECT COUNT(*), COALESCE(SUM(amount),0), COUNT(DISTINCT territory) FROM sfa_bills "
                         "WHERE kind='INV' AND bill_date=?", (day,))[0]
    so = _q("SELECT COUNT(*) FROM sfa_bills WHERE kind='SO' AND bill_date=?", (day,))[0][0]
    imp = _q("SELECT MAX(imported_at) FROM sfa_datasets WHERE dataset='invoices' AND data_date=?", (day,))
    when = imp[0][0][11:16] if imp and imp[0][0] else ""
    foot = f"Rs {_money(amount)} · {terr} territories"
    if so:
        foot += f" · {so} SOs"
    foot += f" · as of {when}" if day == today and when else (f" · latest: {_day_label(day, today)}" if day != today else "")
    return {"value": n, "amount": amount, "day": day, "is_today": day == today, "foot": foot}


def last_sync(today):
    cands = []
    for sql, what in (("SELECT MAX(imported_at) FROM sfa_datasets", "SFA reports"),
                      ("SELECT MAX(read_at) FROM att_days", "Rep attendance"),
                      ("SELECT MAX(finished_at) FROM loading_runs WHERE status IN ('done','success')", "Distributor loadings"),
                      ("SELECT MAX(finished_at) FROM seq_runs WHERE status='done'", "Outlet sequences")):
        r = _q(sql)
        if r and r[0][0]:
            cands.append((r[0][0], what))
    if not cands:
        return {"value": None, "foot": "Chimera hasn't read anything from SFA yet"}
    at, what = max(cands)
    d = at[:10]
    return {"value": at[11:16] if d == today else _day_label(d, today), "at": at, "what": what,
            "foot": what + ("" if d == today else f" · {at[11:16]}")}


def overview(now=None):
    today = (now or datetime.now()).date().isoformat()
    out = {}
    for k, fn in (("logged_in", lambda: logged_in(today)), ("distributors", distributors),
                  ("invoices", lambda: invoices(today)), ("last_sync", lambda: last_sync(today))):
        try:
            out[k] = fn()
        except Exception as e:     # one box failing never blanks the others
            out[k] = {"value": None, "foot": f"Couldn't read: {e}"}
    return out
