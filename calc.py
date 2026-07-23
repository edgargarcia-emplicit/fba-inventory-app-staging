"""
calc.py — Shared inventory math, used by the dashboard, overview, and digest
pages so the formulas live in exactly one place (the original plugin had
this logic duplicated across the AJAX handler, database class, and email
digest — this consolidates it).
"""

import math
import re
from datetime import date, timedelta

import pandas as pd


def active_only(inv: pd.DataFrame) -> pd.DataFrame:
    return inv[inv["sku_status"].str.contains("Active", na=False)].copy()


def order_status_label(days_until: int) -> str:
    if days_until < 0:
        return "🔴 Overdue"
    if days_until <= 7:
        return "🟠 Urgent"
    if days_until <= 14:
        return "🟡 Upcoming"
    return "🟢 On track"


def doi_flag(doi, target):
    if doi >= target:
        return "🟢"
    if doi >= target * 0.6:
        return "🟡"
    return "🔴"


def compute_derived(df: pd.DataFrame, target_doi: int, lead_time: int) -> pd.DataFrame:
    """Add DOI, replenishment, and order-timing columns (standard, non-Prime-Day mode)."""
    df = df.copy()
    if df.empty:
        return df
    df["inbound_total"] = (df["inbound_working"] + df["inbound_shipped"] + df["inbound_receiving"]
                            + df.get("fc_transfer", 0) + df.get("fc_processing", 0))
    df["current_doi"] = df.apply(
        lambda r: round(r["fulfillable_plus_inbound"] / r["daily_avg"]) if r["daily_avg"] > 0 else 999, axis=1)
    df["units_needed"] = ((target_doi + lead_time) * df["daily_avg"]).round()
    df["order_units_calc"] = (df["units_needed"] - df["fulfillable_plus_inbound"]).clip(lower=0)
    df["order_cases_calc"] = df.apply(
        lambda r: math.ceil(r["order_units_calc"] / r["units_per_case"]) if r["units_per_case"] > 0 else 0, axis=1)
    df["order_units_calc"] = df["order_cases_calc"] * df["units_per_case"].clip(lower=1)
    df["days_until_order"] = df["current_doi"] - target_doi - lead_time
    df["order_by"] = df["days_until_order"].apply(
        lambda d: (date.today() + timedelta(days=int(d))).strftime("%b %d"))
    df["order_status"] = df["days_until_order"].apply(order_status_label)
    df["doi_flag"] = df["current_doi"].apply(lambda d: doi_flag(d, target_doi))
    df["aged_181_plus"] = df["inv_age_181_270"] + df["inv_age_271_365"] + df["inv_age_365_plus"]
    df["ais_qty_total"] = (df.get("qty_ais_181_210", 0) + df.get("qty_ais_211_240", 0)
                            + df.get("qty_ais_241_270", 0) + df.get("qty_ais_271_300", 0)
                            + df.get("qty_ais_301_330", 0) + df.get("qty_ais_331_365", 0)
                            + df.get("qty_ais_365_plus", 0))
    return df


def compute_prime_day(df: pd.DataFrame, pd_settings: dict, target_doi: int, lead_time: int) -> pd.DataFrame:
    """Add Prime Day projection columns on top of the standard derived columns."""
    import json
    df = compute_derived(df, target_doi, lead_time)
    if df.empty:
        return df

    multiplier = pd_settings["multiplier"]
    sup_lt = pd_settings["supplier_lead_time"]
    amz_lt = pd_settings["amazon_lead_time"]
    recovery = pd_settings["recovery_days"]
    sku_mult = {}
    try:
        sku_mult = json.loads(pd_settings.get("sku_multipliers_json") or "{}")
    except Exception:
        pass

    start, end = pd_settings.get("start_date"), pd_settings.get("end_date")
    pd_days = 2
    if start and end:
        try:
            d0 = date.fromisoformat(start)
            d1 = date.fromisoformat(end)
            pd_days = max(1, (d1 - d0).days + 1)
        except ValueError:
            pass

    cutoff = pd_settings.get("cutoff_date")
    cutoff_date = None
    if cutoff:
        try:
            cutoff_date = date.fromisoformat(cutoff)
        except ValueError:
            pass

    def row_calc(r):
        sm = float(sku_mult.get(r["sku"], multiplier))
        pd_daily = r["daily_avg"] * sm
        total_lt = sup_lt + amz_lt
        pre_demand = r["daily_avg"] * total_lt
        prime_demand = pd_daily * pd_days
        recovery_demand = pd_daily * recovery
        units_needed_pd = pre_demand + prime_demand + recovery_demand
        units_to_order = max(0, math.ceil(units_needed_pd - r["fulfillable_plus_inbound"]))
        upc = r["units_per_case"] if r["units_per_case"] > 0 else 1
        cases_to_order = math.ceil(units_to_order / upc)
        pd_doi_event = round(r["fulfillable_plus_inbound"] / pd_daily) if pd_daily > 0 else 0

        status = "—"
        is_active = "Active" in r["sku_status"]
        if is_active:
            if cutoff_date and date.today() > cutoff_date:
                status = "🔴 TOO LATE for FBA"
            elif units_to_order > 0:
                days_to_cutoff = (cutoff_date - date.today()).days if cutoff_date else 999
                status = "🟠 URGENT — order now" if days_to_cutoff <= 7 else "🟡 At risk — order soon"
            else:
                status = "🟢 On track"

        return pd.Series({
            "pd_multiplier": sm, "pd_daily_avg": round(pd_daily, 2),
            "pd_doi_event": pd_doi_event, "pd_units_needed": round(units_needed_pd),
            "pd_units_to_order": units_to_order, "pd_cases_to_order": cases_to_order,
            "pd_status": status,
        })

    extra = df.apply(row_calc, axis=1)
    return pd.concat([df, extra], axis=1)


def aged_summary(active_inv: pd.DataFrame) -> pd.DataFrame:
    """Rows with any 91+ day aged inventory or AIS-fee-triggering quantity."""
    if active_inv.empty:
        return active_inv
    df = active_inv.copy()
    df["total_aged"] = df["inv_age_181_270"] + df["inv_age_271_365"] + df["inv_age_365_plus"]
    df["ais_qty_total"] = (df.get("qty_ais_181_210", 0) + df.get("qty_ais_211_240", 0)
                            + df.get("qty_ais_241_270", 0) + df.get("qty_ais_271_300", 0)
                            + df.get("qty_ais_301_330", 0) + df.get("qty_ais_331_365", 0)
                            + df.get("qty_ais_365_plus", 0))
    out = df[(df["total_aged"] > 0) | (df["inv_age_91_180"] > 0) | (df["ais_qty_total"] > 0)]
    return out.sort_values("ais_qty_total", ascending=False)


def client_summary(inv: pd.DataFrame, target_doi: int) -> dict:
    active = active_only(inv)
    if active.empty:
        return {"active_skus": 0, "total_fulfillable": 0, "total_inbound": 0, "daily_total": 0,
                "in_stock_rate": 0, "weighted_rate": 0, "needs_reorder": 0}
    total_fulfillable = int(inv["fulfillable"].sum())
    total_inbound = int(inv["inbound_working"].sum() + inv["inbound_shipped"].sum() + inv["inbound_receiving"].sum())
    daily_total = round(active["daily_avg"].sum(), 1)
    in_stock = active[active["fulfillable"] > 0]
    in_stock_rate = round(len(in_stock) / len(active) * 100, 1) if len(active) else 0
    total_60 = active["units_60day"].sum()
    weighted = 0.0
    if total_60 > 0:
        share = active["units_60day"] / total_60
        weighted = ((active["fulfillable"] > 0).astype(int) * share).sum()
    needs_reorder = 0
    for _, r in active.iterrows():
        if r["daily_avg"] > 0 and (r["fulfillable_plus_inbound"] / r["daily_avg"]) < target_doi:
            needs_reorder += 1
    return {
        "active_skus": len(active), "total_fulfillable": total_fulfillable,
        "total_inbound": total_inbound, "daily_total": daily_total,
        "in_stock_rate": in_stock_rate, "weighted_rate": round(weighted * 100, 2),
        "needs_reorder": needs_reorder,
    }


def trending_skus(active_inv: pd.DataFrame) -> pd.DataFrame:
    """SKUs whose 7-day velocity is running hot vs their 30/90-day baseline."""
    if active_inv.empty:
        return active_inv
    rows = []
    for _, r in active_inv.iterrows():
        if r["units_7day"] <= 0 and r["units_30day"] <= 0:
            continue
        d7 = r["units_7day"] / 7
        d30 = r["units_30day"] / 30 if r["units_30day"] > 0 else 0
        d90 = r["units_90day"] / 90 if r["units_90day"] > 0 else 0
        vs30 = (d7 - d30) / d30 if d30 > 0 else 0
        vs90 = (d7 - d90) / d90 if d90 > 0 else 0
        if vs30 >= 0.40 and vs90 >= 0.25:
            flag = "🔥 Hot"
        elif vs30 >= 0.20 or vs90 >= 0.20:
            flag = "📈 Rising"
        else:
            continue
        rows.append({**r.to_dict(), "flag": flag, "d7": round(d7, 1), "d30": round(d30, 1),
                     "vs30_pct": round(vs30 * 100), "vs90_pct": round(vs90 * 100)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- carriers
CARRIER_COLORS = {"UPS": "#7b3f00", "FedEx": "#4d148c", "USPS": "#004b87",
                   "AMZL": "#e8404a", "DHL": "#FFCC00", "Other": "#6b7280"}


def detect_carrier(num: str) -> str:
    num = re.sub(r"\s", "", num or "").upper()
    if re.fullmatch(r"1Z[A-Z0-9]{16}", num):
        return "UPS"
    if re.fullmatch(r"\d{12}|\d{15}|\d{20}", num):
        return "FedEx"
    if re.fullmatch(r"(94|93|92|91|90)\d{18,20}", num) or re.fullmatch(r"\d{22}", num):
        return "USPS"
    if re.fullmatch(r"TBA\d+", num):
        return "AMZL"
    if re.fullmatch(r"\d{10,11}", num):
        return "DHL"
    return "Other" if num else ""


def carrier_track_url(carrier: str, num: str) -> str:
    urls = {
        "UPS": f"https://www.ups.com/track?tracknum={num}",
        "FedEx": f"https://www.fedex.com/fedextrack/?trknbr={num}",
        "USPS": f"https://tools.usps.com/go/TrackConfirmAction?tLabels={num}",
        "AMZL": f"https://track.amazon.com/tracking/{num}",
        "DHL": f"https://www.dhl.com/en/express/tracking.html?AWB={num}",
    }
    return urls.get(carrier, "")
