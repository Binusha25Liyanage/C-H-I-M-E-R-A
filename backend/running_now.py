"""
running_now.py
The "Running now" screen: every Chimera process in one place, live.

  running      each process that is working right now — SFA report downloads
               (and building the reports), WhatsApp (reports, reminders, tests),
               distributor loadings / distributor list / add loading,
               itinerary reads, rep sheets — with its steps, progress, log and
               its own Stop.
  coming up    what the schedule will do in the next hours: report runs and
               other jobs, WhatsApp reminders, the 23:59 itinerary preparation.
  just ended   the last few things that finished (from the Activity Monitor).

Read only, except stop(pid), which stops one process immediately.
"""

from datetime import datetime, timedelta

import automation_store as AS

PROCESSES = [
    # id, label, module
    ("reports", "SFA reports", "sfa_automation"),
    ("whatsapp", "WhatsApp", "whatsapp_sender"),
    ("loadings", "Distributor loadings", "loading_automation"),
    ("itinerary", "Itineraries", "itinerary_automation"),
    ("outlets", "Outlet updates (rep sheets)", "sequence_automation"),
    ("attendance", "Rep attendance", "rep_attendance_automation"),
]
KIND_WORDS = {
    "loadings": {"prepare": "Reading loadings for approval", "confirm": "Confirming loadings in SFA",
                 "sync": "Updating the distributor list", "add": "Adding a loading"},
    "itinerary": {"itinerary": "Reading itineraries", "detect": "Finding territories"},
    "outlets": {"routes": "Reading SFA's route list", "sequence": "Rep sheets — reading / saving in SFA"},
    "attendance": {"attendance": "Rep attendance — download, images, WhatsApp"},
    "whatsapp": {"send": "Sending reports", "reminder": "Sending reminders", "attendance": "Sending rep attendance", "test": "Sending a test message",
                 "link": "Linking WhatsApp", "chats": "Loading your chats"},
}


def _mod(name):
    import importlib
    return importlib.import_module(name)


def _secs(since):
    try:
        return max(0, int((datetime.now() - datetime.fromisoformat(since)).total_seconds()))
    except (TypeError, ValueError):
        return None


def _snapshot(pid, label, modname):
    m = _mod(modname)
    if pid == "whatsapp":
        s = m.WA.snapshot()
        kind = s.get("action") or ""
        lanes = []
    else:
        s = m.LIVE.snapshot()
        kind = s.get("kind") or ""
        lanes = s.get("lanes") or []
    done = sum(1 for l in lanes if l.get("status") in ("done", "failed", "cancelled", "skipped", "empty"))
    title = KIND_WORDS.get(pid, {}).get(kind) or (label if pid != "reports" else
                                                 f"SFA reports{(' for ' + s['report_date']) if s.get('report_date') else ''}")
    return {"id": pid, "label": label, "title": title, "kind": kind, "active": bool(s.get("active")),
            "phase": s.get("phase") or "", "trigger": s.get("trigger") or "", "slot": s.get("slot") or "",
            "started_at": s.get("started_at"), "elapsed": _secs(s.get("started_at")) if s.get("active") else None,
            "run_id": s.get("run_id"), "dry_run": bool(s.get("dry_run")),
            "lanes": [{"name": l.get("name"), "status": l.get("status"), "message": l.get("message") or "",
                       "steps": l.get("steps") or {}, "elapsed": l.get("elapsed")} for l in lanes],
            "progress": (round(100 * done / len(lanes)) if lanes else None), "done": done, "total": len(lanes),
            "log": (s.get("log") or [])[-120:], "result": s.get("result"),
            "step_labels": getattr(m, "STEP_LABELS", {}) if pid != "whatsapp" else {}}


def running():
    out = []
    for pid, label, mod in PROCESSES:
        try:
            out.append(_snapshot(pid, label, mod))
        except Exception as e:
            out.append({"id": pid, "label": label, "title": label, "active": False, "error": str(e), "lanes": [],
                        "log": [], "result": None})
    return out


def coming_up(hours=6, now=None):
    now = now or datetime.now()
    end = now + timedelta(hours=hours)
    s = AS.get_automation_settings()
    items = []
    try:
        import scheduler
        for o in scheduler.upcoming(now, days=1, settings=s):
            if o["at"] > end:
                break
            items.append({"at": o["at"].isoformat(timespec="minutes"), "what": o["job"]["name"],
                          "kind": "job", "detail": o["job"].get("type_label") or o["job"]["job_type"]})
    except Exception:
        pass
    try:
        import reminders
        for d in (now.date(), (now + timedelta(days=1)).date()):
            for it in reminders.plan_day(d, s):
                if now < it["at"] <= end and not it["done"]:
                    items.append({"at": it["at"].isoformat(timespec="minutes"), "kind": "reminder",
                                  "what": f"Reminder — {it['name']}",
                                  "detail": (f"before the {reminders.fmt_time(it['report_time'])} report · " if it["report_time"] else "")
                                            + ", ".join(it["chats"][:3]) + (" …" if len(it["chats"]) > 3 else ""),
                                  "skip": it["skip"]})
    except Exception:
        pass
    try:
        if s.get("auto_outlet_updates", "1") == "1":
            h, m = (int(x) for x in (s.get("ou_prepare_time") or "23:59").split(":"))
            prep = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if prep <= now:
                prep += timedelta(days=1)
            if prep <= end:
                items.append({"at": prep.isoformat(timespec="minutes"), "kind": "itinerary",
                              "what": "Itineraries — prepare tomorrow's routes", "detail": "Outlet Updates"})
    except Exception:
        pass
    items.sort(key=lambda x: x["at"])
    for x in items:
        x["in_seconds"] = int((datetime.fromisoformat(x["at"]) - now).total_seconds())
    return items[:25]


def just_ended(limit=8):
    try:
        import activity
        ev = [e for e in activity.events(datetime.now().date().isoformat(), datetime.now().date().isoformat())
              if e["status"] != "running"]
        return ev[:limit]
    except Exception:
        return []


def overview():
    s = AS.get_automation_settings()
    procs = running()
    return {"now": datetime.now().isoformat(timespec="seconds"), "paused": s.get("paused") == "1",
            "running": procs, "active_count": sum(1 for p in procs if p["active"]),
            "coming_up": coming_up(), "just_ended": just_ended()}


def stop(pid):
    for p, label, mod in PROCESSES:
        if p == pid:
            m = _mod(mod)
            try:
                was = m.force_stop()
            except Exception as e:
                return {"ok": False, "message": f"Couldn't stop {label}: {e}"}
            if pid == "reports":
                try:
                    m.LIVE.cancel()
                except Exception:
                    pass
            return {"ok": True, "message": f"{label}: stopped." if was else f"{label} wasn't running."}
    return {"ok": False, "message": "Unknown process."}
