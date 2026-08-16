CHIMERA — UI DESIGN PROMPT

CONTEXT
Chimera is a Lakmee Exports Lanka internal desktop tool (pywebview: HTML/
CSS/JS frontend, Python backend) that replaces and extends an existing
internal app. That existing app is a flat set of 7 tabs (Data Import,
Previous Visit, Customer Sales, Customers/Outlets, SKU Report,
Maintenance, SFA Automation) with NO landing dashboard — it opens
straight into a data-import screen. Chimera must be visibly more
professional and more usable than that: a real dashboard, a modern
sidebar layout, and clear visual hierarchy — not just the same 7 tabs
in a nicer font.

BRAND
- Primary color: cherry red (#B5121B or close)
- Secondary color: ash grey (#6E6F71)
- Logo: Chimera creature mark (lion/goat/snake, ornamental circular
  frame, sepia-toned) — use as a small icon in the sidebar/header, not
  stretched or dominating the layout
- Typography: clean, modern sans-serif; avoid anything that reads as a
  generic default desktop-app font
- Overall tone: professional internal business tool — not playful,
  not consumer-app style, but not sterile either. Confident and precise.

LAYOUT SHAPE
- Persistent left sidebar for navigation (not top tabs like the
  original) — this alone is a meaningful structural upgrade, since
  sidebar nav scales better as more processes get added over time
- Sidebar sections, grouped logically (not just a flat list):
    Overview
      - Dashboard
    Reports
      - Combined SKU + Invoice Report
      - (future report types go here)
    SFA Automation
      - Visit State Bulk Update
      - Sequence Bulk Update
      - (future automated processes go here)
    Data
      - Import Data
      - Outlet Master
      - Maintenance
- Sidebar shows the Chimera logo + name at the top, collapsed/expanded
  states optional

DASHBOARD SCREEN (the new thing the original app never had)
This is the landing screen — what you see immediately on open. Needs:
1. Header stat cards (3-5 cards), each showing one key number at a
   glance: Total Outlets, Territories Loaded, Routes Loaded, Last Data
   Import Date, Last SFA Automation Run — cards should visually match
   the existing dashboard-summary data already wired up (total_outlets,
   territories_with_outlets, routes_with_outlets)
2. A "Recent Activity" panel — last few report generations or
   automation runs, each with a status indicator (success/failure/
   partial), timestamp, and a short description (e.g. "Combined Report
   — Ampara — 12 outlets — 2026-08-15")
3. A "Data Freshness" panel — flags stale or missing data sources at a
   glance (e.g. "Itinerary: last imported 3 days ago", "Invoices: no
   data for August yet") so problems are visible before running a
   report against stale data, not discovered after
4. Quick-action buttons for the most common tasks (e.g. "Generate
   Combined Report", "Run Visit State Update") so frequent tasks don't
   require sidebar navigation every time

COMBINED SKU + INVOICE REPORT SCREEN (already functionally built,
needs proper visual design)
- Territory selector + reference date picker at the top
- Two clear actions: Preview (single territory, shows on screen) vs.
  Generate PDFs — All Territories (bulk, writes files)
- Preview results as a clean, readable table: Seq | Outlet Code |
  Outlet | Invoices | Products | Units | Sales Value — multi-product
  outlets should visually group their product rows under one outlet
  (as in the original PDF layout), not repeat outlet info on every row
- Bulk generation should show a results summary: how many PDFs
  written, and a clearly separated list of any skipped territories
  with their reason (never silently drop failures)
- Any data-quality warnings (e.g. unmatched Pre Selling customer
  names) should be visually distinct (warning color/icon), not buried
  in body text

SFA AUTOMATION SCREENS (Visit State + Sequence bulk update — not yet
built, but design for this shape)
- Every automation screen needs to visually support BOTH bulk and
  single-item runs from the same screen — a Territory/Route filter to
  narrow scope, plus a queue/list of what's about to be processed
  before running
- Dry Run toggle should be prominent and default ON — this is a
  safety-critical control, not a minor checkbox
- A live log/progress panel during a run (streaming status per item,
  not just a spinner) — this is something the original app's SFA tab
  already does reasonably well and is worth preserving as a pattern
- Clear success/failure/skipped counts at the end, with failed items
  individually listed (not just a number) so Retry-Failed is
  meaningful

DATA IMPORT SCREEN
- Upload cards per data type (Itinerary, Territories, Invoices, Sales
  Orders, Outlet Master, SKU Sales) — each shows last-imported date/row
  count so staleness is visible at a glance (the original app's flat
  upload buttons give no such feedback)
- Raw SFA export auto-extraction (in progress) should feel like a
  single drag-and-drop step, not a multi-step manual cleaning process

WHAT TO DELIBERATELY NOT COPY FROM THE ORIGINAL APP
- No flat top-tab navigation — use the sidebar instead
- No screen should open with zero context about data freshness/state
  the way the original's tabs do
- Avoid dense, uncategorized upload-button walls (Data Import tab in
  the original has ~7 buttons with no grouping or status feedback)
- Table-heavy screens should use modern spacing/zebra-striping/sticky
  headers rather than the plain default styling the original relies on

DELIVERABLE
Design the Dashboard screen and the Combined SKU + Invoice Report
screen in full detail first (these are built and functional on the
backend, ready for real visual design now). Sketch the sidebar nav
structure and one representative SFA Automation screen at a lower
fidelity, since those are still being scoped.
