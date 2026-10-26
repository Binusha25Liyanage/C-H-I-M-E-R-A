"""
run_reports.py
Command-line / headless entry point for the daily SFA reports. This is what
Windows Task Scheduler will call so reports are produced even when the
Chimera window is closed.

Examples:
    py backend\\run_reports.py                                  (today, all reports)
    py backend\\run_reports.py --date 2026-10-26 --kinds sku pivot
    py backend\\run_reports.py --files Invoices_11.xlsx Sales_Order_6.xlsx ...

Exit code: 0 = all requested reports written, 1 = some/all failed.
"""

import argparse
import os
import sys
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import sfa_store  # noqa: E402
import report_pipeline  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate Chimera daily SFA reports.")
    ap.add_argument("--date", default=_date.today().isoformat(), help="Report date YYYY-MM-DD (default: today)")
    ap.add_argument("--kinds", nargs="+", choices=report_pipeline.REPORT_KINDS,
                    default=list(report_pipeline.REPORT_KINDS))
    ap.add_argument("--files", nargs="*", default=[], help="Raw SFA exports to import first")
    ap.add_argument("--output", default=None, help="Output root folder")
    ap.add_argument("--no-images", action="store_true", help="Skip WhatsApp PNGs")
    ap.add_argument("--style", choices=sfa_store.OUTPUT_STYLES, default=None,
                    help="Output style (default: the style saved in Settings)")
    args = ap.parse_args(argv)

    db.init_db()
    sfa_store.init_schema()
    report_pipeline.init_schema()

    r = report_pipeline.run_reports(args.date, kinds=args.kinds, files=args.files, output_root=args.output,
                                    images=not args.no_images, trigger="cli", style=args.style)
    if r.get("warning"):
        print("WARNING: " + r["warning"])
    for imp in r["imports"]:
        if imp["ok"]:
            for s in imp["stored"]:
                print(f"  imported {s['label']} for {s['date']}: {s['rows']} rows")
        else:
            print(f"  IMPORT FAILED {imp['file']}: {imp['message']}")
    for rep in r["reports"]:
        if rep["ok"]:
            print(f"  OK   {rep['label']}: {rep['pdf']} (+{len(rep['images'])} images)")
            for n in rep["notes"]:
                print(f"       note: {n}")
        else:
            print(f"  FAIL {rep['label']}: {rep['reason']}")
    print(f"Run #{r['run_id']} {r['status'].upper()}" + (f" -> {r['folder']}" if r["folder"] else ""))
    return 0 if r["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
