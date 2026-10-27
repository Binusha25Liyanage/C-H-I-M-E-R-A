# Chimera

**Internal sales operations platform for Lakmee Exports Lanka — unifying itinerary tracking, sales reporting, and SFA (Sales Force Automation) browser automation into one desktop application.**

Chimera replaces and extends an earlier internal tool (a CustomTkinter desktop app) with a modern, web-technology UI packaged as a single desktop application — plus a from-scratch, bidirectional automation layer for Lakmee's SFA system, which has no API and is otherwise only operable by hand, one screen at a time.

The name fits the project's shape: Chimera merges what were previously two separate efforts — an itinerary/sales reporting tool and a browser-automation tool for SFA — into one system with one database and one interface.

---

## Why this exists

Lakmee's SFA (Sales Force Automation) system is a purchased, third-party product with **no API and no bulk import** — every operation (checking an outlet's Visit State, resequencing a route, pulling a report) is UI-only, done by hand, one outlet or one screen at a time. For a sales operation covering dozens of territories and thousands of outlets, that doesn't scale.

Chimera's job is to close that gap: automate what SFA won't expose an API for, using real browser automation, while also giving the IT/sales operations team a single, reliable place to generate the reports the business actually runs on.

---

## What's built

- **Dashboard** — live outlet/territory/route counts, and honest data-freshness tracking (no invented numbers — if something hasn't been imported, it says so)
- **Combined SKU + Invoice Report** — a route-wise, per-outlet report merging invoice counts and product-level SKU breakdown, anchored to each territory's previous-visit date (resolved automatically from the itinerary calendar). Supports both single-territory preview and bulk PDF generation across every territory in one run
- **Import tracking** — every data import is logged with a real timestamp and row count, surfaced on both the Dashboard and the Import Data screen

## What's planned

- **Raw SFA export auto-extraction** — upload an unmodified SFA Excel export directly; Chimera detects and extracts only the columns it needs, instead of requiring manual cleanup first
- **SFA browser automation (Playwright)** — bidirectional Outlet Visit State updates (Mandatory ↔ Optional) and Sequence updates, filterable by Territory/Route, with dry-run mode, audit logging, and read-back verification on every write — built and validated against a Test SFA environment before touching production
- **Outlet Master and Maintenance screens**

---

## Design principles

A few rules apply across every feature in this project, not just one screen:

- **Bulk and single are the same code path.** Every process is built to take a list of items — one item is "single mode," many is "bulk mode." There's no special-casing between them.
- **Never fabricate status.** If something isn't tracked or built yet, the UI says so explicitly rather than showing a plausible-looking placeholder number.
- **Accuracy over convenience.** Where a data join is genuinely unreliable (e.g. Pre Selling orders only carry a customer name, not an outlet code), that limitation is surfaced to the user rather than silently guessed at.
- **Dry-run first, always.** Anything that writes to production SFA data defaults to a safe simulation mode.

---

## Tech stack

| Layer | Technology |
|---|---|
| Desktop shell | [pywebview](https://pywebview.flowrl.com/) — native window, web-technology UI, packaged as a single `.exe` (no browser tab, no server URL) |
| Frontend | HTML / CSS / vanilla JS |
| Backend | Python |
| Database | SQLite |
| Reports | ReportLab (PDF), pandas / openpyxl (Excel) |
| Browser automation | Playwright *(planned)* |
| Packaging | PyInstaller |

## Project structure

```
chimera/
├── backend/
│   ├── app.py                  # Entry point — opens the window, wires the API bridge
│   ├── api.py                  # The only file the frontend talks to — Python<->JS bridge
│   ├── db.py                   # SQLite schema + all data access
│   ├── excel_import.py         # Parses itinerary / territory / invoice / SKU Excel files
│   ├── pdf_export.py           # Legacy PDF builders (carried over, being phased into combined_report_pdf.py)
│   ├── combined_report.py      # Combined SKU + Invoice report logic
│   └── combined_report_pdf.py  # Combined report PDF rendering
├── frontend/
│   ├── index.html
│   ├── css/theme.css           # Cherry red + ash brand theme
│   ├── js/main.js
│   └── assets/                 # Logo, icon
├── build.py                    # PyInstaller packaging (produces a single Windows .exe)
└── requirements.txt
```

## Getting started

```bash
pip install -r requirements.txt
cd backend
python app.py
```

To build a standalone Windows executable:

```bash
python build.py
```

---

## Status

Actively developed. This is an internal tool for Lakmee Exports Lanka's IT department — not a general-purpose or public product.

## Daily SFA Reports (SKU, Pivot, Hourly BC & SV)

Built straight from **raw, unedited** SalesPro exports — no manual merging.

1. **Settings → Manager groups → Import from SalesPulse database…** (one-time): brings in the
   territory→manager list, report rules and history.
2. **Daily SFA Reports → Select SFA export files…**: pick the 5 exports at once (Invoices – Van selling,
   Sales Orders – Pending, Outlet Wise SKU – Van Selling, Outlet Wise SKU – Pre Sales Order Pending,
   Unproductive Visits). Each file is identified from its own headers/banner; a newer export for the same
   date replaces the older one.
3. **Generate reports** → PDFs + WhatsApp-ready PNGs in `~/Chimera/exports/SFA Reports/<date>/<time>/`.

Headless (used by the scheduler): `py backend\run_reports.py --date 2026-10-26`

Tests: `py -m unittest discover -s tests -v`

## SFA Automation (download reports from SFA automatically)

**SFA Automation** screen:

* **Report Profiles** — one per SFA report: navigation URL, what it feeds (Invoices, Sales Orders,
  SKU Van, SKU Pre, Unproductive, or download-only), the filters to fill, and the Search / Export button
  texts. *Detect filters from page* logs in, reads the page's filter labels and dropdown options for you.
  **Test** runs one profile with screenshots and never imports anything. Add as many profiles as you need.
* **Run all now** — logs in once, opens every enabled report in parallel tabs (1–5), fills filters,
  exports, checks each file's banner against the profile (report kind, Report Type, dates…), imports it,
  then builds the daily reports. A live monitor shows each tab's progress; a failed tab retries on its own.
* **Schedule & Control** — run times, run days, Pause/Resume, Skip today, Cancel current run, and
  *Run even when Chimera is closed* (Windows Task Scheduler → `Chimera.exe --scheduled` or
  `py backend\run_scheduled.py`).
* **Run History** — every run with per-report results. Downloads are kept in
  `~/Chimera/downloads/<date>/<time>/`, failure screenshots in `~/Chimera/screenshots/automation/`.

Headless: `py backend\run_scheduled.py --now` runs every enabled profile immediately.
