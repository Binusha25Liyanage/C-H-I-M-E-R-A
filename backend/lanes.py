"""
lanes.py — which Chimera jobs may run at the same time.

Every job that works in SFA belongs to a lane:
    reports     report downloads + building reports (Early Bills, SKU, Pivot, Hourly)
    loadings    distributor loadings: add, prepare, confirm, distributor list update
    attendance  rep attendance
    outlets     itinerary reading + outlet visit sequence / Visit State
One job per lane at a time; jobs in different lanes run side by side (each in its own browser, sharing one SFA
login — see sfa_automation._login). With "Run SFA jobs side by side" off (Schedules), there is one lane for
everything, as before. WhatsApp sending is separate and queues by itself.
"""
import importlib

import automation_store as AS

LANE_MODULES = {
    "reports": ("sfa_automation",),
    "loadings": ("loading_automation",),
    "attendance": ("rep_attendance_automation",),
    "outlets": ("itinerary_automation", "sequence_automation"),
}
LABELS = {"reports": "a report download", "loadings": "a loadings job", "attendance": "rep attendance",
          "outlets": "an outlet updates / itinerary job"}


def job_lane(job_type):
    if job_type == "sfa_downloads" or job_type.startswith("report_"):
        return "reports"
    if job_type in ("loadings", "distributors", "auto_loadings"):
        return "loadings"
    if job_type == "rep_attendance":
        return "attendance"
    if job_type in ("outlet_updates", "itinerary", "sequence", "visit_state", "master_sync"):
        return "outlets"
    return "reports"


def lane_active(lane):
    for m in LANE_MODULES[lane]:
        try:
            if importlib.import_module(m).LIVE.snapshot().get("active"):
                return True
        except Exception:
            pass
    return False


def busy_message(lane):
    """None if a job in this lane may start now, else why not (in plain words)."""
    if lane_active(lane):
        return f"Another {LABELS[lane].replace('a ', '', 1).replace('an ', '', 1)} run is already in progress."
    if not AS.side_by_side():
        for other in AS.LANES:
            if other != lane and lane_active(other):
                return (f"Another SFA run is already in progress ({LABELS[other]}). Turn on \"Run SFA jobs side by "
                        "side\" in Schedules to let them run together.")
    return None


def active_lanes():
    return [l for l in AS.LANES if lane_active(l)]
