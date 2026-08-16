"""
db.py
Database layer for the Itinerary Visit Tracker.
Uses a local SQLite file so uploaded data persists between sessions.

The database lives in a fixed location under the user's home directory
(NOT inside the app folder). This matters because every time you download
an updated copy of this app and extract it to a new folder, an app-folder
-relative database would start out empty again — that's very likely why
territory data kept disappearing and asking to be re-uploaded. Keeping the
database in a stable, app-folder-independent location means it survives
across app updates automatically.
"""

import sqlite3
import os
from datetime import date

# Chimera intentionally uses its own data folder and filename, separate
# from the original Itinerary Visit Tracker app's
# ~/ItineraryVisitTracker/data/itinerary.db — the two are independent
# databases by design, so testing or using one never touches the other's
# data.
APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "data")
DB_PATH = os.path.join(APP_DATA_DIR, "chimera.db")


def get_connection():
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS territories (
            territory   TEXT PRIMARY KEY,
            group_name  TEXT NOT NULL,
            sale_model  TEXT
        )
    """)

    # Migration for databases created before sale_model existed
    cur.execute("PRAGMA table_info(territories)")
    cols = [row[1] for row in cur.fetchall()]
    if "sale_model" not in cols:
        cur.execute("ALTER TABLE territories ADD COLUMN sale_model TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS itinerary (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            year             INTEGER NOT NULL,
            month            INTEGER NOT NULL,
            region           TEXT,
            area             TEXT,
            superviser_area  TEXT,
            territory        TEXT NOT NULL,
            distributor      TEXT,
            sales_rep        TEXT,
            visit_date       TEXT NOT NULL,   -- ISO format YYYY-MM-DD
            route            TEXT NOT NULL,
            UNIQUE(territory, visit_date)
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_itin_territory_date ON itinerary(territory, visit_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_itin_year_month ON itinerary(territory, year, month)")

    # Van Selling — actual invoices (direct same-day sale)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS van_sales (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            inv_number         TEXT UNIQUE NOT NULL,
            so_number          TEXT,
            inv_date           TEXT NOT NULL,   -- ISO format YYYY-MM-DD
            distributor        TEXT,
            sales_rep          TEXT,
            territory          TEXT NOT NULL,
            route              TEXT NOT NULL,
            customer_category  TEXT,
            outlet_code        TEXT,
            customer           TEXT,
            sales_value        REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_van_territory_route_date ON van_sales(territory, route, inv_date)")

    # Pre Selling — sales orders, which may convert to an invoice up to 72h later
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pre_sales (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            so_number      TEXT UNIQUE NOT NULL,
            order_date     TEXT NOT NULL,   -- ISO format YYYY-MM-DD (order creation date)
            distributor    TEXT,
            sales_rep      TEXT,
            territory      TEXT NOT NULL,
            route          TEXT NOT NULL,
            customer       TEXT,
            sales_value    REAL,
            invoice_status TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pre_territory_route_date ON pre_sales(territory, route, order_date)")

    # Outlet / customer master list, sequenced by route visiting order
    cur.execute("""
        CREATE TABLE IF NOT EXISTS outlets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            region       TEXT,
            area         TEXT,
            sa           TEXT,
            territory    TEXT NOT NULL,
            route_code   TEXT,
            route        TEXT NOT NULL,
            sequence     INTEGER,        -- NULL when the source sheet has '-' (unsequenced)
            outlet_code  TEXT UNIQUE NOT NULL,
            outlet       TEXT NOT NULL,
            category     TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_outlets_territory_route ON outlets(territory, route, sequence)")

    # SKU-wise sales, tagged with the visit/reference date the figures apply to
    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_sales (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_model     TEXT NOT NULL,   -- 'Van Selling' or 'Pre Selling'
            report_date    TEXT NOT NULL,   -- ISO date these figures are attributed to
            territory      TEXT NOT NULL,
            outlet_code    TEXT NOT NULL,
            product_code   TEXT,
            product        TEXT NOT NULL,
            units          REAL,
            sales_value    REAL,
            distributor    TEXT,
            sales_rep      TEXT,
            UNIQUE(sale_model, report_date, outlet_code, product_code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prodsales_lookup ON product_sales(sale_model, report_date, territory, outlet_code)")

    # Tracks the real last-imported time and row count per data type, so
    # the UI can show honest "Last Imported: ..." status instead of
    # invented numbers.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS import_log (
            data_type      TEXT PRIMARY KEY,
            last_imported  TEXT NOT NULL,   -- ISO datetime
            row_count      INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def record_import(data_type, row_count):
    """Called after a successful import to log when it happened and how much data came in."""
    from datetime import datetime
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO import_log (data_type, last_imported, row_count)
           VALUES (?, ?, ?)
           ON CONFLICT(data_type) DO UPDATE SET
                last_imported=excluded.last_imported, row_count=excluded.row_count""",
        (data_type, datetime.now().isoformat(timespec="seconds"), row_count),
    )
    conn.commit()
    conn.close()


def get_import_log():
    """Returns {data_type: {last_imported, row_count}} for every data type ever imported."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT data_type, last_imported, row_count FROM import_log")
    result = {dt: {"last_imported": li, "row_count": rc} for dt, li, rc in cur.fetchall()}
    conn.close()
    return result


# ---------------------------------------------------------------------------
# Territories
# ---------------------------------------------------------------------------

SALE_MODELS = ("Van Selling", "Pre Selling")


def upsert_territories(rows):
    """
    rows: list of (territory, group_name, sale_model)
    Adds new territories, updates group/sale_model for existing ones (as
    requested: re-uploading the territory list should add newly-added
    territories and refresh the data for ones already known).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        """INSERT INTO territories (territory, group_name, sale_model) VALUES (?, ?, ?)
           ON CONFLICT(territory) DO UPDATE SET
                group_name = excluded.group_name,
                sale_model = excluded.sale_model""",
        rows,
    )
    conn.commit()
    added_or_updated = cur.rowcount
    conn.close()
    return added_or_updated


def get_all_groups():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT group_name FROM territories ORDER BY group_name")
    groups = [r[0] for r in cur.fetchall()]
    conn.close()
    return groups


def get_territories_for_group(group_name):
    conn = get_connection()
    cur = conn.cursor()
    if group_name == "All":
        cur.execute("SELECT territory, group_name, sale_model FROM territories ORDER BY group_name, territory")
    else:
        cur.execute(
            "SELECT territory, group_name, sale_model FROM territories WHERE group_name = ? ORDER BY territory",
            (group_name,),
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_territory_count():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM territories")
    n = cur.fetchone()[0]
    conn.close()
    return n


def get_territory_info(territory):
    """Returns (group_name, sale_model) for one territory, or None if unknown."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT group_name, sale_model FROM territories WHERE territory = ?", (territory,))
    row = cur.fetchone()
    conn.close()
    return row


def get_van_sales_count():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(inv_date), MAX(inv_date) FROM van_sales")
    row = cur.fetchone()
    conn.close()
    return row  # (count, min_date, max_date)


def get_pre_sales_count():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), MIN(order_date), MAX(order_date) FROM pre_sales")
    row = cur.fetchone()
    conn.close()
    return row  # (count, min_date, max_date)


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------

def replace_month_for_territories(year, month, territories):
    """
    Deletes existing itinerary rows for the given (year, month) for the
    listed territories, so a fresh import of that month cleanly replaces
    old data (handles edits/removals, not just additions).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        "DELETE FROM itinerary WHERE year = ? AND month = ? AND territory = ?",
        [(year, month, t) for t in territories],
    )
    conn.commit()
    conn.close()


def insert_itinerary_rows(rows):
    """
    rows: list of dicts with keys:
        year, month, region, area, superviser_area, territory,
        distributor, sales_rep, visit_date (ISO str), route
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        """INSERT INTO itinerary
           (year, month, region, area, superviser_area, territory,
            distributor, sales_rep, visit_date, route)
           VALUES (:year, :month, :region, :area, :superviser_area, :territory,
                    :distributor, :sales_rep, :visit_date, :route)
           ON CONFLICT(territory, visit_date) DO UPDATE SET
                year=excluded.year, month=excluded.month, region=excluded.region,
                area=excluded.area, superviser_area=excluded.superviser_area,
                distributor=excluded.distributor, sales_rep=excluded.sales_rep,
                route=excluded.route""",
        rows,
    )
    conn.commit()
    n = len(rows)
    conn.close()
    return n


def get_months_loaded():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT year, month FROM itinerary ORDER BY year, month")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_visit_on_date(territory, visit_date_iso):
    """Returns the route text (or None) for a territory on a specific date."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT route, sales_rep, distributor FROM itinerary WHERE territory = ? AND visit_date = ?",
        (territory, visit_date_iso),
    )
    row = cur.fetchone()
    conn.close()
    return row  # (route, sales_rep, distributor) or None


def get_previous_visit(territory, route, before_date_iso):
    """
    Finds the most recent date (strictly before before_date_iso, across ALL
    loaded months) where this territory's route text matches `route`.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT visit_date FROM itinerary
           WHERE territory = ? AND route = ? AND visit_date < ?
           ORDER BY visit_date DESC LIMIT 1""",
        (territory, route, before_date_iso),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_rep_and_distributor(territory):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT sales_rep, distributor FROM itinerary WHERE territory = ? ORDER BY visit_date DESC LIMIT 1",
        (territory,),
    )
    row = cur.fetchone()
    conn.close()
    return row if row else (None, None)


# ---------------------------------------------------------------------------
# Van Selling (invoices)
# ---------------------------------------------------------------------------

def insert_van_sales_rows(rows):
    """
    rows: list of dicts with keys:
        inv_number, so_number, inv_date (ISO), distributor, sales_rep,
        territory, route, customer_category, outlet_code, customer, sales_value
    Upserts by inv_number, so re-uploading the same invoice file is safe.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        """INSERT INTO van_sales
           (inv_number, so_number, inv_date, distributor, sales_rep, territory,
            route, customer_category, outlet_code, customer, sales_value)
           VALUES (:inv_number, :so_number, :inv_date, :distributor, :sales_rep, :territory,
                    :route, :customer_category, :outlet_code, :customer, :sales_value)
           ON CONFLICT(inv_number) DO UPDATE SET
                so_number=excluded.so_number, inv_date=excluded.inv_date,
                distributor=excluded.distributor, sales_rep=excluded.sales_rep,
                territory=excluded.territory, route=excluded.route,
                customer_category=excluded.customer_category, outlet_code=excluded.outlet_code,
                customer=excluded.customer, sales_value=excluded.sales_value""",
        rows,
    )
    conn.commit()
    n = len(rows)
    conn.close()
    return n


def get_van_sales_customers(territory, route, visit_date_iso):
    """Exact-date match: Van Selling = direct sale, invoiced the same day."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT customer, outlet_code, sales_value, inv_date, inv_number, sales_rep, distributor
           FROM van_sales WHERE territory = ? AND route = ? AND inv_date = ?
           ORDER BY customer""",
        (territory, route, visit_date_iso),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Pre Selling (sales orders)
# ---------------------------------------------------------------------------

def insert_pre_sales_rows(rows):
    """
    rows: list of dicts with keys:
        so_number, order_date (ISO), distributor, sales_rep, territory,
        route, customer, sales_value, invoice_status
    Upserts by so_number, so re-uploading the same order file is safe.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        """INSERT INTO pre_sales
           (so_number, order_date, distributor, sales_rep, territory,
            route, customer, sales_value, invoice_status)
           VALUES (:so_number, :order_date, :distributor, :sales_rep, :territory,
                    :route, :customer, :sales_value, :invoice_status)
           ON CONFLICT(so_number) DO UPDATE SET
                order_date=excluded.order_date, distributor=excluded.distributor,
                sales_rep=excluded.sales_rep, territory=excluded.territory, route=excluded.route,
                customer=excluded.customer, sales_value=excluded.sales_value,
                invoice_status=excluded.invoice_status""",
        rows,
    )
    conn.commit()
    n = len(rows)
    conn.close()
    return n


def get_pre_sales_customers(territory, route, date_from_iso, date_to_iso):
    """
    Window match: a Pre Selling order taken on the visit date can be entered
    into the system up to 72 hours later, so date_to_iso should be
    date_from_iso + 2 days.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """SELECT customer, sales_value, order_date, so_number, sales_rep, distributor, invoice_status
           FROM pre_sales WHERE territory = ? AND route = ? AND order_date BETWEEN ? AND ?
           ORDER BY order_date, customer""",
        (territory, route, date_from_iso, date_to_iso),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Outlets (territory / route-wise customer master list)
# ---------------------------------------------------------------------------

def insert_outlets_rows(rows):
    """
    rows: list of dicts with keys:
        region, area, sa, territory, route_code, route, sequence (int or None),
        outlet_code, outlet, category
    Upserts by outlet_code, so re-uploading the full list refreshes everyone
    safely (sequence changes, route reassignments, new outlets all update).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        """INSERT INTO outlets
           (region, area, sa, territory, route_code, route, sequence, outlet_code, outlet, category)
           VALUES (:region, :area, :sa, :territory, :route_code, :route, :sequence,
                    :outlet_code, :outlet, :category)
           ON CONFLICT(outlet_code) DO UPDATE SET
                region=excluded.region, area=excluded.area, sa=excluded.sa,
                territory=excluded.territory, route_code=excluded.route_code, route=excluded.route,
                sequence=excluded.sequence, outlet=excluded.outlet, category=excluded.category""",
        rows,
    )
    conn.commit()
    n = len(rows)
    conn.close()
    return n


def get_outlet_count():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT territory), COUNT(DISTINCT route) FROM outlets")
    row = cur.fetchone()
    conn.close()
    return row  # (count, territory_count, route_count)


def get_outlet_territories(group_filter=None):
    """
    Returns territories that have outlets loaded, optionally narrowed to
    those belonging to a given responsible-person Group (via the
    territories table). group_filter of None or "All" returns everything.
    """
    conn = get_connection()
    cur = conn.cursor()
    if group_filter and group_filter != "All":
        cur.execute(
            """SELECT DISTINCT o.territory FROM outlets o
               JOIN territories t ON t.territory = o.territory
               WHERE t.group_name = ?
               ORDER BY o.territory""",
            (group_filter,),
        )
    else:
        cur.execute("SELECT DISTINCT territory FROM outlets ORDER BY territory")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def get_outlet_routes(territory):
    conn = get_connection()
    cur = conn.cursor()
    if territory == "All":
        cur.execute("SELECT DISTINCT route FROM outlets ORDER BY route")
    else:
        cur.execute("SELECT DISTINCT route FROM outlets WHERE territory = ? ORDER BY route", (territory,))
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def get_outlets(territory, route):
    """
    Returns outlets for the given territory (or 'All') and route (or 'All'),
    sorted ascending by sequence — unsequenced outlets ('-' in the source
    file, stored as NULL) sort last, then by outlet name.
    """
    conn = get_connection()
    cur = conn.cursor()
    clauses = []
    params = []
    if territory != "All":
        clauses.append("territory = ?")
        params.append(territory)
    if route != "All":
        clauses.append("route = ?")
        params.append(route)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cur.execute(
        f"""SELECT territory, route, sequence, outlet_code, outlet, category
            FROM outlets {where}
            ORDER BY territory, route,
                     CASE WHEN sequence IS NULL THEN 1 ELSE 0 END, sequence, outlet""",
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# SKU-wise sales (product-line level), used for the route-wise SKU report
# ---------------------------------------------------------------------------

def insert_product_sales_rows(rows):
    """
    rows: list of dicts with keys:
        sale_model, report_date, territory, outlet_code, product_code,
        product, units, sales_value, distributor, sales_rep
    Upserts by (sale_model, report_date, outlet_code, product_code), so
    re-uploading the same date's file is safe.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        """INSERT INTO product_sales
           (sale_model, report_date, territory, outlet_code, product_code, product,
            units, sales_value, distributor, sales_rep)
           VALUES (:sale_model, :report_date, :territory, :outlet_code, :product_code, :product,
                    :units, :sales_value, :distributor, :sales_rep)
           ON CONFLICT(sale_model, report_date, outlet_code, product_code) DO UPDATE SET
                territory=excluded.territory, product=excluded.product, units=excluded.units,
                sales_value=excluded.sales_value, distributor=excluded.distributor,
                sales_rep=excluded.sales_rep""",
        rows,
    )
    conn.commit()
    n = len(rows)
    conn.close()
    return n


def get_product_sales_count():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT sale_model, COUNT(*), COUNT(DISTINCT report_date), MIN(report_date), MAX(report_date)
                   FROM product_sales GROUP BY sale_model""")
    rows = cur.fetchall()
    conn.close()
    return rows  # [(sale_model, count, distinct_dates, min_date, max_date), ...]


def get_product_sales_dates(sale_model):
    """
    Returns the sorted list of distinct report_date values loaded for this
    sale_model. Used to give a clear diagnostic when a route's SKU report
    comes back empty — distinguishes "no data was ever imported for that
    date" from "data exists but genuinely nobody bought anything."
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT report_date FROM product_sales WHERE sale_model = ? ORDER BY report_date",
        (sale_model,),
    )
    dates = [r[0] for r in cur.fetchall()]
    conn.close()
    return dates


def get_route_sku_report(territory, route, report_date, sale_model):
    """
    Builds the route-wise SKU report for one territory + route + date:
    every outlet on that route (from the outlets master, sequence-sorted),
    with its product lines for that date/sale_model if it bought anything,
    or an empty line list if not (an 'unproductive' outlet).

    Matches product_sales to outlets by outlet_code alone (not territory) —
    the SKU export's own TERRITORY column can lag behind the outlets master
    (e.g. after a rep reassignment), while outlet_code is the stable,
    globally-unique key, so the outlets master's route/territory assignment
    is treated as authoritative.

    Returns (returns/negative rows) are handled specially: a negative-unit
    row is a return, not a sale line, so it is excluded from the displayed
    'lines' (the per-product quantities shown to verify what was actually
    sold) but its value is still netted into 'subtotal' and therefore into
    the report Total. An outlet with only a return (no positive line) shows
    as unproductive (blank lines) but still contributes its negative value
    to the Total.

    Returns a list of dicts:
        {sequence, outlet_code, outlet, category, lines: [(product, units, value), ...], subtotal}
    """
    outlets = get_outlets(territory, route)  # already sorted by sequence, then outlet name
    if not outlets:
        return []

    outlet_codes = [o[3] for o in outlets]

    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in outlet_codes)
    cur.execute(
        f"""SELECT outlet_code, product, units, sales_value FROM product_sales
            WHERE sale_model = ? AND report_date = ? AND outlet_code IN ({placeholders})""",
        (sale_model, report_date, *outlet_codes),
    )
    all_lines_by_outlet = {}
    for outlet_code, product, units, sales_value in cur.fetchall():
        all_lines_by_outlet.setdefault(outlet_code, []).append((product, units, sales_value))
    conn.close()

    report = []
    for territory_, route_, sequence, outlet_code, outlet, category in outlets:
        all_lines = all_lines_by_outlet.get(outlet_code, [])
        display_lines = [(p, u, v) for p, u, v in all_lines if u > 0]
        subtotal = sum(v for _, _, v in all_lines)  # nets any return (negative) rows
        report.append({
            "sequence": sequence,
            "outlet_code": outlet_code,
            "outlet": outlet,
            "category": category,
            "lines": display_lines,
            "subtotal": subtotal,
        })
    return report


# ---------------------------------------------------------------------------
# Maintenance — table-wise data clearing
# ---------------------------------------------------------------------------

# Human label -> actual table name. Whitelisted deliberately (never build
# the table name from raw UI input) so this can't be turned into arbitrary
# SQL execution.
MAINTENANCE_TABLES = {
    "Itinerary (monthly visit plans)": "itinerary",
    "Territories (group / sale model)": "territories",
    "Van Selling invoices": "van_sales",
    "Pre Selling orders": "pre_sales",
    "Outlets (customer master list)": "outlets",
    "SKU-wise sales": "product_sales",
}


def get_maintenance_table_counts():
    """Returns {label: row_count} for every table on the maintenance page."""
    conn = get_connection()
    cur = conn.cursor()
    counts = {}
    for label, table in MAINTENANCE_TABLES.items():
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        counts[label] = cur.fetchone()[0]
    conn.close()
    return counts


def clear_table(label):
    """
    Deletes every row from the table matching this maintenance label.
    Returns the number of rows deleted. Raises ValueError for an unknown
    label (defense in depth — the UI should only ever pass a known label).
    """
    if label not in MAINTENANCE_TABLES:
        raise ValueError(f"Unknown maintenance table label: {label}")
    table = MAINTENANCE_TABLES[label]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    n = cur.fetchone()[0]
    cur.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    return n


def clear_all_tables():
    """Clears every table. Returns {label: rows_deleted}."""
    results = {}
    for label in MAINTENANCE_TABLES:
        results[label] = clear_table(label)
    return results


# ---------------------------------------------------------------------------
# Export current data back to Excel-ready rows (for edit-and-reupload flows)
# ---------------------------------------------------------------------------

def export_territories_rows(territory_filter=None):
    """
    Returns rows for writing back out to Excel in the same shape the
    Territories importer expects: (No, Group, Territory, Sale Model).
    """
    conn = get_connection()
    cur = conn.cursor()
    if territory_filter and territory_filter != "All":
        cur.execute(
            "SELECT territory, group_name, sale_model FROM territories WHERE territory = ? ORDER BY territory",
            (territory_filter,),
        )
    else:
        cur.execute("SELECT territory, group_name, sale_model FROM territories ORDER BY group_name, territory")
    rows = cur.fetchall()
    conn.close()
    return rows


def export_outlets_rows(group_filter="All", territory_filter="All", route_filter="All"):
    """
    Returns rows for writing back out to Excel in the same shape the
    Outlets importer expects: (Region, Area, SA, Territory, Route Code,
    Route, Sequence, Outlet Code, Outlet, Category).

    Scope, most specific wins — same priority as resequence_outlets() /
    get_route_pairs_for_scope(), so "export what I'm looking at" always
    matches "what Recreate Sequence would act on":
      - a specific Route (with a specific Territory): just that route
      - a specific Territory: every route in it
      - a specific Group: every territory in it
      - everything "All": the full outlet list
    """
    conn = get_connection()
    cur = conn.cursor()

    if territory_filter and territory_filter != "All" and route_filter and route_filter != "All":
        cur.execute(
            """SELECT region, area, sa, territory, route_code, route, sequence, outlet_code, outlet, category
               FROM outlets WHERE territory = ? AND route = ?
               ORDER BY CASE WHEN sequence IS NULL THEN 1 ELSE 0 END, sequence, outlet""",
            (territory_filter, route_filter),
        )
    elif territory_filter and territory_filter != "All":
        cur.execute(
            """SELECT region, area, sa, territory, route_code, route, sequence, outlet_code, outlet, category
               FROM outlets WHERE territory = ?
               ORDER BY route, CASE WHEN sequence IS NULL THEN 1 ELSE 0 END, sequence, outlet""",
            (territory_filter,),
        )
    elif group_filter and group_filter != "All":
        cur.execute(
            """SELECT o.region, o.area, o.sa, o.territory, o.route_code, o.route, o.sequence,
                      o.outlet_code, o.outlet, o.category
               FROM outlets o JOIN territories t ON t.territory = o.territory
               WHERE t.group_name = ?
               ORDER BY o.territory, o.route, CASE WHEN o.sequence IS NULL THEN 1 ELSE 0 END, o.sequence, o.outlet""",
            (group_filter,),
        )
    else:
        cur.execute(
            """SELECT region, area, sa, territory, route_code, route, sequence, outlet_code, outlet, category
               FROM outlets
               ORDER BY territory, route, CASE WHEN sequence IS NULL THEN 1 ELSE 0 END, sequence, outlet"""
        )
    rows = cur.fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Re-sequencing outlets (clean up gaps / unsequenced entries per route)
# ---------------------------------------------------------------------------

def get_route_pairs_for_scope(group_filter="All", territory_filter="All", route_filter="All"):
    """
    Resolves a Group/Territory/Route selection into the concrete list of
    (territory, route) pairs it refers to. Most specific wins:
      - route_filter not "All" (with territory_filter specific): just that
        one route.
      - territory_filter not "All": every route in that territory.
      - group_filter not "All": every route in every territory under that
        responsible-person Group.
      - everything "All": every route across every territory.
    Shared by resequence_outlets() and the SFA Automation queue builder so
    both interpret the same Group/Territory/Route selection identically.
    """
    conn = get_connection()
    cur = conn.cursor()
    if territory_filter and territory_filter != "All" and route_filter and route_filter != "All":
        cur.execute(
            "SELECT DISTINCT territory, route FROM outlets WHERE territory = ? AND route = ?",
            (territory_filter, route_filter),
        )
    elif territory_filter and territory_filter != "All":
        cur.execute(
            "SELECT DISTINCT territory, route FROM outlets WHERE territory = ? ORDER BY route",
            (territory_filter,),
        )
    elif group_filter and group_filter != "All":
        cur.execute(
            """SELECT DISTINCT o.territory, o.route FROM outlets o
               JOIN territories t ON t.territory = o.territory
               WHERE t.group_name = ?
               ORDER BY o.territory, o.route""",
            (group_filter,),
        )
    else:
        cur.execute("SELECT DISTINCT territory, route FROM outlets ORDER BY territory, route")
    route_pairs = cur.fetchall()
    conn.close()
    return route_pairs


def resequence_outlets(group_filter="All", territory_filter="All", route_filter="All"):
    """
    Renumbers the Sequence column to a clean 1..N per route, without
    changing anyone's relative order: outlets are re-read in their current
    order (existing sequence ascending, unsequenced '-' entries last, then
    by outlet name), and simply given new consecutive numbers 1, 2, 3, ...
    in that same order. This fixes gaps and assigns numbers to previously
    unsequenced outlets, but never reorders an outlet relative to another
    that already had a valid sequence.

    Scope follows get_route_pairs_for_scope() — see there for details.

    Returns a dict {(territory, route): outlet_count} summarizing what was
    re-sequenced.
    """
    route_pairs = get_route_pairs_for_scope(group_filter, territory_filter, route_filter)

    conn = get_connection()
    cur = conn.cursor()
    summary = {}
    for territory, route in route_pairs:
        cur.execute(
            """SELECT outlet_code FROM outlets WHERE territory = ? AND route = ?
               ORDER BY CASE WHEN sequence IS NULL THEN 1 ELSE 0 END, sequence, outlet""",
            (territory, route),
        )
        outlet_codes = [r[0] for r in cur.fetchall()]
        cur.executemany(
            "UPDATE outlets SET sequence = ? WHERE outlet_code = ?",
            [(i, code) for i, code in enumerate(outlet_codes, start=1)],
        )
        summary[(territory, route)] = len(outlet_codes)

    conn.commit()
    conn.close()
    return summary
