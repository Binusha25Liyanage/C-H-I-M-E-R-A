"""
sfa_exports.py
Reads RAW, unmodified SFA (SalesPro) Excel exports — no manual cleaning or
merging required — and turns them into clean records.

Why this exists: the manual process (copying Invoices + Sales Orders into
one "INV + Preselling" sheet, and two Outlet Wise SKU exports into one
sheet) was where data got lost. On 2026-10-26 the merged sheet had a blank
TERRITORY on every sales order, so every pre-selling bill silently vanished
from the Hourly and Pivot reports. Reading the raw exports directly removes
that step entirely.

What a raw SFA export looks like:
    rows 0-4   company banner + report title (e.g. "Invoices")
    rows 6-7   filter banner as key/value cells ("Invoice Type", "Van
               selling Invoice", "Date From", "2026-10-26", ...)
    row  9     column headers
    rows 10..  data
    last row   a totals row (only numbers, no document number) — must NOT
               be treated as data
Numbers sometimes arrive as text with thousands separators ("1,650.00").

Every export is identified by its headers (not its file name), and its
banner is checked against what the report is supposed to contain, so a
wrong download (wrong date, wrong report type) is rejected instead of
quietly producing a wrong report.
"""

import os
import re
from datetime import datetime

import openpyxl


class ExportError(Exception):
    """Raised when a file can't be used — always with a human-readable reason."""


# ---------------------------------------------------------------------------
# Dataset definitions
# ---------------------------------------------------------------------------
# Each dataset Chimera understands. `anchor` headers identify the file;
# `required` headers must be present to use it.
DATASETS = {
    "invoices": {
        "label": "Invoices (Van Selling)",
        "anchor": {"INV NUMBER", "SO NUMBER", "DELIVERY"},
        "required": ["INV NUMBER", "TERRITORY", "CUSTOMER", "FINAL NET AMOUNT",
                     "CREATED DATE", "CREATED TIME"],
    },
    "sales_orders": {
        "label": "Sales Orders (Pending)",
        "anchor": {"SO NUMBER", "INVOICE STATUS", "CREATED TAB TIME"},
        "required": ["SO NUMBER", "TERRITORY", "CUSTOMER", "NET AMOUNT",
                     "CREATED TAB DATE", "CREATED TAB TIME"],
    },
    "sku_van": {
        "label": "Outlet Wise SKU — Van Selling",
        "anchor": {"INVOICE / SO NUMBER", "PRODUCT", "SALES VALUE (NET SALES)"},
        "required": ["TERRITORY", "INVOICE / SO NUMBER", "PRODUCT",
                     "TOTAL UNITS", "SALES VALUE (NET SALES)"],
    },
    "sku_pre": {
        "label": "Outlet Wise SKU — Pre Sales Order (Pending)",
        "anchor": {"INVOICE / SO NUMBER", "PRODUCT", "SALES VALUE (NET SALES)"},
        "required": ["TERRITORY", "INVOICE / SO NUMBER", "PRODUCT",
                     "TOTAL UNITS", "SALES VALUE (NET SALES)"],
    },
    "unproductive": {
        "label": "Unproductive Visits",
        "anchor": {"UNPRODUCTIVE NO", "REASON"},
        "required": ["UNPRODUCTIVE NO", "TERRITORY", "CUSTOMER",
                     "UNPRODUCTIVED DATE", "UNPRODUCTIVED TIME"],
    },
}

# Header names that can mark the header row of any supported export.
_HEADER_MARKERS = {"INV NUMBER", "SO NUMBER", "UNPRODUCTIVE NO", "INVOICE / SO NUMBER"}

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


# ---------------------------------------------------------------------------
# Small value parsers
# ---------------------------------------------------------------------------
def to_str(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("-", "nan", "None") else s


def to_number(v):
    """'1,650.00' -> 1650.0, 310 -> 310.0, '-'/'' -> 0.0. Raises on junk."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-"):
        return 0.0
    return float(s)


def to_iso_date(v):
    """Accepts datetime, '2026-10-26', 'October 26, 2026', '26/10/2026'. Returns 'YYYY-MM-DD' or ''."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$", s)
    if m and m[1].lower() in _MONTHS:
        return f"{int(m[3]):04d}-{_MONTHS[m[1].lower()]:02d}-{int(m[2]):02d}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{int(m[3]):04d}-{int(m[2]):02d}-{int(m[1]):02d}"
    return ""


def to_hhmm(v):
    """'10:29 AM' / '9:41 AM' / '14:05' / time object -> '10:29'. Returns '' if unreadable."""
    if v is None:
        return ""
    if hasattr(v, "hour") and hasattr(v, "minute"):
        return f"{v.hour:02d}:{v.minute:02d}"
    s = str(v).strip().upper()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$", s)
    if not m:
        return ""
    h, mi, ap = int(m[1]), int(m[2]), m[3]
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return ""
    return f"{h:02d}:{mi:02d}"


def hhmm_to_minutes(hhmm):
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------------------
# Raw workbook reading
# ---------------------------------------------------------------------------
def _read_rows(path):
    if not os.path.exists(path):
        raise ExportError(f"File not found: {path}")
    if not path.lower().endswith((".xlsx", ".xlsm")):
        raise ExportError(f"{os.path.basename(path)}: only .xlsx exports are supported "
                          "(SFA exports are .xlsx — re-download rather than converting).")
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise ExportError(f"{os.path.basename(path)}: could not open as Excel ({e}).")
    try:
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _find_header_row(rows, max_scan=30):
    # The filter banner can contain the same words (e.g. an "SO Number"
    # filter cell), so a banner row — recognisable by its "Not Selected" /
    # "Generated by" cells — is never accepted as the header row.
    banner_words = {"NOT SELECTED", "GENERATED BY", "GENERATED AT"}
    for i, r in enumerate(rows[:max_scan]):
        cells = {to_str(c).upper() for c in r if c is not None}
        if cells & _HEADER_MARKERS and not cells & banner_words and len(cells) >= 5:
            return i
    return None


def _parse_banner(rows, header_idx):
    """
    The filter banner is a run of non-empty cells alternating key, value.
    Returns ({key: value}, title). Files without a banner (e.g. the
    Unproductive Visit export, or an already-cleaned file) return ({}, "").
    """
    banner, title = {}, ""
    for r in rows[:header_idx]:
        cells = [to_str(c) for c in r if c is not None and to_str(c) != ""]
        if not cells:
            continue
        if len(cells) == 1:
            # Single-cell rows are company name / address / report title;
            # the report title is the last single-cell row before the filters.
            title = cells[0]
            continue
        for k, v in zip(cells[0::2], cells[1::2]):
            banner[k] = v
    return banner, title


def detect_dataset(headers, banner):
    """
    Decides which dataset a file is, from its headers and banner.
    The two Outlet Wise SKU exports share identical headers — they are told
    apart by the banner's Report Type, or failing that by the file's own
    SALES REP MODEL column (handled by the caller via `model_hint`).
    """
    hset = set(headers)
    if DATASETS["unproductive"]["anchor"] <= hset:
        return "unproductive"
    if DATASETS["sku_van"]["anchor"] <= hset:
        rtype = banner.get("Report Type", "").lower()
        if "pre" in rtype:
            return "sku_pre"
        if "van" in rtype:
            return "sku_van"
        return "sku_?"
    if DATASETS["invoices"]["anchor"] <= hset:
        return "invoices"
    if DATASETS["sales_orders"]["anchor"] <= hset:
        return "sales_orders"
    if "INV NUMBER" in hset and "DELIVERY" in hset:
        # Legacy hand-merged "INV + Preselling" sheet (no SO NUMBER column).
        return "legacy_inv_pre"
    return None


def read_export(path):
    """
    Reads one raw SFA export.
    Returns {
        dataset, label, path, banner, title, headers,
        records: [dict per data row, keyed by header],
        dropped_rows: int (blank/totals rows removed),
        data_date: 'YYYY-MM-DD' or '' (from banner when it covers one day),
        generated_at: banner's 'Generated at' text or '',
        warnings: [str],
    }
    Raises ExportError with a clear message if the file can't be used.
    """
    name = os.path.basename(path)
    rows = _read_rows(path)
    hdr_idx = _find_header_row(rows)
    if hdr_idx is None:
        raise ExportError(f"{name}: no recognisable SFA header row found "
                          "(looked for INV NUMBER / SO NUMBER / UNPRODUCTIVE NO / INVOICE / SO NUMBER).")

    headers = [to_str(h).upper() for h in rows[hdr_idx]]
    banner, title = _parse_banner(rows, hdr_idx)
    dataset = detect_dataset(headers, banner)
    if dataset is None:
        raise ExportError(f"{name}: headers don't match any supported SFA report.")

    warnings = []
    if dataset == "sku_?":
        # No banner to tell Van from Pre — fall back to the rows' own model column.
        models = {to_str(r[headers.index("SALES REP MODEL")]).lower()
                  for r in rows[hdr_idx + 1:] if "SALES REP MODEL" in headers
                  and r and len(r) > headers.index("SALES REP MODEL")
                  and to_str(r[headers.index("SALES REP MODEL")])}
        if models and all("van" in m for m in models):
            dataset = "sku_van"
        elif models and all("pre" in m for m in models):
            dataset = "sku_pre"
        else:
            dataset = "sku_mixed"
            warnings.append("SKU file has no Report Type banner and mixes Van/Pre rows — "
                            "rows will be split by their SALES REP MODEL column.")

    spec = DATASETS.get(dataset)
    if spec:
        missing = [c for c in spec["required"] if c not in headers]
        if missing:
            raise ExportError(f"{name}: looks like {spec['label']} but is missing column(s): "
                              f"{', '.join(missing)}")

    key_col = {"invoices": "INV NUMBER", "sales_orders": "SO NUMBER",
               "unproductive": "UNPRODUCTIVE NO", "legacy_inv_pre": "INV NUMBER"}.get(
        dataset, "INVOICE / SO NUMBER")
    key_idx = headers.index(key_col)

    records, dropped = [], 0
    for r in rows[hdr_idx + 1:]:
        r = list(r) + [None] * (len(headers) - len(r))
        if not to_str(r[key_idx]):
            # Blank spacer rows and the trailing totals row have no document number.
            if any(to_str(c) for c in r):
                dropped += 1
            continue
        records.append({h: r[i] for i, h in enumerate(headers) if h})

    # Date the export covers, from its banner (single-day exports only).
    d_from = to_iso_date(banner.get("Date From") or banner.get("From Date"))
    d_to = to_iso_date(banner.get("Date To") or banner.get("To Date"))
    data_date = d_from if d_from and d_from == d_to else ""
    if d_from and d_to and d_from != d_to:
        warnings.append(f"Export covers {d_from} to {d_to}; rows will be dated individually.")

    # Filter sanity checks — a wrong filter means the wrong data.
    if dataset == "invoices":
        itype = banner.get("Invoice Type", "")
        if itype and "van" not in itype.lower():
            warnings.append(f"Invoice Type filter is '{itype}', not Van selling — "
                            "these invoices will be treated as their own bills.")
    if dataset == "sales_orders":
        status = banner.get("Invoice Status", "")
        if status and status.lower() != "pending":
            warnings.append(f"Invoice Status filter is '{status}' (expected 'Pending'). "
                            "Orders that are already invoiced may be double-counted if "
                            "their invoices are also imported.")

    return {
        "dataset": dataset,
        "label": DATASETS.get(dataset, {}).get("label", dataset),
        "path": path,
        "banner": banner,
        "title": title,
        "headers": headers,
        "records": records,
        "dropped_rows": dropped,
        "data_date": data_date,
        "generated_at": banner.get("Generated at", ""),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Normalisers: raw records -> clean rows for the database
# ---------------------------------------------------------------------------
def _row_date(raw_date, fallback, warnings, label, doc):
    d = to_iso_date(raw_date) or fallback
    if not d:
        warnings.append(f"{label} {doc}: no readable date, row skipped.")
    return d


def normalise_bills(export):
    """Invoices / Sales Orders / legacy merged sheet -> bill rows."""
    ds, out, warn = export["dataset"], [], []
    mismatched_dates = 0
    for r in export["records"]:
        if ds == "invoices" or (ds == "legacy_inv_pre" and to_str(r.get("INV NUMBER")).upper().startswith("INV")):
            doc = to_str(r.get("INV NUMBER"))
            created_date = to_iso_date(r.get("CREATED DATE"))
            hhmm = to_hhmm(r.get("CREATED TIME"))
            amount = to_number(r.get("FINAL NET AMOUNT"))
            kind, so_no = "INV", to_str(r.get("SO NUMBER"))
            status = to_str(r.get("DELIVERY"))
        else:
            doc = to_str(r.get("SO NUMBER") or r.get("INV NUMBER"))
            created_date = to_iso_date(r.get("CREATED TAB DATE") or r.get("CREATED DATE"))
            hhmm = to_hhmm(r.get("CREATED TAB TIME") or r.get("CREATED TIME"))
            amount = to_number(r.get("NET AMOUNT") if ds == "sales_orders" else r.get("FINAL NET AMOUNT"))
            kind, so_no = "SO", doc
            status = to_str(r.get("INVOICE STATUS") or r.get("DELIVERY"))

        bill_date = export["data_date"] or created_date
        if not bill_date:
            warn.append(f"{doc}: no readable date, skipped.")
            continue
        if export["data_date"] and created_date and created_date != export["data_date"]:
            mismatched_dates += 1
        out.append({
            "bill_no": doc, "bill_date": bill_date, "kind": kind, "so_number": so_no,
            "territory": to_str(r.get("TERRITORY")), "rep": to_str(r.get("SALES REP")),
            "route": to_str(r.get("ROUTE")), "customer": to_str(r.get("CUSTOMER")),
            "outlet_code": to_str(r.get("OUTLET CODE")), "amount": amount,
            "created_time": hhmm, "minutes": hhmm_to_minutes(hhmm), "status": status,
        })
        if not hhmm:
            warn.append(f"{doc}: unreadable created time — it will be counted in totals "
                        "but can't be placed in an hourly bucket.")
    if mismatched_dates:
        warn.append(f"{mismatched_dates} bill(s) were created on a different day than the "
                    f"export's date ({export['data_date']}); they are reported under the export date.")
    return out, warn


def normalise_sku(export):
    out, warn = [], []
    for r in export["records"]:
        model_raw = to_str(r.get("SALES REP MODEL")).lower()
        if export["dataset"] == "sku_van":
            model = "van"
        elif export["dataset"] == "sku_pre":
            model = "pre"
        else:
            model = "van" if "van" in model_raw else "pre"
        d = to_iso_date(r.get("INVOICE / TRANSACTION DATE")) or export["data_date"]
        if not d:
            warn.append(f"{to_str(r.get('INVOICE / SO NUMBER'))}: no date, skipped.")
            continue
        try:
            value = to_number(r.get("SALES VALUE (NET SALES)"))
            units = to_number(r.get("TOTAL UNITS"))
        except ValueError:
            warn.append(f"{to_str(r.get('INVOICE / SO NUMBER'))}: unreadable value/units, skipped.")
            continue
        out.append({
            "line_date": d, "model": model, "territory": to_str(r.get("TERRITORY")),
            "asm": to_str(r.get("ASM")), "rep": to_str(r.get("SALES REP")),
            "outlet_code": to_str(r.get("OUTLET CODE")), "outlet": to_str(r.get("OUTLET")),
            "bill_no": to_str(r.get("INVOICE / SO NUMBER")),
            "product_code": to_str(r.get("PRODUCT CODE")), "product": to_str(r.get("PRODUCT")),
            "units": units, "value": value,
        })
    return out, warn


def normalise_unproductive(export):
    out, warn = [], []
    for r in export["records"]:
        doc = to_str(r.get("UNPRODUCTIVE NO"))
        d = to_iso_date(r.get("UNPRODUCTIVED DATE")) or export["data_date"]
        if not d:
            warn.append(f"{doc}: no readable date, skipped.")
            continue
        hhmm = to_hhmm(r.get("UNPRODUCTIVED TIME"))
        out.append({
            "unproductive_no": doc, "visit_date": d, "territory": to_str(r.get("TERRITORY")),
            "rep": to_str(r.get("REP NAME")), "rep_model": to_str(r.get("REP MODEL")),
            "route": to_str(r.get("ROUTE")), "customer": to_str(r.get("CUSTOMER")),
            "reason": to_str(r.get("REASON")), "visit_time": hhmm,
            "minutes": hhmm_to_minutes(hhmm),
        })
    return out, warn
