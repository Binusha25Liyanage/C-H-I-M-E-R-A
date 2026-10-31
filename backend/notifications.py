"""
notifications.py
One event feed built from what Chimera already records — SFA automation
runs, report runs (incl. previews waiting for approval) and WhatsApp
deliveries. Used by the bell (unread = newer than the last time the user
opened it) and by the Overview's Recent Activity and Data Source Health.
"""

from datetime import date, datetime

import automation_store as AS
import db
import report_pipeline
import sfa_store


def _ago(ts):
    try:
        secs = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
    except Exception:
        return ""
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} d ago"


def events(limit=30):
    ev = []
    for r in AS.list_runs(20):
        if r["status"] == "running":
            continue
        level = {"success": "success", "partial": "warn", "failed": "error", "cancelled": "info",
                 "interrupted": "warn"}.get(r["status"], "info")
        failed = [l["name"] for l in r["lanes"] if l.get("status") not in ("done", "empty")]
        ev.append({"time": r["finished_at"] or r["started_at"], "level": level, "source": "automation",
                   "title": f"SFA download {r['status']}" + (f" ({r['slot']})" if r.get("slot") else ""),
                   "text": (r["message"] or "") + (f" Failed: {', '.join(failed)}." if failed and level != "success" else ""),
                   "action": "automation"})
    for r in report_pipeline.get_runs(20):
        if r["status"] == "pending_review":
            ev.append({"time": r["finished_at"] or r["started_at"], "level": "warn", "source": "reports",
                       "title": "Reports waiting for your approval",
                       "text": f"Report date {r['report_date']} — preview them, then save or discard.",
                       "action": f"preview:{r['id']}", "sticky": True})
        elif r["status"] in ("success", "partial", "failed"):
            oks = [x["label"] for x in r["summary"].get("reports", []) if x.get("ok")]
            ev.append({"time": r["finished_at"] or r["started_at"],
                       "level": {"success": "success", "partial": "warn", "failed": "error"}[r["status"]],
                       "source": "reports", "title": f"Reports {r['status']} — {r['report_date']}",
                       "text": ", ".join(oks) if oks else (r["summary"].get("error") or "No report could be built."),
                       "action": "daily"})
    try:
        import whatsapp_sender
        for d in whatsapp_sender.list_deliveries(20):
            ev.append({"time": d["sent_at"], "level": "success" if d["status"] == "sent" else
                       ("error" if d["status"] == "failed" else "info"), "source": "whatsapp",
                       "title": f"WhatsApp {d['status']}: {d['chat_name']}",
                       "text": (f"{d['files']} image(s). " if d["files"] else "") + (d["message"] or ""),
                       "action": "whatsapp"})
    except Exception:
        pass
    try:
        import loading_store as LS
        pend = LS.pending_items()
        if pend:
            ev.append({"time": max(p["prepared_at"] or "" for p in pend), "level": "warn", "source": "loadings",
                       "title": f"{len(pend)} loading(s) waiting for your approval",
                       "text": ", ".join(p["distributor_name"] for p in pend[:6]) + (" …" if len(pend) > 6 else ""),
                       "action": "loadings-approve", "sticky": True})
        for r in LS.list_runs(10):
            if r["status"] == "running":
                continue
            ev.append({"time": r["finished_at"] or r["started_at"],
                       "level": {"done": "success", "failed": "error", "cancelled": "info"}.get(r["status"], "warn"),
                       "source": "loadings", "title": f"Distributor loadings — {'prepared' if r['kind'] == 'prepare' else 'confirmed'}",
                       "text": r["message"] or "", "action": "loadings"})
    except Exception:
        pass
    ev.sort(key=lambda e: e["time"] or "", reverse=True)
    seen = AS.get_automation_settings().get("notif_seen_at", "")
    for e in ev[:limit]:
        e["unread"] = (e["time"] or "") > seen or bool(e.get("sticky"))
        e["ago"] = _ago(e["time"])
    return ev[:limit]


def summary():
    ev = events(30)
    return {"events": ev, "unread": sum(1 for e in ev if e["unread"])}


def mark_read():
    AS.save_automation_settings({"notif_seen_at": datetime.now().isoformat(timespec="seconds")})


def data_health(day=None):
    """Freshness of each SFA input for a day (default today)."""
    day = day or date.today().isoformat()
    st = sfa_store.get_dataset_status(day)
    out = []
    for d in st["datasets"]:
        when = d["imported_at"] or ""
        out.append({"label": d["label"], "loaded": d["loaded"], "rows": d["rows"],
                    "generated_at": d["generated_at"], "imported_at": when, "ago": _ago(when) if when else "",
                    "warnings": d["warnings"]})
    return {"date": day, "datasets": out}
