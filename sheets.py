"""
sheets.py — Reads client inventory from Google Sheets.

Python version of class-fba-sheets-api.php. Finds the Ordering Template
table inside the tab (the sheet contains multiple tables), maps columns
by header name, and returns clean rows.
"""

import streamlit as st
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES_READ = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

HEADER_MAP = {
    "brand_code":               ["Brand Code"],
    "marketplace":              ["Marketplace"],
    "sku_status":               ["SKU Status"],
    "title":                    ["Title"],
    "asin":                     ["ASIN"],
    "sku":                      ["SKU"],
    "fulfillable":              ["Fulfillable"],
    "inbound_working":          ["Working"],
    "inbound_shipped":          ["Shipped"],
    "inbound_receiving":        ["Receiving"],
    "fc_transfer":              ["FC Transfer"],
    "fc_processing":            ["FC Processing"],
    "fulfillable_plus_inbound": ["Fulfillable + Inbound"],
    "snapshot_date":            ["Last FBA Inventory snapshot Available", "Snapshot Date"],
    "units_7day":               ["7 Day Units Shipped", "7 Day Units Sold"],
    "units_30day":              ["30 Day Units Shipped", "30 Day Units Sold"],
    "units_60day":              ["60 day Units Shipped", "60 Day Units Shipped", "60 Day Units Sold"],
    "units_90day":              ["90 day Units Shipped", "90 Day Units Shipped", "90 Day Units Sold"],
    "daily_avg":                ["Daily Avg. Units Sold", "Daily Avg Units Sold"],
    "remaining_doi":            ["Remaining DOI"],
    "future_doi":               ["Future DOI"],
    "replenish":                ["Replenish"],
    "order_cases":              ["Order Cases"],
    "order_units":              ["Order # Units", "Order Units"],
    "units_per_case":           ["Units per Case"],
    "pct_sales_mix":            ["% of Sales Mix", "Pct Sales Mix", "% Sales Mix"],
    "size_tier":                ["Size Tier", "size_tier", "FBA Size Tier"],
    "qty_ais_181_210":          ["qty-ais-181-to-210-days"],
    "qty_ais_211_240":          ["qty-ais-211-to-240-days"],
    "qty_ais_241_270":          ["qty-ais-241-to-270-days"],
    "qty_ais_271_300":          ["qty-ais-271-to-300-days"],
    "qty_ais_301_330":          ["qty-ais-301-to-330-days"],
    "qty_ais_331_365":          ["qty-ais-331-to-365-days"],
    "qty_ais_365_plus":         ["qty-ais-365-plus-days"],
    "inv_age_91_180":           ["inv-age-91-to-180-days", "Inv Age 91-180 Days"],
    "inv_age_181_270":          ["inv-age-181-to-270-days", "Inv Age 181-270 Days"],
    "inv_age_271_365":          ["inv-age-271-to-365-days", "Inv Age 271-365 Days"],
    "inv_age_365_plus":         ["inv-age-365-plus-days", "Inv Age 365+ Days"],
    "storage_cost_next_month":  ["Storage Cost Next Month", "storage_cost_next_month"],
}

NUMERIC_FIELDS = [
    "fulfillable", "inbound_working", "inbound_shipped", "inbound_receiving",
    "fc_transfer", "fc_processing", "fulfillable_plus_inbound",
    "units_7day", "units_30day", "units_60day", "units_90day",
    "units_per_case", "order_cases", "order_units",
    "qty_ais_181_210", "qty_ais_211_240", "qty_ais_241_270", "qty_ais_271_300",
    "qty_ais_301_330", "qty_ais_331_365", "qty_ais_365_plus",
    "inv_age_91_180", "inv_age_181_270", "inv_age_271_365", "inv_age_365_plus",
]
FLOAT_FIELDS = ["daily_avg", "pct_sales_mix", "storage_cost_next_month"]


def _credentials(scopes):
    info = dict(st.secrets["gcp_service_account"])
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


def _sheets_service(scopes=SCOPES_READ):
    return build("sheets", "v4", credentials=_credentials(scopes), cache_discovery=False)


def _to_int(v):
    try:
        return int(float(str(v).replace(",", "").replace("%", "").strip() or 0))
    except (ValueError, TypeError):
        return 0


def _to_float(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_inventory(sheet_id: str, tab_name: str) -> pd.DataFrame:
    service = _sheets_service()
    tab_ref = f"'{tab_name}'" if " " in tab_name else tab_name
    result = (
        service.spreadsheets().values()
        .get(spreadsheetId=sheet_id, range=f"{tab_ref}!A1:BF1000")
        .execute()
    )
    raw = result.get("values", [])
    if not raw:
        raise ValueError("No data returned from the sheet. Check the tab name.")

    best = None
    for i, row in enumerate(raw):
        joined = "|".join(str(c) for c in row)
        if "Brand Code" not in joined:
            continue
        if "snapshot_date" in joined:
            continue
        score = len(row)
        if "inv-age" in joined:
            score += 1000
        if "Fulfillable" in joined:
            score += 100
        if best is None or score > best[0]:
            best = (score, i, row)

    if best is None:
        raise ValueError("Could not find an 'Ordering Template' table (no 'Brand Code' header).")

    _, header_idx, header = best
    header = [str(c).strip() for c in header]

    cols = {}
    for field, candidates in HEADER_MAP.items():
        for cand in candidates:
            if cand in header:
                cols[field] = header.index(cand)
                break

    for required in ("brand_code", "asin", "fulfillable"):
        if required not in cols:
            raise ValueError(f"Sheet is missing a required column: {required}")

    rows = []
    for drow in raw[header_idx + 1:]:
        cells = [str(c).strip() for c in drow]
        if not any(cells):
            break
        brand = cells[0] if cells else ""
        if brand == "Brand Code":
            break

        def cell(field, default=""):
            idx = cols.get(field)
            if idx is None or idx >= len(cells):
                return default
            return cells[idx]

        asin = cell("asin")
        if not brand or brand == "-" or not asin or asin in ("-", "ASIN"):
            continue

        rec = {f: cell(f) for f in HEADER_MAP}
        if not rec.get("sku"):
            rec["sku"] = asin
        for f in NUMERIC_FIELDS:
            rec[f] = _to_int(rec.get(f))
        for f in FLOAT_FIELDS:
            rec[f] = _to_float(rec.get(f))
        rec["replenish"] = str(rec.get("replenish", "")).lower() in ("yes", "1", "true")
        rows.append(rec)

    if not rows:
        raise ValueError("Found the table header but no data rows under it.")
    return pd.DataFrame(rows)


def clear_inventory_cache():
    fetch_inventory.clear()


def _to_float_dim(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0.0


def _to_int_dim(v):
    try:
        return int(float(str(v).replace(",", "").strip() or 0))
    except (ValueError, TypeError):
        return 0


def fetch_sku_sheet(sheet_id: str, tab_name: str = "SKU Sheet") -> dict:
    """
    Reads the "SKU Sheet" tab (same spreadsheet as the Ordering Template) for
    per-SKU product dimensions and case pack ("Master Carton") sizing.
    Ported from class-fba-sheets-api.php's fetch_sku_sheet().

    "Longest side" / "Median side" / "Shortest side" each appear TWICE in
    this sheet — the first occurrence is the product's own dimensions, the
    second is the master carton (case pack) dimensions.

    Returns {sku: {weight_lb, longest_side, median_side, shortest_side,
                   units_per_case, cp_weight_lb, cp_longest_side,
                   cp_median_side, cp_shortest_side}}
    """
    service = _sheets_service()
    tab_ref = f"'{tab_name}'" if " " in tab_name else tab_name
    try:
        result = (
            service.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"{tab_ref}!A1:AQ500")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Could not read the '{tab_name}' tab: {e}")

    rows = result.get("values", [])
    if len(rows) < 2:
        return {}

    sku_candidates = ["amazon sku", "sku", "merchant sku", "seller sku", "asin"]
    header_row_idx = 0
    for idx, row in enumerate(rows):
        normalized = [str(h or "").strip().lower() for h in row]
        if any(c in normalized for c in sku_candidates):
            header_row_idx = idx
            break

    headers = [str(h or "").strip().lower() for h in rows[header_row_idx]]

    def col(candidates):
        for c in candidates:
            c = c.strip().lower()
            if c in headers:
                return headers.index(c)
        return None

    def col_all(candidates):
        cands = [c.strip().lower() for c in candidates]
        return [i for i, h in enumerate(headers) if h in cands]

    sku_col = col(["amazon sku", "sku", "merchant sku", "seller sku"])
    weight_col = col(["product weight (lb)", "product weight lb", "weight lb", "weight (lb)",
                      "product weight (lbs)", "weight (lbs)"])
    longest_cols = col_all(["longest side", "longest side "])
    median_cols = col_all(["median side", "median side "])
    shortest_cols = col_all(["shortest side", "shortest side "])
    longest_col = longest_cols[0] if longest_cols else None
    median_col = median_cols[0] if median_cols else None
    shortest_col = shortest_cols[0] if shortest_cols else None

    upc_col = col(["unit count per case/ box/ pallet", "unit count per case/box/pallet",
                   "unit count per case box pallet", "unit count per case",
                   "units per case box pallet", "units per case", "units per case/ box/ pallet"])
    cp_weight_col = col(["master carton / pallet weight (lbs)", "master carton/pallet weight (lbs)",
                         "carton weight (lbs)", "case weight (lbs)", "case weight lb"])
    cp_longest_col = longest_cols[1] if len(longest_cols) > 1 else None
    cp_median_col = median_cols[1] if len(median_cols) > 1 else None
    cp_shortest_col = shortest_cols[1] if len(shortest_cols) > 1 else None

    if sku_col is None:
        return {}

    def cell(row, idx):
        return row[idx] if idx is not None and idx < len(row) else ""

    results = {}
    for row in rows[header_row_idx + 1:]:
        sku = str(cell(row, sku_col) or "").strip()
        if not sku:
            continue
        results[sku] = {
            "sku": sku,
            "weight_lb": _to_float_dim(cell(row, weight_col)),
            "longest_side": _to_float_dim(cell(row, longest_col)),
            "median_side": _to_float_dim(cell(row, median_col)),
            "shortest_side": _to_float_dim(cell(row, shortest_col)),
            "units_per_case": _to_int_dim(cell(row, upc_col)),
            "cp_weight_lb": _to_float_dim(cell(row, cp_weight_col)),
            "cp_longest_side": _to_float_dim(cell(row, cp_longest_col)),
            "cp_median_side": _to_float_dim(cell(row, cp_median_col)),
            "cp_shortest_side": _to_float_dim(cell(row, cp_shortest_col)),
        }
    return results
