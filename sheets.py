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
