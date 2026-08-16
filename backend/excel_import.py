"""
excel_import.py
Reads the monthly Itinerary workbook and the Territories workbook and
converts them into rows ready for db.py.
"""

import re
import calendar
import pandas as pd

DAY_COL_RE = re.compile(r"^\s*(\d{1,2})\s")

FIXED_COLS = ["Year", "Month", "Region", "Area", "Superviser Area",
              "Territory", "Distributor", "Sales Rep"]


class ImportError_(Exception):
    pass


def _safe_str(value):
    """Safely stringify a pandas cell value, treating NaN/None as blank."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _find_day_columns(columns):
    day_cols = []
    for col in columns:
        m = DAY_COL_RE.match(str(col))
        if m:
            day_cols.append((int(m.group(1)), col))
    day_cols.sort(key=lambda x: x[0])
    return day_cols


def parse_itinerary_workbook(path):
    """
    Returns:
        rows: list of dicts ready for db.insert_itinerary_rows
        territories_seen: set of territory names in the file
        year_months: set of (year, month) tuples present in the file
        warnings: list of human-readable warning strings
    """
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in FIXED_COLS if c not in df.columns]
    if missing:
        raise ImportError_(
            f"The itinerary file is missing expected column(s): {', '.join(missing)}"
        )

    day_cols = _find_day_columns(df.columns)
    if not day_cols:
        raise ImportError_("No day columns (e.g. '01 Mon', '02 Tue', ...) were found in the file.")

    rows = []
    territories_seen = set()
    year_months = set()
    warnings = []

    for idx, r in df.iterrows():
        territory = _safe_str(r.get("Territory"))
        if not territory or territory == "-":
            continue

        try:
            year = int(float(r.get("Year")))
            month = int(float(r.get("Month")))
        except (TypeError, ValueError):
            warnings.append(f"Row {idx + 2}: could not read Year/Month, row skipped.")
            continue

        if not (1 <= month <= 12):
            warnings.append(f"Row {idx + 2}: invalid month '{r.get('Month')}', row skipped.")
            continue

        territories_seen.add(territory)
        year_months.add((year, month))

        last_day_in_month = calendar.monthrange(year, month)[1]

        for day_num, col_name in day_cols:
            if day_num > last_day_in_month:
                # e.g. a '31' column on a 30-day month — expected, just skip
                continue

            route = r.get(col_name)
            if route is None:
                continue
            route = str(route).strip()
            if route == "" or route == "-" or route.lower() == "nan":
                continue

            visit_date = f"{year:04d}-{month:02d}-{day_num:02d}"

            rows.append({
                "year": year,
                "month": month,
                "region": _safe_str(r.get("Region")),
                "area": _safe_str(r.get("Area")),
                "superviser_area": _safe_str(r.get("Superviser Area")),
                "territory": territory,
                "distributor": _safe_str(r.get("Distributor")),
                "sales_rep": _safe_str(r.get("Sales Rep")),
                "visit_date": visit_date,
                "route": route,
            })

    return rows, territories_seen, year_months, warnings


VALID_SALE_MODELS = {"van selling": "Van Selling", "pre selling": "Pre Selling"}
SALE_MODEL_COL_CANDIDATES = ["Sale Model", "Sales Model", "Sale Rep Model", "Model"]


def parse_territories_workbook(path):
    """
    Returns:
        rows: list of (territory, group_name, sale_model) tuples ready for
              db.upsert_territories. sale_model is "Van Selling", "Pre Selling",
              or None if the file has no such column / the cell is blank.
        warnings: list of human-readable warning strings
    """
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    if "Group" not in df.columns or "Territory" not in df.columns:
        raise ImportError_(
            "The territories file must have 'Group' and 'Territory' columns."
        )

    sale_model_col = next((c for c in SALE_MODEL_COL_CANDIDATES if c in df.columns), None)

    rows = []
    warnings = []
    for idx, r in df.iterrows():
        territory = _safe_str(r.get("Territory"))
        group = _safe_str(r.get("Group"))
        if not territory or territory == "-":
            continue
        if not group:
            warnings.append(f"Row {idx + 2}: territory '{territory}' has no group, row skipped.")
            continue

        sale_model = None
        if sale_model_col:
            raw = _safe_str(r.get(sale_model_col))
            if raw:
                normalized = VALID_SALE_MODELS.get(raw.lower())
                if normalized:
                    sale_model = normalized
                else:
                    warnings.append(
                        f"Row {idx + 2}: territory '{territory}' has an unrecognized "
                        f"sale model '{raw}' (expected 'Van Selling' or 'Pre Selling'); "
                        f"stored as entered."
                    )
                    sale_model = raw

        rows.append((territory, group, sale_model))

    if not sale_model_col:
        warnings.append(
            "No sale model column found (expected a header like 'Sale Model'); "
            "territories were imported without a sale model. Add that column and "
            "re-upload to set it."
        )

    return rows, warnings


# ---------------------------------------------------------------------------
# Van Selling — Invoices workbook
# ---------------------------------------------------------------------------

INVOICE_FIXED_COLS = ["INV NUMBER", "INV DATE", "DISTRIBUTOR", "SALES REP",
                       "TERRITORY", "ROUTE", "CUSTOMER"]


def parse_invoices_workbook(path):
    """
    Returns:
        rows: list of dicts ready for db.insert_van_sales_rows
        warnings: list of human-readable warning strings
    """
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in INVOICE_FIXED_COLS if c not in df.columns]
    if missing:
        raise ImportError_(
            f"The invoices file is missing expected column(s): {', '.join(missing)}"
        )

    value_col = None
    for candidate in ["FINAL NET AMOUNT", "NET AMOUNT"]:
        if candidate in df.columns:
            value_col = candidate
            break
    if value_col is None:
        raise ImportError_("The invoices file is missing a 'FINAL NET AMOUNT' or 'NET AMOUNT' column.")

    rows = []
    warnings = []

    for idx, r in df.iterrows():
        inv_number = _safe_str(r.get("INV NUMBER"))
        if not inv_number or inv_number == "-":
            continue

        inv_date_raw = _safe_str(r.get("INV DATE"))
        inv_date = _parse_date_flexible(inv_date_raw)
        if not inv_date:
            warnings.append(f"Row {idx + 2}: could not read INV DATE '{inv_date_raw}', row skipped.")
            continue

        territory = _safe_str(r.get("TERRITORY"))
        route = _safe_str(r.get("ROUTE"))
        if not territory or not route:
            warnings.append(f"Row {idx + 2}: missing territory/route, row skipped.")
            continue

        value_raw = r.get(value_col)
        try:
            sales_value = float(value_raw) if value_raw not in (None, "", "-") else 0.0
        except (TypeError, ValueError):
            sales_value = 0.0
            warnings.append(f"Row {idx + 2}: could not read {value_col} '{value_raw}', treated as 0.")

        rows.append({
            "inv_number": inv_number,
            "so_number": _safe_str(r.get("SO NUMBER")),
            "inv_date": inv_date,
            "distributor": _safe_str(r.get("DISTRIBUTOR")),
            "sales_rep": _safe_str(r.get("SALES REP")),
            "territory": territory,
            "route": route,
            "customer_category": _safe_str(r.get("CUSTOMER CATEGORY")),
            "outlet_code": _safe_str(r.get("OUTLET CODE")),
            "customer": _safe_str(r.get("CUSTOMER")),
            "sales_value": sales_value,
        })

    return rows, warnings


# ---------------------------------------------------------------------------
# Pre Selling — Sales Order workbook
# ---------------------------------------------------------------------------

SALES_ORDER_FIXED_COLS = ["SO NUMBER", "TERRITORY", "ROUTE", "CUSTOMER",
                           "NET AMOUNT", "CREATED TAB DATE"]


def _find_header_row(path, marker="SO NUMBER", max_scan=25):
    """
    The Sales Order export has a report header block (company name, filter
    summary, etc.) before the real column headers. Scan the first N rows to
    find the row whose first cell is the marker, and return its 0-based index.
    """
    raw = pd.read_excel(path, sheet_name=0, header=None, nrows=max_scan, dtype=str)
    for i in range(len(raw)):
        first_cell = _safe_str(raw.iloc[i, 0])
        if first_cell == marker:
            return i
    return None


def parse_sales_orders_workbook(path):
    """
    Returns:
        rows: list of dicts ready for db.insert_pre_sales_rows
        warnings: list of human-readable warning strings
    """
    header_row = _find_header_row(path)
    if header_row is None:
        raise ImportError_(
            "Could not find the data header row (expected a row starting with 'SO NUMBER') "
            "in the sales order file."
        )

    df = pd.read_excel(path, sheet_name=0, header=header_row, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in SALES_ORDER_FIXED_COLS if c not in df.columns]
    if missing:
        raise ImportError_(
            f"The sales order file is missing expected column(s): {', '.join(missing)}"
        )

    rows = []
    warnings = []

    for idx, r in df.iterrows():
        so_number = _safe_str(r.get("SO NUMBER"))
        if not so_number or so_number == "-":
            continue

        order_date_raw = _safe_str(r.get("CREATED TAB DATE")) or _safe_str(r.get("CREATED SERVER DATE"))
        order_date = _parse_date_flexible(order_date_raw)
        if not order_date:
            warnings.append(f"Row {idx + 2}: could not read order date '{order_date_raw}', row skipped.")
            continue

        territory = _safe_str(r.get("TERRITORY"))
        route = _safe_str(r.get("ROUTE"))
        if not territory or not route:
            warnings.append(f"Row {idx + 2}: missing territory/route, row skipped.")
            continue

        value_raw = r.get("NET AMOUNT")
        try:
            sales_value = float(value_raw) if value_raw not in (None, "", "-") else 0.0
        except (TypeError, ValueError):
            sales_value = 0.0
            warnings.append(f"Row {idx + 2}: could not read NET AMOUNT '{value_raw}', treated as 0.")

        rows.append({
            "so_number": so_number,
            "order_date": order_date,
            "distributor": _safe_str(r.get("DISTRIBUTOR")),
            "sales_rep": _safe_str(r.get("SALES REP")),
            "territory": territory,
            "route": route,
            "customer": _safe_str(r.get("CUSTOMER")),
            "sales_value": sales_value,
            "invoice_status": _safe_str(r.get("INVOICE STATUS")),
        })

    return rows, warnings


def _parse_date_flexible(raw):
    """Parses either 'YYYY-MM-DD' or 'Month D, YYYY' into an ISO date string."""
    if not raw:
        return None
    try:
        return pd.to_datetime(raw).date().isoformat()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Outlet / customer master list (territory + route wise, sequenced)
# ---------------------------------------------------------------------------

OUTLET_FIXED_COLS = ["TERRITORY", "ROUTE", "SEQUENCE", "OUTLET CODE", "OUTLET"]


def parse_outlets_workbook(path):
    """
    Returns:
        rows: list of dicts ready for db.insert_outlets_rows
        warnings: list of human-readable warning strings
    """
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in OUTLET_FIXED_COLS if c not in df.columns]
    if missing:
        raise ImportError_(
            f"The outlet file is missing expected column(s): {', '.join(missing)}"
        )

    rows = []
    warnings = []
    seen_codes = set()

    for idx, r in df.iterrows():
        outlet_code = _safe_str(r.get("OUTLET CODE"))
        territory = _safe_str(r.get("TERRITORY"))
        route = _safe_str(r.get("ROUTE"))
        outlet = _safe_str(r.get("OUTLET"))

        if not outlet_code or not territory or not route or not outlet:
            warnings.append(f"Row {idx + 2}: missing outlet code/territory/route/outlet, row skipped.")
            continue

        if outlet_code in seen_codes:
            warnings.append(f"Row {idx + 2}: duplicate outlet code '{outlet_code}' in this file, last one wins.")
        seen_codes.add(outlet_code)

        seq_raw = _safe_str(r.get("SEQUENCE"))
        sequence = None
        if seq_raw and seq_raw != "-":
            try:
                sequence = int(float(seq_raw))
            except ValueError:
                warnings.append(f"Row {idx + 2}: unrecognized sequence '{seq_raw}' for outlet '{outlet}', treated as unsequenced.")

        rows.append({
            "region": _safe_str(r.get("REGION")),
            "area": _safe_str(r.get("AREA")),
            "sa": _safe_str(r.get("SA")),
            "territory": territory,
            "route_code": _safe_str(r.get("ROUTE CODE")),
            "route": route,
            "sequence": sequence,
            "outlet_code": outlet_code,
            "outlet": outlet,
            "category": _safe_str(r.get("CATEGORY")),
        })

    return rows, warnings


# ---------------------------------------------------------------------------
# SKU-wise (product-line) sales — Van Selling and Pre Selling exports
# ---------------------------------------------------------------------------

SKU_FIXED_COLS = ["TERRITORY", "SALES REP MODEL", "OUTLET CODE", "OUTLET",
                   "PRODUCT CODE", "PRODUCT", "TOTAL UNITS", "SALES VALUE (NET SALES)"]


def parse_sku_sales_workbook(path, expected_model, report_date):
    """
    expected_model: "Van Selling" or "Pre Selling" — rows whose own
        SALES REP MODEL column doesn't match this (case-insensitively,
        'pre' matches 'Pre Order'/'Pre Selling') are skipped with a warning,
        since these exports can contain a few stray rows from the other model.
    report_date: ISO date string to tag every row with — the file itself
        carries no date column, so the caller (the app) supplies it.

    Returns:
        rows: list of dicts ready for db.insert_product_sales_rows
        warnings: list of human-readable warning strings
    """
    df = pd.read_excel(path, sheet_name=0, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in SKU_FIXED_COLS if c not in df.columns]
    if missing:
        raise ImportError_(
            f"The SKU sales file is missing expected column(s): {', '.join(missing)}"
        )

    expected_key = "van" if expected_model.strip().lower().startswith("van") else "pre"

    rows = []
    warnings = []
    skipped_other_model = 0

    for idx, r in df.iterrows():
        model_raw = _safe_str(r.get("SALES REP MODEL")).lower()
        is_van = "van" in model_raw
        is_pre = "pre" in model_raw
        if expected_key == "van" and not is_van:
            skipped_other_model += 1
            continue
        if expected_key == "pre" and not is_pre:
            skipped_other_model += 1
            continue

        units_raw = r.get("TOTAL UNITS")
        try:
            units = float(units_raw) if units_raw not in (None, "", "-") else 0.0
        except (TypeError, ValueError):
            units = 0.0

        if units == 0:
            continue  # catalog placeholder line (product listed, nothing sold or returned)

        outlet_code = _safe_str(r.get("OUTLET CODE"))
        territory = _safe_str(r.get("TERRITORY"))
        product = _safe_str(r.get("PRODUCT"))
        if not outlet_code or not territory or not product:
            warnings.append(f"Row {idx + 2}: missing outlet code/territory/product, row skipped.")
            continue

        value_raw = r.get("SALES VALUE (NET SALES)")
        try:
            sales_value = float(value_raw) if value_raw not in (None, "", "-") else 0.0
        except (TypeError, ValueError):
            sales_value = 0.0
            warnings.append(f"Row {idx + 2}: could not read sales value '{value_raw}', treated as 0.")

        rows.append({
            "sale_model": expected_model,
            "report_date": report_date,
            "territory": territory,
            "outlet_code": outlet_code,
            "product_code": _safe_str(r.get("PRODUCT CODE")),
            "product": product,
            "units": units,
            "sales_value": sales_value,
            "distributor": _safe_str(r.get("DISTRIBUTOR")),
            "sales_rep": _safe_str(r.get("SALES REP")),
        })

    if skipped_other_model:
        warnings.append(
            f"{skipped_other_model} row(s) had a different Sales Rep Model than expected "
            f"('{expected_model}') and were skipped."
        )

    return rows, warnings


# ---------------------------------------------------------------------------
# Writing current data back out to Excel (for edit-and-reupload workflows)
# ---------------------------------------------------------------------------

def write_territories_workbook(path, rows):
    """
    rows: list of (territory, group_name, sale_model) — same shape as
    db.export_territories_rows(). Writes an .xlsx matching the exact
    column layout parse_territories_workbook() expects, so it can be
    edited and re-uploaded directly.
    """
    df = pd.DataFrame(
        [{"No": i + 1, "Group": g, "Territory": t, "Sale Model": m or ""}
         for i, (t, g, m) in enumerate(rows)]
    )
    df.to_excel(path, index=False)


def write_outlets_workbook(path, rows):
    """
    rows: list of (region, area, sa, territory, route_code, route, sequence,
    outlet_code, outlet, category) — same shape as db.export_outlets_rows().
    Writes an .xlsx matching the exact column layout
    parse_outlets_workbook() expects, so it can be edited (e.g. change
    Sequence) and re-uploaded directly. Sequence is written as blank
    (not 0) when unset, matching the '-' convention used on import.
    """
    out_rows = []
    for region, area, sa, territory, route_code, route, sequence, outlet_code, outlet, category in rows:
        out_rows.append({
            "REGION": region or "",
            "AREA": area or "",
            "SA": sa or "",
            "TERRITORY": territory,
            "ROUTE CODE": route_code or "",
            "ROUTE": route,
            "SEQUENCE": sequence if sequence is not None else "-",
            "OUTLET CODE": outlet_code,
            "OUTLET": outlet,
            "CATEGORY": category or "",
        })
    df = pd.DataFrame(out_rows)
    df.to_excel(path, index=False)
