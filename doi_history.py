"""
doi_history.py — Daily DOI snapshots per SKU, for sparklines and trend charts.

Python version of class-fba-doi-history.php. One snapshot per client+SKU+day
is kept; if the app is opened more than once on the same day, the snapshot
for that day is skipped (rather than re-written), to keep writes cheap.
"""

from datetime import date, timedelta

import pandas as pd
import store


def snapshot_today(brand_code: str, active_inv: pd.DataFrame):
    """
    Save today's DOI snapshot for every active SKU. Safe to call on every
    dashboard load — it's a no-op if today's snapshot already exists.
    """
    today = date.today().isoformat()
    existing = store.read_tab("doi_history")
    already_done = not existing[
        (existing["brand_code"] == brand_code) & (existing["snapshot_date"] == today)
    ].empty
    if already_done or active_inv.empty:
        return

    rows = []
    for _, r in active_inv.iterrows():
        rows.append({
            "brand_code": brand_code, "sku": r["sku"], "title": r["title"],
            "snapshot_date": today,
            "doi": int(r.get("current_doi", 999)),
            "future_doi": int(r.get("future_doi", r.get("current_doi", 999))),
            "fulfillable": int(r["fulfillable"]),
            "inbound": int(r["inbound_working"] + r["inbound_shipped"] + r["inbound_receiving"]
                           + r.get("fc_transfer", 0) + r.get("fc_processing", 0)),
            "daily_avg": float(r["daily_avg"]),
            "units_7day": int(r["units_7day"]),
            "units_30day": int(r["units_30day"]),
            "replenish": bool(r["replenish"]),
        })
    store._append_rows("doi_history", rows)


def get_history_by_client(brand_code: str, days: int = 90) -> pd.DataFrame:
    df = store.read_tab("doi_history")
    df = df[df["brand_code"] == brand_code].copy()
    if df.empty:
        return df
    since = (date.today() - timedelta(days=days)).isoformat()
    df = df[df["snapshot_date"] >= since]
    for c in ("doi", "future_doi", "fulfillable", "inbound", "units_7day", "units_30day"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    df["daily_avg"] = pd.to_numeric(df["daily_avg"], errors="coerce").fillna(0.0)
    return df.sort_values(["sku", "snapshot_date"])


def get_history_by_sku(brand_code: str, sku: str, days: int = 90) -> pd.DataFrame:
    df = get_history_by_client(brand_code, days)
    return df[df["sku"] == sku]


def get_tracking_stats(brand_code: str) -> dict:
    df = store.read_tab("doi_history")
    df = df[df["brand_code"] == brand_code]
    if df.empty:
        return {"days_tracked": 0, "first_snapshot": None, "last_snapshot": None, "skus_tracked": 0}
    return {
        "days_tracked": df["snapshot_date"].nunique(),
        "first_snapshot": df["snapshot_date"].min(),
        "last_snapshot": df["snapshot_date"].max(),
        "skus_tracked": df["sku"].nunique(),
    }


def sku_stats(hist: pd.DataFrame) -> dict | None:
    """Quick stats block matching the original modal: first/last/min/max/avg/trend."""
    if hist.empty:
        return None
    dois = hist["doi"].tolist()
    return {
        "days": len(dois), "first_doi": dois[0], "last_doi": dois[-1],
        "min_doi": min(dois), "max_doi": max(dois),
        "avg_doi": round(sum(dois) / len(dois)), "trend": dois[-1] - dois[0],
    }
