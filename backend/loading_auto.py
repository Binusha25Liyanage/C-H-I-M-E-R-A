"""
loading_auto.py — "Auto add loadings" (Schedules › What to run, or Run now).

For each chosen distributor (territory), one after the other:
  1. read SFA's Add Loading form (loading_create.read_form) — its suggested quantities and the stock;
  2. quantities: SFA's suggested ones (job option "suggested") or the whole available stock ("stock");
     with "suggested", a territory SFA suggests nothing for is PAUSED (loading_holds) and the run goes on;
  3. add it (loading_create.create — follows Add loading's dry-run switch, checks the stock, verifies on
     View Loadings).
Then, for what was saved, the job's "after adding" choice:
  auto  — Prepare those distributors and confirm only the loading(s) Chimera just added (same number);
  ask   — Prepare them so they wait in Loadings for your approval (skipped if not answered by the end of the day);
  none  — leave them; you confirm yourself.
Paused territories are listed together at the end ("SFA suggested nothing — load the whole stock?"). Approving
adds the whole stock for the ones you pick, with the same "after adding" choice. Unanswered = skipped at the end
of the day.

SFA's own refusals (e.g. "Previously loaded quantities are not confirmed by the sales rep" — the rep hasn't
confirmed the last loading in the SFA mobile app) are reported as SFA's rule, and the run carries on.
"""
import threading
import time
from datetime import date, datetime

import loading_automation as LA
import loading_create as LC
import loading_store as LS

AUTO_STEPS = ["read", "add"]
REP_RULE = "not confirmed by the sales rep"


def _note(kind, title, body, action="loadings", tag=None):
    try:
        import desktop_notify
        desktop_notify.event(kind, "loadings", title, body, action=action, tag=tag)
    except Exception:
        pass


def _qty_for(products, mode):
    """{key: qty} from the form's products (only lines with stock). mode: suggested | stock."""
    if mode == "stock":
        return {p["key"]: p["stock"] for p in products if p["stock"] > 0}
    return {p["key"]: min(p["suggested"], p["stock"]) for p in products if p["suggested"] > 0 and p["stock"] > 0}


def _explain(msg):
    if REP_RULE in (msg or "").lower():
        return (msg + " — SFA's rule: the rep must confirm the previous loading in the SFA mobile app first. "
                      "Not a Chimera error.")
    return msg


def _wait_free(timeout_s=900):
    """Wait until no other loadings job is running (Prepare / Confirm / Add need the same lane)."""
    import lanes
    t0 = time.time()
    while lanes.busy_message("loadings") and time.time() - t0 < timeout_s:
        time.sleep(3)
    return lanes.busy_message("loadings") is None


def add_all(distributor_ids, mode="suggested", after="ask", trigger="manual", job_name="", hold_ids=None):
    """The whole job. Returns {ok, status, message, added, paused, failed, dry}. Runs in the caller's thread."""
    ds_all = {d["id"]: d for d in LS.list_distributors()}
    ds = [ds_all[i] for i in distributor_ids if i in ds_all]
    gone = [i for i in distributor_ids if i not in ds_all]
    if not ds:
        return {"ok": False, "status": "failed", "message": "None of the chosen distributors exist any more."}
    lanes_ = [LA._lane(d, AUTO_STEPS) for d in ds]
    LA.LIVE.reset(kind="auto_add", trigger=trigger)
    LA.LIVE.set(lanes=lanes_, steps=AUTO_STEPS, phase="adding")
    rid = LS.start_run("auto_add", trigger)
    LA.LIVE.set(run_id=rid)
    what = "the whole stock" if mode == "stock" else "SFA's suggested quantities"
    LA.LIVE.log(f"Auto add loadings{(' — ' + job_name) if job_name else ''}: {len(ds)} distributor(s), {what}. "
                f"After adding: {dict(auto='confirm automatically', ask='ask you first', none='leave for you')[after]}.")
    if gone:
        LA.LIVE.log(f"{len(gone)} chosen distributor(s) no longer exist — skipped.", level="warn")
    added, paused, failed, dry = [], [], [], []
    today = date.today().isoformat()
    for i, d in enumerate(ds):
        if LA.LIVE.cancelled():
            LA.LIVE.lane(i, status="cancelled", message="Stopped.")
            continue
        name = d["name"]
        if d.get("blocked") or d.get("problems"):
            why = (d.get("problems") or ["Needs setting up."])[0]
            failed.append((name, why))
            LA.LIVE.lane(i, status="failed", message=why)
            LA.LIVE.log(f"{name}: {why}", level="warn")
            continue
        LA.LIVE.lane(i, status="running", step="read", started=datetime.now().isoformat(timespec="seconds"))
        LA.LIVE.step(i, "read", "running")
        form = LC.read_form(d["id"])
        if not form.get("ok"):
            why = _explain(form.get("message") or "Couldn't read the Add Loading form.")
            if form.get("need_rep"):
                why = ("SFA offers more than one sales rep here — set the rep on this distributor "
                       "(Loadings › Add loading › Change fields) so Chimera can pick it.")
            failed.append((name, why))
            LA.LIVE.step(i, "read", "failed")
            LA.LIVE.lane(i, status="failed", message=why)
            LA.LIVE.log(f"{name}: {why}", level="warn")
            continue
        LA.LIVE.step(i, "read", "done")
        products = form.get("products") or []
        qty = _qty_for(products, mode)
        if not qty:
            if mode == "suggested" and products:
                total = sum(p["stock"] for p in products)
                hid = LS.add_hold(today, job_name, d, after, len(products), total)
                paused.append((name, len(products), total, hid))
                LA.LIVE.lane(i, status="skipped", message=f"Paused — SFA suggested nothing ({len(products)} product(s), "
                                                          f"{total:g} in stock). You'll be asked at the end.")
                LA.LIVE.log(f"{name}: SFA suggested nothing — paused; you'll be asked whether to load the whole stock.")
            else:
                why = "Nothing in stock to load."
                failed.append((name, why))
                LA.LIVE.lane(i, status="failed", message=why)
                LA.LIVE.log(f"{name}: {why}", level="warn")
            continue
        LA.LIVE.lane(i, step="add")
        LA.LIVE.step(i, "add", "running")
        res = LC.create(d["id"], qty)
        if res.get("dry_run"):
            dry.append(name)
            LA.LIVE.step(i, "add", "done")
            LA.LIVE.lane(i, status="done", message="Dry run — filled, stopped before SAVE (nothing saved).")
            LA.LIVE.log(f"{name}: dry run — page filled with {len(qty)} line(s), nothing saved.")
        elif res.get("ok") and res.get("saved"):
            added.append((d, res.get("number") or ""))
            LA.LIVE.step(i, "add", "done")
            LA.LIVE.lane(i, status="done", loading_number=res.get("number") or "",
                         message=f"Loading {res.get('number') or ''} saved ({len(qty)} line(s)).")
            LA.LIVE.log(f"{name}: loading {res.get('number') or '?'} saved in SFA ({len(qty)} line(s)).")
        else:
            why = _explain(res.get("message") or "Not saved.")
            failed.append((name, why))
            LA.LIVE.step(i, "add", "failed")
            LA.LIVE.lane(i, status="failed", message=why)
            LA.LIVE.log(f"{name}: {why}", level="warn")
    cancelled = LA.LIVE.cancelled()
    for did, hid in (hold_ids or {}).items():            # approved pauses: record how each one went
        name = next((d["name"] for d in ds if d["id"] == did), "")
        why = next((w for n, w in failed if n == name), None)
        if any(d["id"] == did for d, _ in added) or name in dry:
            LS.set_hold(hid, "added", "Whole stock added.")
        else:
            LS.set_hold(hid, "failed", why or ("Stopped." if cancelled else "Not added."))
    msg = (f"{len(added)} added" + (f", {len(dry)} dry run" if dry else "") + (f", {len(paused)} paused" if paused else "")
           + (f", {len(failed)} failed" if failed else "") + f" — of {len(ds)}.")
    status = "cancelled" if cancelled else ("done" if added or dry or paused else "failed")
    LS.finish_run(rid, status, msg, LA.log_text())
    LA.LIVE.set(active=False, phase="done",
                result={"ok": status == "done", "status": status, "message": msg, "run_id": rid})
    LA.LIVE.log(msg, level="info" if status == "done" else "warn")

    # ---- after adding: Prepare, then confirm automatically or leave them for your approval
    after_msg = ""
    if added and after in ("auto", "ask") and not cancelled:
        after_msg = _after_adding(added, after, trigger)
    # ---- one question for everything paused
    if paused:
        names = ", ".join(n for n, *_ in paused)
        _note("approval", "SFA suggested nothing — load the whole stock?",
              f"{len(paused)} territory(ies) paused: {names}. Open Loadings to choose.", tag=f"holds-{rid}")
    summary = msg + (" " + after_msg if after_msg else "")
    if failed:
        summary += " Failed: " + "; ".join(f"{n} ({w[:90]})" for n, w in failed[:6]) + "."
    _note("finished" if status == "done" and not failed else "failed", "Auto add loadings", summary,
          tag=f"autoadd-{rid}")
    return {"ok": status == "done", "status": status, "message": summary, "run_id": rid,
            "added": [d["name"] for d, _ in added], "paused": [p[0] for p in paused],
            "failed": failed, "dry": dry}


def _after_adding(added, after, trigger):
    ids = [d["id"] for d, _ in added]
    if not _wait_free():
        return "Couldn't prepare them — another loadings job was still running; prepare them in Loadings."
    prep = LA.run_prepare(ids, trigger="auto add", headless=None)
    if not prep.get("run_id"):
        return f"Couldn't prepare them for confirming: {prep.get('message', '')}"
    items = LS.list_items(run_id=prep["run_id"], states=("pending_approval",))
    # only the loading(s) Chimera just added: same number; if SFA gave no number, the one pending loading
    want = []
    for d, num in added:
        mine = [it for it in items if it["distributor_id"] == d["id"]]
        if num:
            mine = [it for it in mine if it.get("loading_number") == num]
        elif len(mine) != 1:
            mine = []
        want += [it["id"] for it in mine]
    if after == "ask":
        if want:
            _note("approval", "Loadings waiting for your approval",
                  f"{len(want)} loading(s) Chimera just added are ready to confirm — open Loadings.", tag="autoadd-ask")
        return (f"{len(want)} waiting for your approval in Loadings (skipped if not answered today)." if want
                else "They weren't found on View Loadings to prepare — check Loadings.")
    if not want:
        return "Not confirmed — the new loadings weren't found on View Loadings (check Loadings)."
    if not _wait_free():
        return "Not confirmed — another loadings job was still running; confirm them in Loadings."
    conf = LA.run_confirm(want, trigger="auto add")
    return f"Confirm: {conf.get('message', '')}"


# ---------------------------------------------------------------------------
# Background / paused territories / end of day
# ---------------------------------------------------------------------------
def start_background(distributor_ids, mode="suggested", after="ask", trigger="manual", job_name="", hold_ids=None):
    import lanes
    busy = lanes.busy_message("loadings")
    if busy:
        return {"ok": False, "message": busy}
    LA.LIVE.reset(kind="auto_add", trigger=trigger)
    threading.Thread(target=add_all, args=(distributor_ids, mode, after, trigger, job_name, hold_ids),
                     daemon=True, name="chimera-auto-loadings").start()
    return {"ok": True, "message": "Started."}


def approve_holds(hold_ids):
    """Load the whole stock for the paused territories you picked (each keeps its job's 'after adding')."""
    hs = [h for h in (LS.get_hold(int(i)) for i in hold_ids) if h and h["state"] == "waiting"]
    if not hs:
        return {"ok": False, "message": "Nothing to approve — those aren't waiting any more."}
    after = "ask" if any(h["after"] == "ask" for h in hs) else ("auto" if all(h["after"] == "auto" for h in hs) else "none")
    for h in hs:
        LS.set_hold(h["id"], "approved", "Approved — loading the whole stock.")
    r = start_background([h["distributor_id"] for h in hs], mode="stock", after=after, trigger="approved pause",
                         job_name="paused territories", hold_ids={h["distributor_id"]: h["id"] for h in hs})
    if not r.get("ok"):
        for h in hs:
            LS.set_hold(h["id"], "waiting", "")
    return r


def skip_holds(hold_ids):
    n = 0
    for i in hold_ids:
        h = LS.get_hold(int(i))
        if h and h["state"] == "waiting":
            LS.set_hold(h["id"], "skipped", "Skipped by you.")
            n += 1
    return {"ok": True, "message": f"Skipped {n}."}


def expire_old(today=None):
    """End of the day: unanswered pauses and unanswered 'ask me first' confirmations from auto runs are skipped."""
    today = today or date.today().isoformat()
    n = 0
    for h in LS.list_holds():
        if h["day"] < today:
            LS.set_hold(h["id"], "skipped", "Not answered by the end of the day.")
            n += 1
    for r in LS.list_runs(200):
        if r.get("kind") == "prepare" and r.get("trigger") == "auto add" and str(r.get("started_at") or "")[:10] < today:
            ids = [it["id"] for it in LS.list_items(run_id=r["id"], states=("pending_approval",))]
            if ids:
                LS.skip_items(ids)
                n += len(ids)
    return n
