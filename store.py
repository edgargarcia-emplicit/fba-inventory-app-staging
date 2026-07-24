"""
store.py — Persistent app data, stored in one Google Sheet ("FBA App Data").

Streamlit Community Cloud's free tier has no permanent disk, so all of the
app's own bookkeeping (clients, settings, notes, shipments, DOI history,
health reports, case packs, dimensions, Prime Day config, client profiles)
lives in tabs inside that one spreadsheet. The service account needs
EDITOR access to this sheet only — client inventory sheets stay Viewer-only.
"""

from datetime import datetime, date

import pandas as pd
import streamlit as st
from googleapiclient.discovery import build

from sheets import _credentials

SCOPES_RW = ["https://www.googleapis.com/auth/spreadsheets"]

TABS = {
    "clients":            ["client_name", "brand_code", "sheet_id", "tab_name", "marketplace"],
    "settings":           ["brand_code", "target_doi", "lead_time"],
    "notes":              ["brand_code", "sku", "note", "updated_by", "updated_at"],
    "shipments":          ["brand_code", "sku", "title", "units", "fba_shipment_id",
                            "tracking_number", "carrier", "date_ordered", "date_expected",
                            "status", "notes"],
    "doi_history":        ["brand_code", "sku", "title", "snapshot_date", "doi", "future_doi",
                            "fulfillable", "inbound", "daily_avg", "units_7day", "units_30day",
                            "replenish"],
    "health_stranded":    ["brand_code", "synced_at", "file_name", "record_type", "asin", "sku",
                            "fnsku", "product", "condition", "issue", "quantity", "dollar_value",
                            "recommended_action", "date_stranded", "is_new"],
    "health_unfulfillable": ["brand_code", "synced_at", "file_name", "record_type", "asin", "sku",
                            "fnsku", "product", "issue", "quantity", "dollar_value",
                            "recommended_action", "recommended_removal_quantity", "is_new"],
    "health_folders":     ["brand_code", "drive_folder_id"],
    "prime_day":          ["brand_code", "active", "multiplier", "start_date", "end_date",
                            "cutoff_date", "supplier_lead_time", "amazon_lead_time",
                            "recovery_days", "sku_multipliers_json"],
    "case_packs":         ["id", "brand_code", "sku", "pack_name", "units",
                            "length_in", "width_in", "height_in", "weight_lb"],
    "sku_dimensions":     ["brand_code", "sku", "weight_lb", "longest_side", "median_side",
                            "shortest_side", "units_per_case", "size_tier"],
    "client_profiles":    ["brand_code", "account_location", "marketplaces", "categories",
                            "client_notes", "sku_sheet_link", "replenishment_link", "ops_mistakes"],
    "last_checked":       ["brand_code", "checked_at", "checked_by"],
    "ignored_alerts":     ["key"],
    "digest_recipients":  ["email"],
    "grid_prefs":         ["brand_code", "column_order_json", "width_preset"],
}


def _svc():
    return build("sheets", "v4", credentials=_credentials(SCOPES_RW), cache_discovery=False)


def _app_sheet_id() -> str:
    return st.secrets["app_data_sheet_id"]


def ensure_tabs():
    """Create any missing tabs with header rows. Safe to call every run."""
    svc = _svc()
    meta = svc.spreadsheets().get(spreadsheetId=_app_sheet_id()).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}
    requests = [{"addSheet": {"properties": {"title": tab}}} for tab in TABS if tab not in existing]
    if requests:
        svc.spreadsheets().batchUpdate(spreadsheetId=_app_sheet_id(), body={"requests": requests}).execute()
        for tab in TABS:
            if tab not in existing:
                svc.spreadsheets().values().update(
                    spreadsheetId=_app_sheet_id(), range=f"{tab}!A1",
                    valueInputOption="RAW", body={"values": [TABS[tab]]},
                ).execute()


@st.cache_data(ttl=120, show_spinner=False)
def read_tab(tab: str) -> pd.DataFrame:
    """Read one tab into a DataFrame (2-minute cache; writes clear it)."""
    svc = _svc()
    result = svc.spreadsheets().values().get(
        spreadsheetId=_app_sheet_id(), range=f"{tab}!A1:Z20000"
    ).execute()
    values = result.get("values", [])
    cols = TABS[tab]
    if len(values) <= 1:
        return pd.DataFrame(columns=cols)
    body = [row + [""] * (len(cols) - len(row)) for row in values[1:]]
    return pd.DataFrame(body, columns=cols)


def _write_tab(tab: str, df: pd.DataFrame):
    """Overwrite one tab with header + rows, then clear the read cache."""
    svc = _svc()
    cols = TABS[tab]
    values = [cols] + df[cols].astype(str).values.tolist()
    svc.spreadsheets().values().clear(spreadsheetId=_app_sheet_id(), range=f"{tab}!A1:Z20000").execute()
    svc.spreadsheets().values().update(
        spreadsheetId=_app_sheet_id(), range=f"{tab}!A1",
        valueInputOption="RAW", body={"values": values},
    ).execute()
    read_tab.clear()


def _append_rows(tab: str, rows: list[dict]):
    """Append rows without rewriting the whole tab (cheaper for growing logs)."""
    if not rows:
        return
    svc = _svc()
    cols = TABS[tab]
    values = [[str(r.get(c, "")) for c in cols] for r in rows]
    svc.spreadsheets().values().append(
        spreadsheetId=_app_sheet_id(), range=f"{tab}!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": values},
    ).execute()
    read_tab.clear()


# ================================================================== clients
def get_clients() -> pd.DataFrame:
    return read_tab("clients")


def save_client(client_name, brand_code, sheet_id, tab_name, marketplace):
    df = read_tab("clients")
    df = df[df["brand_code"] != brand_code]
    row = pd.DataFrame([{"client_name": client_name, "brand_code": brand_code,
                          "sheet_id": sheet_id, "tab_name": tab_name, "marketplace": marketplace}])
    _write_tab("clients", pd.concat([df, row], ignore_index=True))


def delete_client(brand_code):
    df = read_tab("clients")
    _write_tab("clients", df[df["brand_code"] != brand_code])


# ================================================================= settings
def get_settings(brand_code) -> dict:
    df = read_tab("settings")
    row = df[df["brand_code"] == brand_code]
    if row.empty:
        return {"target_doi": 45, "lead_time": 14}
    r = row.iloc[0]
    return {"target_doi": int(float(r["target_doi"] or 45)), "lead_time": int(float(r["lead_time"] or 14))}


def save_settings(brand_code, target_doi, lead_time):
    df = read_tab("settings")
    df = df[df["brand_code"] != brand_code]
    row = pd.DataFrame([{"brand_code": brand_code, "target_doi": int(target_doi), "lead_time": int(lead_time)}])
    _write_tab("settings", pd.concat([df, row], ignore_index=True))


# ==================================================================== notes
def get_notes(brand_code) -> pd.DataFrame:
    df = read_tab("notes")
    return df[df["brand_code"] == brand_code]


def save_note(brand_code, sku, note, updated_by):
    df = read_tab("notes")
    df = df[~((df["brand_code"] == brand_code) & (df["sku"] == sku))]
    if note.strip():
        row = pd.DataFrame([{"brand_code": brand_code, "sku": sku, "note": note.strip(),
                              "updated_by": updated_by,
                              "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}])
        df = pd.concat([df, row], ignore_index=True)
    _write_tab("notes", df)


# ================================================================ shipments
def get_shipments(brand_code) -> pd.DataFrame:
    df = read_tab("shipments")
    return df[df["brand_code"] == brand_code]


def add_shipment(brand_code, sku, title, units, fba_shipment_id, tracking_number,
                  carrier, date_ordered, date_expected, notes):
    df = read_tab("shipments")
    row = pd.DataFrame([{
        "brand_code": brand_code, "sku": sku, "title": title, "units": int(units),
        "fba_shipment_id": fba_shipment_id, "tracking_number": tracking_number,
        "carrier": carrier, "date_ordered": str(date_ordered), "date_expected": str(date_expected),
        "status": "pending", "notes": notes,
    }])
    _write_tab("shipments", pd.concat([df, row], ignore_index=True))


def update_shipment_status(brand_code, sku, date_ordered, status):
    df = read_tab("shipments")
    mask = (df["brand_code"] == brand_code) & (df["sku"] == sku) & (df["date_ordered"] == str(date_ordered))
    df.loc[mask, "status"] = status
    _write_tab("shipments", df)


def update_shipment_tracking(brand_code, sku, date_ordered, fba_shipment_id, tracking_number, carrier):
    df = read_tab("shipments")
    mask = (df["brand_code"] == brand_code) & (df["sku"] == sku) & (df["date_ordered"] == str(date_ordered))
    df.loc[mask, "fba_shipment_id"] = fba_shipment_id
    df.loc[mask, "tracking_number"] = tracking_number
    df.loc[mask, "carrier"] = carrier
    if tracking_number:
        df.loc[mask & (df["status"] == "pending"), "status"] = "shipped"
    _write_tab("shipments", df)


# ================================================================= prime day
def get_prime_day(brand_code) -> dict:
    df = read_tab("prime_day")
    row = df[df["brand_code"] == brand_code]
    defaults = {"active": False, "multiplier": 2.5, "start_date": "", "end_date": "",
                "cutoff_date": "", "supplier_lead_time": 21, "amazon_lead_time": 28,
                "recovery_days": 7, "sku_multipliers_json": "{}"}
    if row.empty:
        return defaults
    r = row.iloc[0]
    return {
        "active": str(r["active"]).lower() in ("true", "1"),
        "multiplier": float(r["multiplier"] or 2.5),
        "start_date": r["start_date"], "end_date": r["end_date"], "cutoff_date": r["cutoff_date"],
        "supplier_lead_time": int(float(r["supplier_lead_time"] or 21)),
        "amazon_lead_time": int(float(r["amazon_lead_time"] or 28)),
        "recovery_days": int(float(r["recovery_days"] or 7)),
        "sku_multipliers_json": r["sku_multipliers_json"] or "{}",
    }


def save_prime_day(brand_code, **kwargs):
    df = read_tab("prime_day")
    df = df[df["brand_code"] != brand_code]
    row = {"brand_code": brand_code, **kwargs}
    _write_tab("prime_day", pd.concat([df, pd.DataFrame([row])], ignore_index=True))


# ================================================================ case packs
def get_case_packs(brand_code, sku) -> pd.DataFrame:
    df = read_tab("case_packs")
    return df[(df["brand_code"] == brand_code) & (df["sku"] == sku)]


def save_case_pack(brand_code, sku, pack_name, units, length_in, width_in, height_in, weight_lb, pack_id=None):
    df = read_tab("case_packs")
    if pack_id:
        df = df[df["id"] != str(pack_id)]
    else:
        pack_id = str(int(datetime.now().timestamp() * 1000))
    row = pd.DataFrame([{"id": pack_id, "brand_code": brand_code, "sku": sku, "pack_name": pack_name,
                          "units": int(units), "length_in": length_in, "width_in": width_in,
                          "height_in": height_in, "weight_lb": weight_lb}])
    _write_tab("case_packs", pd.concat([df, row], ignore_index=True))


def get_case_pack_id_by_name(brand_code, sku, pack_name):
    df = read_tab("case_packs")
    match = df[(df["brand_code"] == brand_code) & (df["sku"] == sku) & (df["pack_name"] == pack_name)]
    return match.iloc[0]["id"] if not match.empty else None


def delete_case_pack(pack_id):
    df = read_tab("case_packs")
    _write_tab("case_packs", df[df["id"] != str(pack_id)])


# ============================================================ sku dimensions
def get_sku_dimensions(brand_code, sku=None):
    df = read_tab("sku_dimensions")
    df = df[df["brand_code"] == brand_code]
    if sku:
        row = df[df["sku"] == sku]
        return row.iloc[0].to_dict() if not row.empty else None
    return df


def save_sku_dimension(brand_code, sku, weight_lb, longest_side, median_side, shortest_side,
                        units_per_case, size_tier):
    df = read_tab("sku_dimensions")
    df = df[~((df["brand_code"] == brand_code) & (df["sku"] == sku))]
    row = pd.DataFrame([{"brand_code": brand_code, "sku": sku, "weight_lb": weight_lb,
                          "longest_side": longest_side, "median_side": median_side,
                          "shortest_side": shortest_side, "units_per_case": int(units_per_case),
                          "size_tier": size_tier}])
    _write_tab("sku_dimensions", pd.concat([df, row], ignore_index=True))


# =========================================================== client profile
def get_client_profile(brand_code) -> dict:
    df = read_tab("client_profiles")
    row = df[df["brand_code"] == brand_code]
    if row.empty:
        return {c: "" for c in TABS["client_profiles"] if c != "brand_code"}
    return row.iloc[0].to_dict()


def save_client_profile(brand_code, **kwargs):
    df = read_tab("client_profiles")
    df = df[df["brand_code"] != brand_code]
    row = {"brand_code": brand_code, **kwargs}
    _write_tab("client_profiles", pd.concat([df, pd.DataFrame([row])], ignore_index=True))


# ============================================================= health folder
def get_health_folder(brand_code) -> str:
    df = read_tab("health_folders")
    row = df[df["brand_code"] == brand_code]
    return row.iloc[0]["drive_folder_id"] if not row.empty else ""


def save_health_folder(brand_code, folder_id):
    df = read_tab("health_folders")
    df = df[df["brand_code"] != brand_code]
    row = pd.DataFrame([{"brand_code": brand_code, "drive_folder_id": folder_id}])
    _write_tab("health_folders", pd.concat([df, row], ignore_index=True))


# ============================================================= last checked
def get_last_checked(brand_code):
    df = read_tab("last_checked")
    row = df[df["brand_code"] == brand_code]
    return row.iloc[0].to_dict() if not row.empty else None


def mark_checked(brand_code, checked_by):
    df = read_tab("last_checked")
    df = df[df["brand_code"] != brand_code]
    row = pd.DataFrame([{"brand_code": brand_code,
                          "checked_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
                          "checked_by": checked_by}])
    _write_tab("last_checked", pd.concat([df, row], ignore_index=True))


# ============================================================ ignored alerts
def get_ignored_alerts() -> set:
    df = read_tab("ignored_alerts")
    return set(df["key"].tolist())


def ignore_alert(key):
    df = read_tab("ignored_alerts")
    if key not in df["key"].values:
        _write_tab("ignored_alerts", pd.concat([df, pd.DataFrame([{"key": key}])], ignore_index=True))


def unignore_alert(key):
    df = read_tab("ignored_alerts")
    _write_tab("ignored_alerts", df[df["key"] != key])


# ========================================================= digest recipients
def get_digest_recipients() -> list:
    df = read_tab("digest_recipients")
    return df["email"].tolist()


def save_digest_recipients(emails: list):
    df = pd.DataFrame([{"email": e.strip()} for e in emails if e.strip()])
    _write_tab("digest_recipients", df)


# ============================================================== grid layout
def get_grid_prefs(brand_code) -> dict:
    df = read_tab("grid_prefs")
    row = df[df["brand_code"] == brand_code]
    if row.empty:
        return {"column_order": None, "width_preset": "Comfortable"}
    r = row.iloc[0]
    import json
    try:
        order = json.loads(r["column_order_json"]) if r["column_order_json"] else None
    except (ValueError, TypeError):
        order = None
    return {"column_order": order, "width_preset": r["width_preset"] or "Comfortable"}


def save_grid_prefs(brand_code, column_order: list, width_preset: str):
    import json
    df = read_tab("grid_prefs")
    df = df[df["brand_code"] != brand_code]
    row = pd.DataFrame([{"brand_code": brand_code, "column_order_json": json.dumps(column_order),
                          "width_preset": width_preset}])
    _write_tab("grid_prefs", pd.concat([df, row], ignore_index=True))
