"""
Made-up SFA Rep Attendance exports for tests (no real reps, no real data).
Same layout as SFA's RepAttendance_Reports export: filter meta above, a
group row (DETAILS / ATTENDANCE / TOUR START / TEAM / ASM ATTENDANCE ...),
the header row, then one row per rep who logged in.
"""
import io

GROUPS = [("DETAILS", 10), ("ATTENDANCE", 6), ("TOUR START", 2), ("TEAM", 2), ("ASM ATTENDANCE", 2)]
HEAD = ["REGION", "AREA", "SA", "TERRITORY", "DISTRIBUTOR", "SALES REP CODE", "SALES REP", "SALES REP PHONE NUMBER", "REP TYPE", "ROUTE",
        "LOGGED IN DATE", "LOGGED IN", "LOGGED IN LOCATION", "LOGGED OUT DATE", "LOGGED OUT", "LOGGED OUT LOCATION",
        "TOUR START", "ODO", "ASM", "SE",
        "LOGGED IN", "LOGGED OUT"]

REPS = [  # territory, rep, type, in, out
    ("Alpha Town", "CSR Alpha Town - Kamal Test", "VAN", "07:41:10", ""),
    ("Beta Bay", "DSR Beta Bay - Nimal Sample", "PRE", "08:05:00", "17:20:44"),
    ("Gamma Hills", "CSR Gamma Hills - Sunil Demo", "VAN", "08:47:31", ""),
    ("Delta Port", "Ruwan Example", "VAN", "07:59:59", "16:02:03"),
]


def export_bytes(day, reps=REPS, date_to=None, generated="2026-01-01 10:00:00"):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export"
    ws.append(["REP ATTENDANCE REPORT"])
    for _ in range(4):
        ws.append([])
    ws.append(["Date From", day, "Date To", date_to or day, "Generated at", generated])
    ws.append([])
    grp = []
    for g, n in GROUPS:
        grp += [g] + [None] * (n - 1)
    ws.append(grp)
    ws.append(HEAD)
    for i, r in enumerate(reps):
        terr, rep, typ, tin, tout = r[:5]
        asm = r[5] if len(r) > 5 else "ASM Test Person"
        ws.append(["Test Region", "Test Area", "Test SA", terr, f"Test Distributor {i + 1}", f"SR{i + 1:03d}", rep, "0770000000", typ, "R1",
                   day, tin, "6.9,79.8", day if tout else "", tout, "", "", "", asm, "",
                   "08:00:00", ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_export(path, day, **kw):
    with open(path, "wb") as f:
        f.write(export_bytes(day, **kw))
    return path
