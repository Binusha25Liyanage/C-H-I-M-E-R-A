"""
sequence_report.py
One route sheet's preview / results as an Excel file: every outlet with the
rep's sequence and Top 15 mark, SFA's old sequence and Visit State, the new
ones, and what happened in SFA (saved / failed / would be saved).
"""

import os
from datetime import datetime

import sequence_store as SS

OUTCOME = {"": "Waiting", "updated": "Saved in SFA", "would_update": "Would be saved (dry run)", "failed": "Failed",
           "not_found": "Not on this route in SFA", "unchanged": "Already matched", "skipped": "Skipped"}
STATE = {"received": "Read from the file", "ready": "Waiting for approval", "approved": "Approved", "applying": "Saving",
         "done": "Done", "partial": "Partly saved", "failed": "Failed", "rejected": "Rejected", "discarded": "Discarded",
         "superseded": "Replaced by a newer sheet"}


def export_sheet(sid, folder=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    s = SS.get_sheet(sid)
    if not s:
        raise ValueError("That sheet no longer exists.")
    folder = folder or os.path.join(os.path.expanduser("~"), "Chimera", "exports")
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in s["route_name"] or "sheet")[:50]
    path = os.path.join(folder, f"Outlet_updates_{safe}_{s['route_code']}_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "Outlets"
    red = PatternFill("solid", fgColor="B5121B")
    ws.append([f"Outlet updates — {s['route_name']} ({s['route_code']})"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Territory: {s['territory']}    Rep: {s['rep']}    Distributor: {s['distributor']}    "
               f"Sheet #{s['id']} from {s['source']} ({s['file_name']}) received {s['received_at'].replace('T', ' ')}"])
    ws.append([f"Status: {STATE.get(s['state'], s['state'])}{' (dry run)' if s['dry_run'] else ''} — {s['message']}"])
    ws.append([])
    head = ["New seq", "Outlet code", "Outlet name", "Rep's seq", "Top 15", "Old seq", "Visit State before", "Visit State after",
            "Set by", "Remark", "Demand product", "Result", "Details"]
    ws.append(head)
    for c in ws[5]:
        c.font, c.fill, c.alignment = Font(bold=True, color="FFFFFF"), red, Alignment(wrap_text=True, vertical="center")
    for l in s["lines"]:
        ws.append([l["new_seq"], l["outlet_code"], l["outlet_name"], l["rep_seq"], l["top_mark"] or ("✓" if l["top"] else ""),
                   l["cur_seq"], l["cur_state"],
                   l["new_state"], {"top": "Top 15", "you": "You", "": ""}.get(l["state_by"], l["state_by"]),
                   l["remark"], l["demand"], OUTCOME.get(l["outcome"], l["outcome"]), l["message"]])
    for col, w in zip("ABCDEFGHIJKLM", (8, 17, 26, 9, 8, 8, 13, 13, 9, 24, 22, 20, 70)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A6"
    if s["problems"]:
        p = wb.create_sheet("Notes")
        for x in s["problems"]:
            p.append([x["level"].upper(), x["text"]])
        p.column_dimensions["B"].width = 120
    wb.save(path)
    return path
