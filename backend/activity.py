"""
activity.py
Activity Monitor: one timeline of everything Chimera did in a period, read
from the records each part already keeps (nothing new is logged here):

  downloads     SFA report downloads            automation_runs
  reports       reports built (SKU/Pivot/...)   report_runs
  whatsapp      deliveries to WhatsApp chats    wa_deliveries
  loadings      prepare / confirm / add runs    loading_runs + loading_items
  distributors  distributor list updates        loading_runs (kind 'sync')
  itinerary     itinerary reads / detect        ou_runs
  schedules     scheduled job firings           schedule_fires

Every event: {time, process, title, detail, status ok|warn|fail|info|running,
trigger, ref {source, id}}. Loadings are also listed one by one
(distributor, loading number, what happened, quantity, amount).
"""

import json
import os
import re
from datetime import date, datetime, timedelta

import db

PROCESSES = {
    "downloads": "SFA downloads", "reports": "Reports built", "whatsapp": "WhatsApp", "loadings": "Distributor loadings",
    "distributors": "Distributor list", "itinerary": "Itineraries", "outlets": "Outlet updates", "schedules": "Schedules",
}
STATE_LABEL = {
    "pending_approval": "Read — waiting for approval", "nothing_pending": "Nothing pending", "failed": "Read failed",
    "confirming": "Confirming", "confirmed": "Confirmed", "confirm_failed": "Confirm failed", "changed": "Changed in SFA — not confirmed",
    "skipped": "Skipped by you", "superseded": "Replaced by a newer read", "added": "Added in SFA", "dry_run": "Add — dry run",
}


def _status(s):
    s = (s or "").lower()
    if s in ("success", "done", "sent", "confirmed", "ok", "added"):
        return "ok"
    if s in ("failed", "error", "confirm_failed", "interrupted", "changed"):
        return "fail"
    if s in ("running", "pending_review", "confirming", "pending_approval"):
        return "running" if s in ("running", "confirming") else "warn"
    if s in ("partial", "cancelled", "skipped", "missing", "busy"):
        return "warn"
    return "info"


def _range(d_from, d_to):
    a = (d_from or date.today().isoformat())[:10]
    b = (d_to or a)[:10]
    if b < a:
        a, b = b, a
    return a, b, f"{a}T00:00:00", f"{b}T23:59:59"


def _q(sql, args=()):
    conn = db.get_connection()
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        return []                     # a part of Chimera that isn't set up yet (table missing)
    finally:
        conn.close()


def _j(text, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _num(v):
    try:
        return float(str(v or 0).replace(",", ""))
    except ValueError:
        return 0.0


def events(d_from=None, d_to=None):
    a, b, lo, hi = _range(d_from, d_to)
    out = []
    for rid, st, fin, rd, trig, slot, status, lanes, msg in _q(
            "SELECT id, started_at, finished_at, report_date, trigger, slot, status, lanes, message FROM automation_runs "
            "WHERE started_at BETWEEN ? AND ? ORDER BY started_at", (lo, hi)):
        ls = _j(lanes, [])
        ok = sum(1 for l in ls if isinstance(l, dict) and l.get("status") in ("done", "empty"))
        out.append({"time": st, "process": "downloads", "status": _status(status), "trigger": trig + (f" {slot}" if slot else ""),
                    "title": f"SFA downloads for {rd}" + (f" — {ok}/{len(ls)} reports" if ls else ""),
                    "detail": msg or "", "ref": {"source": "automation", "id": rid}})
    for rid, st, fin, rd, trig, status, folder, summ in _q(
            "SELECT id, started_at, finished_at, report_date, trigger, status, folder, summary FROM report_runs "
            "WHERE started_at BETWEEN ? AND ? ORDER BY started_at", (lo, hi)):
        s = _j(summ, {})
        names = [r.get("label") or r.get("kind") for r in s.get("reports", []) if isinstance(r, dict) and r.get("ok")]
        label = {"pending_review": "waiting for approval", "discarded": "discarded"}.get(status, status)
        out.append({"time": st, "process": "reports", "status": _status(status), "trigger": trig,
                    "title": f"Reports for {rd} — {label}", "detail": ", ".join(n for n in names if n) or (s.get("review_note") or ""),
                    "ref": {"source": "report", "id": rid}})
    for did, rr, chat, files, status, msg, at in _q(
            "SELECT id, report_run_id, chat_name, files, status, message, sent_at FROM wa_deliveries "
            "WHERE sent_at BETWEEN ? AND ? ORDER BY sent_at", (lo, hi)):
        out.append({"time": at, "process": "whatsapp", "status": _status(status), "trigger": "",
                    "title": f"WhatsApp → {chat}: {files} file(s) {status}", "detail": msg or "",
                    "ref": {"source": "whatsapp", "id": did}})
    for rid, kind, st, fin, trig, status, msg in _q(
            "SELECT id, kind, started_at, finished_at, trigger, status, message FROM loading_runs "
            "WHERE started_at BETWEEN ? AND ? ORDER BY started_at", (lo, hi)):
        proc = "distributors" if kind == "sync" else "loadings"
        title = {"prepare": "Loadings read (prepare)", "confirm": "Loadings confirmed in SFA", "add": "Add loading",
                 "sync": "Distributor list update"}.get(kind, kind)
        out.append({"time": st, "process": proc, "status": _status(status), "trigger": trig, "title": title,
                    "detail": msg or "", "ref": {"source": "loading", "id": rid}})
    for rid, kind, st, trig, tday, status, msg in _q(
            "SELECT id, kind, started_at, trigger, target_day, status, message FROM ou_runs "
            "WHERE started_at BETWEEN ? AND ? ORDER BY started_at", (lo, hi)):
        out.append({"time": st, "process": "itinerary", "status": _status(status), "trigger": trig,
                    "title": ("Itinerary read" if kind == "itinerary" else "Territories detected") + (f" — for {tday}" if tday else ""),
                    "detail": msg or "", "ref": {"source": "itinerary", "id": rid}})
    # Outlet updates: rep sheets received / decided / saved, and the SFA runs behind them
    src = {"upload": "uploaded", "whatsapp": "from WhatsApp", "gsheet": "from Google Sheets"}
    for (sid, rec, source, sender, fname, route, code, terr, state, msg, dec, by, applied, dry) in _q(
            "SELECT id, received_at, source, sender, file_name, route_name, route_code, territory, state, message, decided_at, "
            "decided_by, applied_at, dry_run FROM seq_sheets WHERE received_at BETWEEN ? AND ? OR decided_at BETWEEN ? AND ? "
            "OR applied_at BETWEEN ? AND ? ORDER BY id", (lo, hi, lo, hi, lo, hi)):
        what = f"{route} ({code})" if route else fname
        if lo <= (rec or "") <= hi:
            bad = state == "rejected"
            out.append({"time": rec, "process": "outlets", "status": "fail" if bad else "info",
                        "trigger": src.get(source, source) + (f" · {sender}" if sender else ""),
                        "title": f"Rep sheet {'refused' if bad else 'received'} — {what}",
                        "detail": (msg if bad else f"{terr} · {fname}"), "ref": {"source": "sheet", "id": sid}})
        if dec and lo <= dec <= hi and state != "superseded":
            out.append({"time": dec, "process": "outlets", "status": "info" if state != "discarded" else "warn",
                        "trigger": "you" if by == "you" else "automatic",
                        "title": (f"Discarded — {what}" if state == "discarded" else
                                  f"Approved {'by you' if by == 'you' else '(approval not required)'} — {what}"),
                        "detail": "", "ref": {"source": "sheet", "id": sid}})
        if applied and lo <= applied <= hi:
            out.append({"time": applied, "process": "outlets", "status": _status({"done": "success", "partial": "partial"}.get(state, state)),
                        "trigger": "", "title": f"Saved in SFA — {what}", "detail": msg or "", "ref": {"source": "sheet", "id": sid}})
    for rid, kind, st, trig, status, msg in _q("SELECT id, kind, started_at, trigger, status, message FROM seq_runs "
                                               "WHERE started_at BETWEEN ? AND ? ORDER BY started_at", (lo, hi)):
        title = {"routes": "Route list update (View Route)", "read": "Rep sheets — SFA read for previews",
                 "apply": "Rep sheets — SFA run"}.get(kind, kind)
        out.append({"time": st, "process": "outlets", "status": _status(status), "trigger": trig, "title": title,
                    "detail": msg or "", "ref": {"source": "seq_run", "id": rid}})
    jobs = {r[0]: r[1] for r in _q("SELECT id, name FROM schedule_jobs")}
    for jid, day, slot, at, trig, outcome in _q(
            "SELECT job_id, day, slot, fired_at, trigger, outcome FROM schedule_fires WHERE fired_at BETWEEN ? AND ? "
            "ORDER BY fired_at", (lo, hi)):
        o = outcome or ""
        st = "fail" if o.lower().startswith(("fail", "error")) else "warn" if o.lower().startswith(("skip", "missed")) else "ok"
        out.append({"time": at, "process": "schedules", "status": st, "trigger": trig,
                    "title": f"{jobs.get(jid, f'Job #{jid}')} — {slot}", "detail": o, "ref": {"source": "schedule", "id": jid}})
    out.sort(key=lambda e: e["time"] or "", reverse=True)
    return out


def loadings(d_from=None, d_to=None):
    """Each loading Chimera touched in the period: read, confirmed, skipped, changed… and Add Loading attempts."""
    a, b, lo, hi = _range(d_from, d_to)
    rows = []
    for (iid, rid, dist, state, num, rep, ldate, ltime, totals, msg, prep, dec, conf) in _q(
            "SELECT i.id, i.run_id, i.distributor_name, i.state, i.loading_number, i.rep, i.loading_date, i.loading_time, "
            "i.totals, i.message, i.prepared_at, i.decided_at, i.confirmed_at FROM loading_items i "
            "WHERE COALESCE(i.confirmed_at, i.decided_at, i.prepared_at, '') BETWEEN ? AND ? ORDER BY i.id", (lo, hi)):
        t = _j(totals, {})
        rows.append({"time": conf or dec or prep or "", "distributor": dist, "loading_number": num, "rep": rep,
                     "loading_date": f"{ldate} {ltime}".strip(), "state": state, "action": STATE_LABEL.get(state, state),
                     "status": _status(state), "qty": _num(t.get("qty")), "amount": _num(t.get("amount")),
                     "message": msg or "", "run_id": rid})
    for rid, st, status, msg in _q("SELECT id, started_at, status, message FROM loading_runs WHERE kind='add' "
                                   "AND started_at BETWEEN ? AND ?", (lo, hi)):
        dist = (msg or "").split(":")[0]
        dry = "dry run" in (msg or "")
        m = re.search(r"loading (?:number )?([^\s;)]+)", msg or "")
        num = m.group(1) if m and m.group(1) != "?" else ""
        rows.append({"time": st, "distributor": dist, "loading_number": num, "rep": "", "loading_date": "",
                     "state": "dry_run" if dry else "added", "action": STATE_LABEL["dry_run" if dry else "added"],
                     "status": "info" if dry else "ok", "qty": 0.0, "amount": 0.0, "message": msg or "", "run_id": rid})
    rows.sort(key=lambda r: r["time"] or "", reverse=True)
    return rows


OUTLET_OUTCOME = {"updated": "Saved in SFA", "would_update": "Dry run — would be saved", "failed": "Failed",
                  "unchanged": "Already matched SFA", "not_found": "Not on the route in SFA"}


def outlet_changes(d_from=None, d_to=None):
    """Each outlet a rep sheet changed (or tried to) in the period: old → new sequence and Visit State."""
    a, b, lo, hi = _range(d_from, d_to)
    rows = []
    for (code, name, rep_seq, top, cur_seq, cur_state, new_seq, new_state, by, outcome, msg, done, remark, demand,
         sid, route, rcode, terr, applied, rec) in _q(
            "SELECT l.outlet_code, l.outlet_name, l.rep_seq, l.top, l.cur_seq, l.cur_state, l.new_seq, l.new_state, "
            "l.state_by, l.outcome, l.message, l.done_at, l.remark, l.demand, s.id, s.route_name, s.route_code, s.territory, "
            "s.applied_at, s.received_at FROM seq_lines l JOIN seq_sheets s ON s.id=l.sheet_id "
            "WHERE l.outcome IN ('updated','would_update','failed','unchanged','not_found') "
            "AND COALESCE(l.done_at, s.applied_at, s.received_at) BETWEEN ? AND ? ORDER BY l.id", (lo, hi)):
        rows.append({"time": done or applied or rec, "territory": terr, "route": route, "route_code": rcode, "sheet": sid,
                     "outlet_code": code, "outlet_name": name, "old_seq": cur_seq, "new_seq": new_seq,
                     "old_state": cur_state, "new_state": new_state, "top": bool(top), "state_by": by,
                     "outcome": outcome, "action": OUTLET_OUTCOME.get(outcome, outcome),
                     "status": {"updated": "ok", "failed": "fail", "would_update": "info"}.get(outcome, "info"),
                     "remark": remark, "demand": demand, "message": msg})
    rows.sort(key=lambda r: (r["time"] or "", -(r["new_seq"] or 0)), reverse=True)
    return rows


def overview(d_from=None, d_to=None, process="all", q=""):
    a, b, _lo, _hi = _range(d_from, d_to)
    ev = events(a, b)
    ql = (q or "").strip().lower()
    if ql:
        ev = [e for e in ev if ql in f"{e['title']} {e['detail']} {e['trigger']}".lower()]
    by_process = {p: sum(1 for e in ev if e["process"] == p) for p in PROCESSES}   # counts for every chip, whatever is chosen
    if process and process != "all":
        ev = [e for e in ev if e["process"] == process]
    lds = loadings(a, b)
    if ql:
        lds = [r for r in lds if ql in f"{r['distributor']} {r['loading_number']} {r['rep']} {r['action']}".lower()]
    ocs = outlet_changes(a, b)
    if ql:
        ocs = [r for r in ocs if ql in f"{r['outlet_code']} {r['outlet_name']} {r['route']} {r['route_code']} {r['territory']}".lower()]
    days, d = [], date.fromisoformat(a)
    while d <= date.fromisoformat(b) and len(days) < 93:
        days.append(d.isoformat())
        d += timedelta(days=1)
    per_day = {x: {p: 0 for p in PROCESSES} for x in days}
    for e in ev:
        k = (e["time"] or "")[:10]
        if k in per_day:
            per_day[k][e["process"]] += 1
    conf = [r for r in lds if r["state"] == "confirmed"]
    kpis = {"events": len(ev), "ok": sum(e["status"] == "ok" for e in ev), "failed": sum(e["status"] == "fail" for e in ev),
            "warn": sum(e["status"] == "warn" for e in ev),
            "by_process": by_process,
            "loadings_confirmed": len(conf), "loadings_amount": round(sum(r["amount"] for r in conf), 2),
            "loadings_qty": round(sum(r["qty"] for r in conf), 2),
            "loadings_added": sum(1 for r in lds if r["state"] == "added"),
            "whatsapp_sent": sum(1 for e in ev if e["process"] == "whatsapp" and e["status"] == "ok"),
            "outlets_saved": sum(1 for r in ocs if r["outcome"] == "updated"),
            "outlets_failed": sum(1 for r in ocs if r["outcome"] == "failed"),
            "outlets_mandatory": sum(1 for r in ocs if r["outcome"] == "updated" and r["new_state"] == "Mandatory"
                                     and r["old_state"] != "Mandatory"),
            "sheets_received": sum(1 for e in ev if e["process"] == "outlets" and e["title"].startswith("Rep sheet"))}
    return {"from": a, "to": b, "process": process or "all", "processes": PROCESSES, "kpis": kpis,
            "per_day": [{"day": x, **per_day[x]} for x in days], "events": ev[:1500], "loadings": lds[:1500], "outlets": ocs[:3000],
            "truncated": len(ev) > 1500}


def detail(source, rid):
    """Full text for one event (its log), for the details pop-up / Copy."""
    rid = int(rid)
    if source == "automation":
        try:
            import automation_store as AS
            return AS.get_run_log(rid) or ""
        except Exception:
            return ""
    if source == "loading":
        r = _q("SELECT log, message FROM loading_runs WHERE id=?", (rid,))
        items = _q("SELECT distributor_name, state, loading_number, message FROM loading_items WHERE run_id=? ORDER BY id", (rid,))
        head = "\n".join(f"{d} — {STATE_LABEL.get(s, s)} {n} {m}".rstrip() for d, s, n, m in items)
        return ((r[0][1] or "") + ("\n\n" + head if head else "") + ("\n\n" + r[0][0] if r and r[0][0] else "")) if r else ""
    if source == "itinerary":
        r = _q("SELECT log, message FROM ou_runs WHERE id=?", (rid,))
        return (r[0][0] or r[0][1] or "") if r else ""
    if source == "report":
        r = _q("SELECT summary, folder, status FROM report_runs WHERE id=?", (rid,))
        if not r:
            return ""
        s = _j(r[0][0], {})
        lines = [f"Status: {r[0][2]}", f"Folder: {r[0][1] or ''}"]
        if s.get("review_note"):
            lines.append(s["review_note"])
        for x in s.get("reports", []):
            if isinstance(x, dict):
                lines.append(f"- {x.get('label') or x.get('kind')}: {'ok' if x.get('ok') else x.get('reason') or 'failed'}")
        for x in s.get("imports", []):
            if isinstance(x, dict):
                lines.append(f"  import {os.path.basename(str(x.get('file') or x.get('dataset') or ''))}: "
                             f"{'ok' if x.get('ok') else x.get('message') or 'failed'}")
        return "\n".join(lines)
    if source == "sheet":
        import sequence_store as SS
        s = SS.get_sheet(rid)
        if not s:
            return ""
        sm = s["summary"]
        out = [f"Sheet #{s['id']} — {s['route_name']} ({s['route_code']}), {s['territory']}",
               f"From {s['source']}{' · ' + s['sender'] if s['sender'] else ''}: {s['file_name']} at {s['received_at'].replace('T', ' ')}",
               f"Rep: {s['rep']}   Distributor: {s['distributor']}",
               f"State: {s['state']}{' (dry run)' if s['dry_run'] else ''} — {s['message']}",
               f"{sm['outlets']} outlets · {sm['top']} Top 15 · {sm['seq_changes']} sequence and {sm['state_changes']} Visit State "
               f"changes · saved {sm['updated']} · failed {sm['failed']}", ""]
        out += [f"[{p['level']}] {p['text']}" for p in s["problems"]]
        out.append("")
        for l in s["lines"]:
            if l["outcome"] or (l["in_sfa"] and (str(l["new_seq"]) != l["cur_seq"] or l["new_state"] != l["cur_state"])):
                out.append(f"{l['new_seq'] or '-':>3}  {l['outlet_code']:<16} {l['outlet_name'][:24]:<24} seq {l['cur_seq'] or '-'} → "
                           f"{l['new_seq'] or '-'}  {l['cur_state'] or '-'} → {l['new_state'] or '-'}  "
                           f"{OUTLET_OUTCOME.get(l['outcome'], 'waiting')}  {l['message']}")
        return "\n".join(out)
    if source == "seq_run":
        r = _q("SELECT log, message FROM seq_runs WHERE id=?", (rid,))
        return (r[0][0] or r[0][1] or "") if r else ""
    if source == "whatsapp":
        r = _q("SELECT chat_name, files, status, message, sent_at, report_run_id FROM wa_deliveries WHERE id=?", (rid,))
        return (f"{r[0][4]} — {r[0][0]}: {r[0][1]} file(s), {r[0][2]}\nReport run #{r[0][5]}\n{r[0][3] or ''}") if r else ""
    return ""


def export_excel(d_from=None, d_to=None, process="all", q="", folder=None):
    """Writes the period's activity (and loadings) to an Excel file; returns its path."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    ov = overview(d_from, d_to, process, q)
    folder = folder or os.path.join(os.path.expanduser("~"), "Chimera", "exports")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"Chimera_activity_{ov['from']}_to_{ov['to']}_{datetime.now():%H%M%S}.xlsx")
    wb = Workbook()
    head = PatternFill("solid", fgColor="B5121B")

    def sheet(ws, cols, rows, widths):
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), head, Alignment(vertical="center", wrap_text=True)
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Activity"
    sheet(ws, ["Time", "Process", "What", "Details", "Result", "Started by"],
          [[e["time"].replace("T", " "), PROCESSES.get(e["process"], e["process"]), e["title"], e["detail"],
            {"ok": "Success", "fail": "Failed", "warn": "Attention", "running": "Running", "info": "Info"}[e["status"]], e["trigger"]]
           for e in ov["events"]], [19, 18, 46, 70, 11, 14])
    ws2 = wb.create_sheet("Loadings")
    sheet(ws2, ["Time", "Distributor", "Loading number", "Rep", "Loading date", "What happened", "Quantity", "Amount", "Note"],
          [[r["time"].replace("T", " "), r["distributor"], r["loading_number"], r["rep"], r["loading_date"], r["action"],
            r["qty"], r["amount"], r["message"]] for r in ov["loadings"]], [19, 26, 24, 26, 18, 28, 10, 14, 60])
    for row in ws2.iter_rows(min_row=2, min_col=8, max_col=8):
        for c in row:
            c.number_format = "#,##0.00"
    ws4 = wb.create_sheet("Outlet updates")
    sheet(ws4, ["Time", "Territory", "Route", "Route code", "Outlet code", "Outlet", "Old seq", "New seq", "Visit State before",
                "Visit State after", "Top 15", "What happened", "Remark", "Demand product", "Note"],
          [[(r["time"] or "").replace("T", " "), r["territory"], r["route"], r["route_code"], r["outlet_code"], r["outlet_name"],
            r["old_seq"], r["new_seq"], r["old_state"], r["new_state"], "✓" if r["top"] else "", r["action"], r["remark"],
            r["demand"], r["message"]] for r in ov["outlets"]], [19, 16, 28, 12, 17, 24, 8, 8, 14, 14, 7, 24, 22, 22, 60])
    ws3 = wb.create_sheet("Summary")
    k = ov["kpis"]
    for line in [["Period", f"{ov['from']} to {ov['to']}"], ["Events", k["events"]], ["Successful", k["ok"]], ["Failed", k["failed"]],
                 ["Needs attention", k["warn"]], ["Loadings confirmed", k["loadings_confirmed"]],
                 ["Confirmed amount", k["loadings_amount"]], ["Loadings added", k["loadings_added"]],
                 ["WhatsApp deliveries", k["whatsapp_sent"]], ["Rep sheets received", k["sheets_received"]],
                 ["Outlets saved in SFA", k["outlets_saved"]], ["Outlets failed", k["outlets_failed"]]] + [[PROCESSES[p], n] for p, n in k["by_process"].items()]:
        ws3.append(line)
    ws3.column_dimensions["A"].width, ws3.column_dimensions["B"].width = 26, 26
    wb.save(path)
    return path
