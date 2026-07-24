"""
FBA Inventory Sync — Streamlit edition
Full Python conversion of the WordPress plugin.
"""

import io
from datetime import date

import pandas as pd
import streamlit as st

import calc
import digest
import doi_history
import health
import sheets
import store

import amazon_scraper
import diffing
import docx_parser
import xlsx_export_parser

st.set_page_config(page_title="FBA Inventory Sync", page_icon="📦", layout="wide")


# =============================================================== login gate
def check_password() -> bool:
    if st.session_state.get("authed"):
        return True
    st.title("📦 FBA Inventory Sync")
    pw = st.text_input("Team password", type="password")
    if pw:
        if pw == st.secrets.get("app_password", ""):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


if not check_password():
    st.stop()

store.ensure_tabs()


# =============================================================== helpers
def csv_safe(value):
    """Prevent spreadsheet formula injection in exports (security review Finding 1)."""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def safe_view(table: pd.DataFrame, display_cols: dict) -> pd.DataFrame:
    """
    Build the display table without ever KeyError-ing if an expected column
    is missing. This shouldn't happen in normal operation, but calc.py and
    streamlit_app.py must be updated together — if only one gets deployed,
    the columns each expects can fall out of sync. Rather than crash the
    whole page, show what's available and flag what's missing.
    """
    missing = [c for c in display_cols if c not in table.columns]
    if missing:
        st.warning(
            f"Some expected data columns are missing and have been skipped: {missing}. "
            "This usually means calc.py and streamlit_app.py are out of sync in your "
            "deployment — double check both files were updated together in your GitHub repo."
        )
    present = {k: v for k, v in display_cols.items() if k in table.columns}
    return table[list(present)].rename(columns=present)


def goto(page_name, brand_code=None, prefill_sku=None):
    """
    Switch page (and optionally client / a SKU to prefill) from a button click.

    Must be called via a button's on_click= callback, not directly inside
    the page body — Streamlit forbids writing to session_state for a key
    that's already bound to a widget rendered earlier in the same run
    (the sidebar's Page radio, key="nav_page", is drawn before any page
    body runs). Callbacks execute in a separate phase before the rerun,
    where this is explicitly allowed.
    """
    st.session_state["nav_page"] = page_name
    if brand_code is not None:
        clients = store.get_clients()
        match = clients[clients["brand_code"] == brand_code]
        if not match.empty:
            st.session_state["client_selector"] = match.iloc[0]["client_name"]
    if prefill_sku is not None:
        st.session_state["prefill_sku"] = prefill_sku


# ================================================== FF Pro Sync / QA helpers
_RESULT_COLORS = {
    "pass": ("#f0fff4", "#276749", "#9ae6b4", "✅ Pass"),
    "fail": ("#fff5f5", "#c53030", "#fc8181", "❌ Fail"),
    "missing": ("#fffbeb", "#92400e", "#fcd34d", "⚠ Missing"),
    "error": ("#fff5f5", "#9b2c2c", "#feb2b2", "⚠ Error"),
}


def _result_badge_html(result: str) -> str:
    bg, color, border, label = _RESULT_COLORS.get(result, _RESULT_COLORS["missing"])
    return (f'<span style="background:{bg};color:{color};border:1px solid {border};'
            f'padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600">{label}</span>')


def _render_comparison_table(results: dict, filt: str, hide_parents: bool, search: str,
                              left_label: str, right_label: str) -> str:
    """Builds the same word-level-diff-highlighted comparison table as the WordPress
    plugin's crossref/QA results — st.dataframe can't render inline HTML marks, so this
    is a real HTML table via st.components.v1.html instead."""
    rows_html = []
    search_lc = (search or "").lower()

    for asin, item in results.items():
        skus = item.get("skus") or ([item["sku"]] if item.get("sku") else [])
        sku_list = ", ".join(skus) if skus else (item.get("sku") or "—")

        if search_lc:
            searchable = f"{asin} {sku_list} {item.get('title', '')}".lower()
            if search_lc not in searchable:
                continue
        if hide_parents and diffing.skus_are_all_parents(skus):
            continue

        fields = item.get("fields", [])
        if filt != "all":
            fields = [f for f in fields if f["result"] == filt]

        is_missing_asin = not item.get("in_xlsx", True)
        has_error = bool(item.get("error"))
        if not fields and not is_missing_asin and not has_error:
            continue

        title_safe = str(item.get("title", "") or "")[:80]
        rows_html.append(
            f'<tr style="background:#fafafa"><td colspan="6" style="padding:10px 12px;font-weight:700">'
            f'<a href="https://www.amazon.com/dp/{asin}" target="_blank" style="color:#0d6efd;text-decoration:none">{asin} ↗</a>'
            f'<span style="color:#666;font-weight:400;font-size:12px;margin-left:10px">{title_safe}</span>'
            + (f'<span style="margin-left:10px">{_result_badge_html("missing")}</span>' if is_missing_asin else "")
            + (f'<span style="margin-left:10px">{_result_badge_html("error")} {item["error"]}</span>' if has_error else "")
            + '</td></tr>'
        )

        for f in fields:
            source = f.get("source", f.get("expected", ""))
            target = f.get("uploaded", f.get("actual", ""))
            if f["result"] == "fail" and target:
                diff = diffing.build_diff_html(source or "", target)
                left_html = diff["source_html"]
                right_html = diff["target_html"]
                sim_bar = (f'<div style="margin-top:4px"><span style="display:inline-block;width:60px;height:4px;'
                           f'background:#eee;border-radius:2px;overflow:hidden;vertical-align:middle">'
                           f'<span style="display:block;height:100%;width:{f["similarity"]}%;background:#e8404a"></span>'
                           f'</span> <span style="font-size:11px;color:#888">{f["similarity"]}% match</span></div>')
                right_html += sim_bar
            else:
                import html as _html
                left_html = _html.escape(source or "—")
                right_html = (f'<span style="color:#bbb;font-style:italic">Not found</span>' if not target
                              else _html.escape(target))

            row_bg = {"pass": "#fff", "fail": "#fff8f8", "missing": "#fffbeb", "error": "#fff5f5"}.get(f["result"], "#fff")
            rows_html.append(
                f'<tr style="background:{row_bg}">'
                f'<td style="padding:8px 12px;color:#888;font-size:11px;vertical-align:top">{sku_list}</td>'
                f'<td style="padding:8px 12px;color:#555;font-weight:500;vertical-align:top;font-size:12px">{f["label"]}</td>'
                f'<td style="padding:8px 12px;vertical-align:top;font-size:13px;max-width:280px">{left_html}</td>'
                f'<td style="padding:8px 12px;vertical-align:top;font-size:13px;max-width:280px">{right_html}</td>'
                f'<td style="padding:8px 12px;vertical-align:top">{_result_badge_html(f["result"])}</td>'
                f'</tr>'
            )

    if not rows_html:
        body = '<tr><td colspan="6" style="padding:24px;text-align:center;color:#888">No results match the current filter.</td></tr>'
    else:
        body = "".join(rows_html)

    return f"""
    <div style="font-family:-apple-system,sans-serif;font-size:13px">
    <table style="border-collapse:collapse;width:100%">
      <thead><tr style="background:#f9fafb;border-bottom:2px solid #e5e7eb">
        <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">SKU(s)</th>
        <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Field</th>
        <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">{left_label}</th>
        <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">{right_label}</th>
        <th style="padding:8px 12px;text-align:left;font-size:11px;color:#666">Result</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>
    </div>
    """


def _build_export_df(results: dict) -> pd.DataFrame:
    rows = []
    for asin, item in results.items():
        skus = item.get("skus") or ([item["sku"]] if item.get("sku") else [])
        sku_list = "; ".join(skus) if skus else ""
        if item.get("error"):
            rows.append({"ASIN": asin, "SKU(s)": sku_list, "Title": item.get("title", ""),
                         "Field": "error", "Expected": "", "Actual": item["error"],
                         "Result": "error", "Similarity %": 0})
            continue
        if not item.get("in_xlsx", True):
            rows.append({"ASIN": asin, "SKU(s)": sku_list, "Title": item.get("title", ""),
                         "Field": "ASIN", "Expected": asin, "Actual": "NOT IN XLSX",
                         "Result": "missing", "Similarity %": 0})
        for f in item.get("fields", []):
            rows.append({"ASIN": asin, "SKU(s)": sku_list, "Title": item.get("title", ""),
                         "Field": f["label"], "Expected": f.get("source", f.get("expected", "")),
                         "Actual": f.get("uploaded", f.get("actual", "")),
                         "Result": f["result"], "Similarity %": f["similarity"]})
    return pd.DataFrame(rows)


def _build_diff_export_df(results: dict, hide_parents: bool):
    """Only failing ASINs; if any bullet fails, include all bullets (source-of-truth values)."""
    rows = []
    for asin, item in results.items():
        skus = item.get("skus") or ([item["sku"]] if item.get("sku") else [])
        if hide_parents and diffing.skus_are_all_parents(skus):
            continue
        fields = item.get("fields", [])
        has_failure = any(f["result"] != "pass" for f in fields)
        if not has_failure and item.get("in_xlsx", True):
            continue
        by_field = {f["field"]: f for f in fields}
        title_failed = by_field.get("title", {}).get("result") != "pass" if "title" in by_field else False
        any_bullet_fail = any(f["result"] != "pass" for f in fields if f["field"].startswith("bullet_"))
        row = {"ASIN": asin, "Title": by_field["title"]["source"] if title_failed and "title" in by_field else ""}
        for key in ("bullet_1", "bullet_2", "bullet_3", "bullet_4", "bullet_5"):
            f = by_field.get(key)
            row[key.replace("_", " ").title()] = f["source"] if (f and any_bullet_fail) else ""
        for key, label in (("description", "Description"), ("keywords", "Keywords")):
            f = by_field.get(key)
            row[label] = f["source"] if (f and f["result"] != "pass") else ""
        rows.append(row)
    return pd.DataFrame(rows) if rows else None


def render_dashboard_module():
    """
    Renders the full inventory dashboard: KPI cards, the editable grid
    (Mock Units / Future DOI / Notes), filters, CSV export, and the SKU
    deep-dive section.
    """
    st.header(f"{active['client_name']} — Inventory Dashboard")

    snapshot = inv["snapshot_date"].iloc[0] if len(inv) else ""

    active_inv = calc.active_only(inv)

    # ---- Target DOI / lead time controls ----
    c1, c2, c3 = st.columns([1, 1, 2])
    target_doi = c1.number_input("Target DOI (days)", 1, 365, settings["target_doi"])
    lead_time = c2.number_input("Lead time (days)", 1, 180, settings["lead_time"])
    if (target_doi, lead_time) != (settings["target_doi"], settings["lead_time"]):
        if c3.button("💾 Save settings"):
            store.save_settings(brand, target_doi, lead_time)
            st.toast("Settings saved.")
            st.rerun()

    derived = calc.compute_derived(active_inv, target_doi, lead_time)
    doi_history.snapshot_today(brand, derived)
    health_counts = health.get_notification_counts(brand)

    # ---- Prime Day settings ----
    pd_settings = store.get_prime_day(brand)
    with st.expander(f"🎯 Prime Day mode {'(ACTIVE)' if pd_settings['active'] else ''}"):
        with st.form("pd_form"):
            pd_active = st.checkbox("Active", value=pd_settings["active"])
            c1, c2 = st.columns(2)
            pd_mult = c1.number_input("Demand multiplier", min_value=1.0, value=pd_settings["multiplier"], step=0.1)
            pd_recovery = c2.number_input("Post-event recovery days", min_value=0, value=pd_settings["recovery_days"])
            c3, c4, c5 = st.columns(3)
            pd_start = c3.text_input("Event start (YYYY-MM-DD)", value=pd_settings["start_date"])
            pd_end = c4.text_input("Event end (YYYY-MM-DD)", value=pd_settings["end_date"])
            pd_cutoff = c5.text_input("Shipment cutoff (YYYY-MM-DD)", value=pd_settings["cutoff_date"])
            c6, c7 = st.columns(2)
            pd_sup_lt = c6.number_input("Supplier lead time (days)", min_value=0, value=pd_settings["supplier_lead_time"])
            pd_amz_lt = c7.number_input("Amazon lead time (days)", min_value=0, value=pd_settings["amazon_lead_time"])
            if st.form_submit_button("Save Prime Day settings"):
                store.save_prime_day(brand, active=pd_active, multiplier=pd_mult, start_date=pd_start,
                                      end_date=pd_end, cutoff_date=pd_cutoff, supplier_lead_time=pd_sup_lt,
                                      amazon_lead_time=pd_amz_lt, recovery_days=pd_recovery,
                                      sku_multipliers_json=pd_settings["sku_multipliers_json"])
                st.success("Saved.")
                st.rerun()

    mode = "prime_day" if pd_settings["active"] else "standard"
    if pd_settings["active"]:
        mode = st.radio("Export / view mode", ["standard", "prime_day"], horizontal=True,
                         format_func=lambda m: "Standard" if m == "standard" else "🎯 Prime Day projections")

    if mode == "prime_day":
        derived = calc.compute_prime_day(active_inv, pd_settings, target_doi, lead_time)

    # ---- KPI cards ----
    in_stock = (derived["fulfillable"] > 0).sum() if not derived.empty else 0
    total_active = len(derived)
    aged_n = int((derived["ais_qty_total"] > 0).sum()) if not derived.empty and "ais_qty_total" in derived else 0
    trending_n = int((derived["trend"] != "").sum()) if not derived.empty and "trend" in derived else 0
    stranded_n = sum(1 for v in health_counts.values() if "stranded" in v)
    unful_n = sum(1 for v in health_counts.values() if "unfulfillable" in v)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Active SKUs", total_active)
    k2.metric("In-stock rate", f"{(in_stock / total_active * 100):.1f}%" if total_active else "—")
    if mode == "prime_day":
        k3.metric("Need PD order", int((derived["pd_units_to_order"] > 0).sum()) if not derived.empty else 0)
        k4.metric("Too late for FBA", int(derived["pd_status"].str.contains("TOO LATE").sum()) if not derived.empty else 0)
    else:
        k3.metric("Below target DOI", int((derived["current_doi"] < target_doi).sum()) if not derived.empty else 0)
        k4.metric("Need reorder now", int((derived["order_units_calc"] > 0).sum()) if not derived.empty else 0)
    k5.metric("Aged SKUs", aged_n)
    k6.metric("Trending SKUs", trending_n)
    if stranded_n or unful_n:
        st.caption(f"🟥 {stranded_n} stranded · 🟧 {unful_n} unfulfillable — flagged in the Alerts column below, "
                    f"full detail on the Health Report page.")
    if snapshot:
        st.caption(f"Sheet snapshot date: {snapshot}")

    st.divider()

    # ---- Filters ----
    f1, f2 = st.columns([3, 1])
    search = f1.text_input("🔍 Search title / ASIN / SKU", "")
    show_inactive = f2.toggle("Show inactive SKUs")

    table = calc.compute_derived(inv, target_doi, lead_time) if show_inactive else derived
    if show_inactive and mode == "prime_day":
        table = calc.compute_prime_day(inv, pd_settings, target_doi, lead_time)
    if search:
        q = search.lower()
        mask = (table["title"].str.lower().str.contains(q, na=False)
                | table["asin"].str.lower().str.contains(q, na=False)
                | table["sku"].str.lower().str.contains(q, na=False))
        table = table[mask]
    table = table.reset_index(drop=True)

    if table.empty:
        st.info("No active SKUs found for this client. If that's unexpected, check "
                "'Show inactive SKUs' above, or confirm the sheet's SKU Status column.")
        st.stop()

    # ---- Health badges + note preview (everything inline, no separate panels) ----
    table["alerts"] = table["sku"].map(lambda s: (
        ("🟥" if health_counts.get(s, {}).get("stranded") else "")
        + ("🟧" if health_counts.get(s, {}).get("unfulfillable") else "")
    ))
    notes_df = store.get_notes(brand)
    table["note_preview"] = table["sku"].map(
        lambda s: (notes_df.loc[notes_df["sku"] == s, "note"].iloc[0]
                   if (notes_df["sku"] == s).any() else ""))
    table["synced"] = snapshot

    # ---- Main table: one wide, dense grid, with Mock Units / Future DOI / Note editable inline ----
    if mode == "prime_day":
        display_cols = {
            "alerts": "Alerts", "trend": "Trend", "trend_vs30": "vs 30d %",
            "trend_vs90": "vs 90d %", "title": "Title", "sku": "SKU", "asin": "ASIN",
            "fulfillable": "Fulfillable",
            "inbound_working": "Inbound: Working", "inbound_shipped": "Inbound: Shipped",
            "inbound_receiving": "Inbound: Receiving", "fc_transfer": "Inbound: FC Transfer",
            "fc_processing": "Inbound: FC Processing", "fulfillable_plus_inbound": "Fulf.+Inbound",
            "daily_avg": "Daily Avg", "pd_multiplier": "PD Mult.", "pd_daily_avg": "PD Daily Avg",
            "pd_doi_event": "PD DOI (event)", "pd_units_to_order": "PD Order Units",
            "pd_cases_to_order": "PD Order Cases", "pd_status": "PD Status",
            "aged_alert": "Aged", "synced": "Synced",
        }
        daily_col = "pd_daily_avg"
    else:
        display_cols = {
            "alerts": "Alerts", "doi_flag": "Flag", "current_doi": "DOI",
            "trend": "Trend", "trend_vs30": "vs 30d %", "trend_vs90": "vs 90d %",
            "title": "Title", "sku": "SKU", "asin": "ASIN",
            "fulfillable": "Fulfillable",
            "inbound_working": "Inbound: Working", "inbound_shipped": "Inbound: Shipped",
            "inbound_receiving": "Inbound: Receiving", "fc_transfer": "Inbound: FC Transfer",
            "fc_processing": "Inbound: FC Processing", "fulfillable_plus_inbound": "Fulf.+Inbound",
            "units_7day": "Sales: 7d Units", "units_30day": "Sales: 30d Units",
            "units_60day": "Sales: 60d Units", "units_90day": "Sales: 90d Units",
            "doi_7d": "7d DOI", "daily_avg": "Daily Avg",
            "order_by": "Order By", "order_status": "Status", "action": "Action",
            "order_units_calc": "Order Units", "order_cases_calc": "Order Cases",
            "aged_alert": "Aged", "pct_sales_mix": "% Mix", "synced": "Synced",
        }
        daily_col = "daily_avg"

    edit_df = safe_view(table, display_cols)
    edit_df.insert(0, "🔎", [False] * len(table))
    st.caption("Streamlit's table has no real merged/spanning header row (checked directly in its "
               "source — no such feature exists), so the bold labels above are a best-effort "
               "approximation built from st.columns(), not a true grouped header cell. It should stay "
               "reasonably aligned, including if you reorder columns below, but isn't pixel-perfect.")


    editor_key = f"main_editor_{brand}_{mode}_{show_inactive}_{hash(search)}"

    # Mock Units and Note are tracked in our OWN persistent session_state dicts, keyed by SKU,
    # rather than relying on data_editor's own edited_rows diff. That diff is relative to
    # whatever base dataframe we hand the widget each render — if we bake the last edit back in
    # as the new baseline (needed so Future DOI can update), the diff looks like "no change"
    # again on the very next rerun, silently reverting the value. Keeping our own store, synced
    # via on_change the instant an edit happens, avoids that entirely.
    mock_store = st.session_state.setdefault("mock_units_by_sku", {})
    pending_notes = st.session_state.setdefault("pending_notes_by_sku", {})

    # Sync any in-progress edit out of the widget's own transient diff into our stable stores,
    # every run. Not using on_change for this: the sync has to happen before we rebuild the base
    # dataframe below anyway, so doing it inline here is simpler and (per direct testing) more
    # reliably triggered than a callback.
    _edited_now = st.session_state.get(editor_key, {}).get("edited_rows", {})
    _view_trigger_sku = None
    for _pos_str, _changes in _edited_now.items():
        _pos = int(_pos_str)
        if _pos >= len(table):
            continue
        _sku_val = table.iloc[_pos]["sku"]
        if "Mock Units" in _changes:
            mock_store[_sku_val] = _changes["Mock Units"]
        if "Note" in _changes:
            pending_notes[_sku_val] = _changes["Note"]
        if _changes.get("🔎") is True:
            _view_trigger_sku = _sku_val


    mock_units_col = [mock_store.get(sku, 0) for sku in table["sku"]]
    note_col = [pending_notes.get(sku, orig) for sku, orig in zip(table["sku"], table["note_preview"])]

    edit_df.insert(list(edit_df.columns).index("DOI") + 1 if "DOI" in edit_df.columns else len(edit_df.columns),
                   "Mock Units", mock_units_col)


    def _future_doi(pos):
        mu = mock_units_col[pos]
        dv = table.iloc[pos][daily_col] if daily_col in table.columns else 0
        fi = table.iloc[pos]["fulfillable_plus_inbound"]
        if mu and dv and dv > 0:
            return round((fi + mu) / dv)
        return None


    edit_df.insert(list(edit_df.columns).index("Mock Units") + 1, "Future DOI",
                   [_future_doi(i) for i in range(len(table))])
    edit_df["Note"] = note_col

    # ---- Column order / width — Streamlit can't remember a live-dragged column resize or ----
    # ---- reorder in the table itself, so this is an explicit settings panel that persists ----
    # ---- instead. Computed here, now that every column (including Mock Units / Future DOI / ----
    # ---- Note) actually exists on edit_df — anything left out of column_order gets hidden. ----
    grid_prefs = store.get_grid_prefs(brand)
    all_col_labels = list(edit_df.columns)
    with st.expander("⚙️ Customize grid layout"):
        st.caption("Streamlit's table can't remember a column you dragged wider or reordered directly in "
                   "the grid — that resets every time the page reloads. Set your preferred order and width "
                   "here instead and it'll persist across sessions.")
        default_order = [c for c in (grid_prefs["column_order"] or all_col_labels) if c in all_col_labels]
        chosen_order = st.multiselect("Column order (pick in the order you want, left to right)",
                                       all_col_labels, default=default_order, key="grid_order_picker")
        width_options = ["Compact", "Comfortable", "Wide"]
        width_preset = st.selectbox("Column width", width_options,
                                     index=width_options.index(grid_prefs["width_preset"])
                                     if grid_prefs["width_preset"] in width_options else 1,
                                     key="grid_width_picker")
        if st.button("💾 Save layout"):
            final_order = chosen_order + [c for c in all_col_labels if c not in chosen_order]
            store.save_grid_prefs(brand, final_order, width_preset)
            st.success("Layout saved — it'll be there next time you open this page.")
            st.rerun()

    saved_order = grid_prefs["column_order"]
    column_order = None
    if saved_order:
        column_order = [c for c in saved_order if c in edit_df.columns]
        column_order += [c for c in edit_df.columns if c not in column_order]

    width_map = {"Compact": "small", "Comfortable": "medium", "Wide": "large"}
    default_width = width_map.get(grid_prefs["width_preset"], "medium")

    base_column_config = {c: st.column_config.Column(width=default_width) for c in edit_df.columns}
    base_column_config.update({
        "🔎": st.column_config.CheckboxColumn(
            help="Check to open this SKU's details popup.", width="small"),
        "Daily Avg": st.column_config.NumberColumn(format="%.1f", width=default_width),
        "7d DOI": st.column_config.NumberColumn(
            help="Projected DOI if the last 7 days' sell-through rate continues.", width=default_width),
        "% Mix": st.column_config.NumberColumn(format="%.1f%%", width=default_width),
        "vs 30d %": st.column_config.NumberColumn(format="%d%%", width=default_width),
        "vs 90d %": st.column_config.NumberColumn(format="%d%%", width=default_width),
        "DOI": st.column_config.ProgressColumn(
            help="Current DOI, with a bar showing it relative to your target (100% of the "
                 "bar = 2x target). The Flag column's 🟢/🟡/🔴 is the color-coded signal — "
                 "Streamlit can't color this bar itself based on its value.",
            format="%dd", min_value=0, max_value=max(int(target_doi) * 2, 1), width=default_width),
        "Mock Units": st.column_config.NumberColumn(
            help="Type a hypothetical incoming quantity to see Future DOI update.", min_value=0, width=default_width),
        "Future DOI": st.column_config.NumberColumn(
            help="Projected DOI if the Mock Units quantity arrived today.", disabled=True, width=default_width),
        "Note": st.column_config.TextColumn(width=default_width),
    })
    # ---- Best-effort grouped header strip ----
    # Streamlit's table has no real merged/spanning header row (checked directly in its
    # source — no such feature exists), so this fakes one: a row of st.columns() sized by
    # how many grid columns each group spans, placed right above the table. Since every
    # column below uses the same width preset, count-based proportions line up reasonably
    # well — not pixel-perfect (no horizontal-scroll awareness, and very long/short window
    # widths can drift it slightly), but close, and it recomputes if you reorder columns
    # in "Customize grid layout" above.
    GROUP_MAP = {
        "🔎": "", "Alerts": "Status", "Aged": "Status",
        "Flag": "DOI", "DOI": "DOI", "Mock Units": "What-If", "Future DOI": "What-If", "7d DOI": "DOI",
        "Trend": "Trend", "vs 30d %": "Trend", "vs 90d %": "Trend",
        "Title": "Item", "SKU": "Item", "ASIN": "Item",
        "Fulfillable": "Inventory", "Fulf.+Inbound": "Inventory",
        "Inbound: Working": "Inbound", "Inbound: Shipped": "Inbound", "Inbound: Receiving": "Inbound",
        "Inbound: FC Transfer": "Inbound", "Inbound: FC Processing": "Inbound",
        "Sales: 7d Units": "Sales", "Sales: 30d Units": "Sales", "Sales: 60d Units": "Sales",
        "Sales: 90d Units": "Sales", "Daily Avg": "Sales", "% Mix": "Sales",
        "Order By": "Replenishment", "Status": "Replenishment", "Action": "Replenishment",
        "Order Units": "Replenishment", "Order Cases": "Replenishment",
        "PD Mult.": "Prime Day", "PD Daily Avg": "Prime Day", "PD DOI (event)": "Prime Day",
        "PD Order Units": "Prime Day", "PD Order Cases": "Prime Day", "PD Status": "Prime Day",
        "Synced": "", "Note": "",
    }
    visible_order = column_order if column_order else list(edit_df.columns)
    segments = []  # list of [group_label, column_count]
    for col in visible_order:
        label = GROUP_MAP.get(col, "")
        if segments and segments[-1][0] == label:
            segments[-1][1] += 1
        else:
            segments.append([label, 1])
    header_cols = st.columns([count for _, count in segments])
    for hc, (label, _count) in zip(header_cols, segments):
        if label:
            hc.markdown(f"**{label}**")

    result = st.data_editor(
        edit_df, use_container_width=True, hide_index=True, height=520, key=editor_key,
        column_order=column_order,
        disabled=[c for c in edit_df.columns if c not in ("Mock Units", "Note", "🔎")],
        column_config=base_column_config,
    )

    if st.button("💾 Save note changes"):
        saved = 0
        for pos in range(len(table)):
            sku_val = table.iloc[pos]["sku"]
            new_note_val = str(pending_notes.get(sku_val, table.iloc[pos]["note_preview"]) or "")
            original = notes_df.loc[notes_df["sku"] == sku_val, "note"]
            original_val = str(original.iloc[0]) if len(original) else ""
            if new_note_val != original_val:
                store.save_note(brand, sku_val, new_note_val, updated_by="team")
                pending_notes.pop(sku_val, None)
                saved += 1
        st.toast(f"Saved {saved} note(s)." if saved else "No note changes to save.")
        st.rerun()

    # ---- CSV export (formula-injection safe, mode-aware) ----
    export = table.copy()
    for col in ("title", "sku", "asin", "sku_status"):
        if col in export.columns:
            export[col] = export[col].map(csv_safe)
    mode_label = "primeday_" if mode == "prime_day" else ""
    st.download_button("⬇️ Export CSV", export.to_csv(index=False).encode("utf-8"),
                        file_name=f"{brand}_inventory_{mode_label}{date.today()}.csv", mime="text/csv")

    # ---- Aged Inventory — its own table, separate from the main grid ----
    aged = calc.aged_summary(derived if mode != "prime_day" else calc.compute_derived(active_inv, target_doi, lead_time))
    st.divider()
    st.subheader(f"⚠️ Aged Inventory{f' ({len(aged)})' if not aged.empty else ''}")
    if aged.empty:
        st.caption("No aged inventory (91+ days) or AIS-fee-triggering quantity right now.")
    else:
        aged_view = aged[["title", "sku", "inv_age_91_180", "inv_age_181_270", "inv_age_271_365",
                           "inv_age_365_plus", "ais_qty_total"]].rename(columns={
            "title": "Title", "sku": "SKU", "inv_age_91_180": "91-180d", "inv_age_181_270": "181-270d",
            "inv_age_271_365": "271-365d", "inv_age_365_plus": "365d+", "ais_qty_total": "AIS Units",
        })
        st.dataframe(aged_view, use_container_width=True, hide_index=True)

    # ---- SKU deep-dive: a real popup (st.dialog) ----
    # Honest limitation: Streamlit's editable grid has no click-to-select the way a
    # read-only table does — that interaction isn't exposed by data_editor at all, so this
    # stays selectbox + button rather than clicking the row/SKU directly.
    @st.dialog("🔎 SKU Deep-Dive", width="large")
    def _sku_deep_dive_dialog(dd_sku):
        row = table[table["sku"] == dd_sku].iloc[0]
        st.markdown(f"**{row['title']}** — `{dd_sku}`")
        days = st.radio("History range", [30, 60, 90], horizontal=True, index=2, key=f"range_{dd_sku}")
        hist = doi_history.get_history_by_sku(brand, dd_sku, days)
        if hist.empty:
            st.info("No history yet for this SKU — a snapshot is saved automatically once per day; "
                     "check back tomorrow to start seeing a trend.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.caption("DOI over time")
                doi_chart_df = hist.set_index("snapshot_date")[["doi", "future_doi"]]
                doi_chart_df.columns = ["Current DOI", "Future DOI"]
                st.line_chart(doi_chart_df)
            with c2:
                st.caption("Daily average units sold")
                avg_chart_df = hist.set_index("snapshot_date")[["daily_avg"]]
                avg_chart_df.columns = ["Daily Avg Units Sold"]
                st.line_chart(avg_chart_df)
            stats_box = doi_history.sku_stats(hist)
            if stats_box:
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric("Avg DOI", stats_box["avg_doi"])
                sc2.metric("Min / Max", f"{stats_box['min_doi']} / {stats_box['max_doi']}")
                trend_val = stats_box["trend"]
                sc3.metric("Trend", f"{'+' if trend_val >= 0 else ''}{trend_val}d", delta=trend_val)

        st.divider()
        st.caption("Dimensions & case packs")
        dims = store.get_sku_dimensions(brand, dd_sku) or {}
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Weight (lb)", dims.get("weight_lb", "—") or "—")
        d2.metric("Longest (in)", dims.get("longest_side", "—") or "—")
        d3.metric("Median (in)", dims.get("median_side", "—") or "—")
        d4.metric("Shortest (in)", dims.get("shortest_side", "—") or "—")

        packs = store.get_case_packs(brand, dd_sku)
        if not packs.empty:
            st.dataframe(packs.drop(columns=["id", "brand_code", "sku"]),
                         use_container_width=True, hide_index=True)
        else:
            st.caption("No case packs saved yet for this SKU.")

        with st.form(f"add_pack_{dd_sku}"):
            st.caption("Add a case pack")
            p1, p2, p3, p4 = st.columns(4)
            pack_name = p1.text_input("Pack name", placeholder="e.g. Case of 24")
            p_units = p2.number_input("Units", min_value=1, value=24)
            p_wt = p3.number_input("Weight (lb)", min_value=0.0, value=0.0)
            p_l = p4.number_input("Length (in)", min_value=0.0, value=0.0)
            p5, p6 = st.columns(2)
            p_w = p5.number_input("Width (in)", min_value=0.0, value=0.0)
            p_h = p6.number_input("Height (in)", min_value=0.0, value=0.0)
            if st.form_submit_button("Add case pack"):
                if pack_name.strip():
                    store.save_case_pack(brand, dd_sku, pack_name.strip(), p_units, p_l, p_w, p_h, p_wt)
                    st.toast(f"Added case pack '{pack_name}'.")
                    st.rerun()
                else:
                    st.error("Give the case pack a name first.")

        st.divider()
        st.button("🚚 Log a shipment for this SKU", key=f"shipbtn_{dd_sku}",
                   on_click=goto, args=("Shipments", brand), kwargs={"prefill_sku": dd_sku})

    st.caption("🔎 Check the box on any row above to open that SKU's details popup.")
    if _view_trigger_sku:
        _sku_deep_dive_dialog(_view_trigger_sku)


# =============================================================== sidebar
clients = store.get_clients()
PAGES = ["Overview", "Dashboard", "Health Report", "Shipments", "Digest Preview",
         "FF Pro Sync", "QA", "Clients"]

with st.sidebar:
    st.title("📦 FBA Inventory")
    page = st.radio("Page", PAGES, label_visibility="collapsed", key="nav_page")
    st.divider()

    active = None
    if clients.empty:
        st.info("No clients yet — add one on the Clients page.")
    elif page not in ("Overview", "Clients", "FF Pro Sync", "QA"):
        names = clients["client_name"].tolist()
        chosen = st.selectbox("Client", names, key="client_selector")
        active = clients[clients["client_name"] == chosen].iloc[0]

        if st.button("🔄 Sync now", use_container_width=True):
            sheets.clear_inventory_cache()
            st.toast("Cache cleared — pulling fresh data from Google Sheets.")

        last_checked = store.get_last_checked(active["brand_code"])
        if last_checked:
            st.caption(f"✓ Last checked {last_checked['checked_at']} by {last_checked['checked_by']}")
        if st.button("Mark as checked", use_container_width=True):
            store.mark_checked(active["brand_code"], "team")
            st.rerun()


def load_client_inventory(client_row):
    try:
        with st.spinner(f"Loading {client_row['client_name']} inventory…"):
            inv = sheets.fetch_inventory(client_row["sheet_id"], client_row["tab_name"])
        return inv, None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


# =============================================================== OVERVIEW
if page == "Overview":
    st.header("📊 Portfolio Overview")
    if clients.empty:
        st.info("No clients configured yet. Add one on the Clients page.")
        st.stop()

    rows_summary = []
    for _, c in clients.iterrows():
        inv, err = load_client_inventory(c)
        if err:
            st.warning(f"**{c['client_name']}**: {err}")
            continue
        settings = store.get_settings(c["brand_code"])
        active_inv = calc.active_only(inv)
        derived = calc.compute_derived(active_inv, settings["target_doi"], settings["lead_time"])
        summary = calc.client_summary(inv, settings["target_doi"])
        aged = calc.aged_summary(derived)
        shipments_df = store.get_shipments(c["brand_code"])
        active_ships = shipments_df[shipments_df["status"].isin(["pending", "shipped"])] if not shipments_df.empty else shipments_df

        oos = int((derived["fulfillable"] == 0).sum()) if not derived.empty else 0
        critical = int((derived["current_doi"] < settings["lead_time"]).sum()) if not derived.empty else 0
        overdue = int((derived["days_until_order"] < 0).sum()) if not derived.empty else 0

        rows_summary.append({
            "Client": c["client_name"], "Brand": c["brand_code"],
            "In-stock %": summary["in_stock_rate"], "Active SKUs": summary["active_skus"],
            "OOS": oos, "Critical (<lead time)": critical, "Overdue orders": overdue,
            "Aged SKUs": len(aged), "Shipments in transit": len(active_ships),
            "Daily units": summary["daily_total"],
        })

    if rows_summary:
        st.dataframe(pd.DataFrame(rows_summary), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Jump to a client")
    cols = st.columns(min(4, len(clients)) or 1)
    for i, (_, c) in enumerate(clients.iterrows()):
        with cols[i % len(cols)]:
            st.button(f"Open {c['client_name']} →", key=f"open_{c['brand_code']}", use_container_width=True,
                      on_click=goto, args=("Dashboard", c["brand_code"]))
    st.stop()


# =============================================================== CLIENTS
if page == "Clients":
    st.header("Clients")
    if not clients.empty:
        st.dataframe(clients, use_container_width=True, hide_index=True)

    st.subheader("Add / update a client")
    st.caption("Saving with an existing Brand Code updates that client.")
    with st.form("client_form"):
        c1, c2 = st.columns(2)
        client_name = c1.text_input("Client name", placeholder="e.g. Earth Echo")
        brand_code = c2.text_input("Brand code", placeholder="e.g. CCB").upper()
        sheet_id = st.text_input("Google Sheet ID",
                                  help="From the URL: docs.google.com/spreadsheets/d/THIS_PART/edit")
        c3, c4 = st.columns(2)
        tab_name = c3.text_input("Ordering Template tab", value="Ordering_Template_US")
        marketplace = c4.selectbox("Marketplace", ["United States", "Canada", "United Kingdom",
                                                     "Germany", "France", "Japan", "Australia"])
        if st.form_submit_button("Save client", type="primary"):
            if client_name and brand_code and sheet_id:
                store.save_client(client_name, brand_code, sheet_id, tab_name, marketplace)
                st.success(f"Saved {client_name}. Remember to share the sheet with the service account (Viewer).")
                st.rerun()
            else:
                st.error("Client name, brand code, and sheet ID are required.")

    if not clients.empty:
        st.subheader("Client profile & links")
        prof_client = st.selectbox("Client", clients["client_name"].tolist(), key="prof_client")
        prof_brand = clients[clients["client_name"] == prof_client].iloc[0]["brand_code"]
        prof = store.get_client_profile(prof_brand)
        with st.form("profile_form"):
            account_location = st.text_input("Account location", value=prof.get("account_location", ""))
            categories = st.text_input("Categories", value=prof.get("categories", ""))
            sku_sheet_link = st.text_input("SKU sheet link", value=prof.get("sku_sheet_link", ""))
            replenishment_link = st.text_input("Replenishment link", value=prof.get("replenishment_link", ""))
            client_notes = st.text_area("Client notes", value=prof.get("client_notes", ""))
            ops_mistakes = st.text_area("Common ops mistakes to avoid", value=prof.get("ops_mistakes", ""))
            if st.form_submit_button("Save profile"):
                store.save_client_profile(prof_brand, account_location=account_location, marketplaces="",
                                           categories=categories, client_notes=client_notes,
                                           sku_sheet_link=sku_sheet_link, replenishment_link=replenishment_link,
                                           ops_mistakes=ops_mistakes)
                st.success("Profile saved.")

        st.subheader("Health report folder (for Health Report page)")
        hf_client = st.selectbox("Client", clients["client_name"].tolist(), key="hf_client")
        hf_brand = clients[clients["client_name"] == hf_client].iloc[0]["brand_code"]
        current_folder = store.get_health_folder(hf_brand)
        new_folder = st.text_input("Google Drive folder ID", value=current_folder,
                                    help="The folder where stranded/unfulfillable reports are uploaded.")
        if st.button("Save folder ID"):
            store.save_health_folder(hf_brand, new_folder)
            st.success("Saved.")

        st.subheader("Delete a client")
        target = st.selectbox("Client to delete", clients["client_name"].tolist(), key="del")
        if st.button("Delete", type="secondary"):
            code = clients[clients["client_name"] == target].iloc[0]["brand_code"]
            store.delete_client(code)
            st.rerun()
    st.stop()


# =============================================================== FF PRO SYNC
if page == "FF Pro Sync":
    st.header("📋 FF Pro Sync")
    st.caption("Upload one or more copy templates (.docx) and your Amazon listing export (.xlsx). "
               "The DOCX is the source of truth — differences are highlighted per ASIN, per field.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**📄 Copy template(s) (.docx) — source of truth**")
        docx_uploads = st.file_uploader("DOCX files", type=["docx"], accept_multiple_files=True,
                                         label_visibility="collapsed", key="cr_docx")
    with c2:
        st.markdown("**📊 Amazon listing export (.xlsx) — what's uploaded**")
        xlsx_upload = st.file_uploader("XLSX file", type=["xlsx"], label_visibility="collapsed", key="cr_xlsx")

    label = st.text_input("Label (optional)", placeholder="e.g. SIG Enclosure June 2026", key="cr_label")

    title_mode = st.session_state.get("cr_title_mode")
    if st.button("Run FF Pro Sync", type="primary", disabled=not (docx_uploads and xlsx_upload)):
        with st.spinner("Parsing documents and comparing fields…"):
            try:
                docx_asins = {}
                docx_names = []
                needs_choice = False
                for uf in docx_uploads:
                    tmp_path = f"/tmp/{uf.name}"
                    with open(tmp_path, "wb") as f:
                        f.write(uf.getvalue())
                    parsed = docx_parser.parse(tmp_path, title_mode or "auto")
                    if parsed["needs_title_choice"]:
                        needs_choice = True
                        break
                    docx_asins.update(parsed["asins"])
                    docx_names.append(uf.name)

                if needs_choice:
                    st.session_state["cr_needs_choice"] = True
                    st.session_state["cr_title_modes"] = parsed["title_modes"]
                else:
                    st.session_state["cr_needs_choice"] = False
                    xlsx_tmp = f"/tmp/{xlsx_upload.name}"
                    with open(xlsx_tmp, "wb") as f:
                        f.write(xlsx_upload.getvalue())
                    xlsx_data = xlsx_export_parser.parse(xlsx_tmp)

                    results = {}
                    fields_to_compare = {
                        "title": "Title", "bullet_1": "Bullet 1", "bullet_2": "Bullet 2",
                        "bullet_3": "Bullet 3", "bullet_4": "Bullet 4", "bullet_5": "Bullet 5",
                        "description": "Description", "keywords": "Generic Keywords",
                    }
                    for asin, docx_row in docx_asins.items():
                        xlsx_row = xlsx_data.get(asin, {})
                        comparisons = []
                        for field, flabel in fields_to_compare.items():
                            source = (docx_row.get(field) or "").strip()
                            if not source:
                                continue
                            uploaded = (xlsx_row.get(field) or "").strip()
                            cmp = diffing.compare_field(source, uploaded)
                            comparisons.append({"field": field, "label": flabel, "source": source,
                                                 "uploaded": uploaded, **cmp})
                        results[asin] = {
                            "asin": asin, "sku": docx_row.get("sku", ""),
                            "skus": xlsx_row.get("skus", []), "title": docx_row.get("title", asin),
                            "in_xlsx": asin in xlsx_data, "fields": comparisons,
                        }

                    run_id = store.save_crossref_run(
                        label or f"Run {date.today()}", docx_names, xlsx_upload.name, results, title_mode or "auto")
                    st.session_state["cr_results"] = results
                    st.session_state["cr_run_id"] = run_id
                    st.session_state["cr_title_mode"] = None
                    st.success(f"Compared {len(results)} ASIN(s).")
            except Exception as e:  # noqa: BLE001
                st.error(f"Sync failed: {e}")

    if st.session_state.get("cr_needs_choice"):
        st.warning("📋 The DOCX has both TEST and CURRENT titles — which should be used?")
        cc1, cc2 = st.columns(2)
        if cc1.button("Use TEST titles"):
            st.session_state["cr_title_mode"] = "test"
            st.rerun()
        if cc2.button("Use CURRENT titles"):
            st.session_state["cr_title_mode"] = "current"
            st.rerun()

    st.divider()
    st.subheader("History")
    cr_runs = store.get_crossref_runs()
    if cr_runs.empty:
        st.caption("No runs yet.")
    else:
        for _, run in cr_runs.head(20).iterrows():
            rate = round(int(run["pass_count"]) / max(int(run["asin_count"]), 1) * 100)
            rc1, rc2, rc3 = st.columns([4, 1, 1])
            rc1.markdown(f"**{run['label']}** — {run['docx_files']} · {run['created_at']}")
            rc2.markdown(f"{rate}% pass")
            if rc3.button("Load", key=f"loadcr_{run['run_id']}"):
                res_df = store.get_crossref_run_results(run["run_id"])
                results = {}
                for _, r in res_df.iterrows():
                    a = r["asin"]
                    if a not in results:
                        import json as _json
                        try:
                            skus = _json.loads(r["skus"]) if r["skus"] else []
                        except (ValueError, TypeError):
                            skus = []
                        results[a] = {"asin": a, "sku": "", "skus": skus, "title": r["title"],
                                      "in_xlsx": True, "fields": []}
                    if r["field"] == "asin_missing":
                        results[a]["in_xlsx"] = False
                    else:
                        results[a]["fields"].append({
                            "field": r["field"], "label": r["field"].replace("_", " ").title(),
                            "source": r["source"], "uploaded": r["uploaded"],
                            "result": r["result"], "similarity": int(float(r["similarity"] or 0)),
                        })
                st.session_state["cr_results"] = results
                st.session_state["cr_run_id"] = run["run_id"]
                st.rerun()

    results = st.session_state.get("cr_results")
    if results:
        st.divider()
        pass_n = sum(1 for r in results.values() if r.get("in_xlsx", True) and all(f["result"] == "pass" for f in r["fields"]))
        fail_n = len(results) - pass_n
        k1, k2, k3 = st.columns(3)
        k1.metric("ASINs", len(results))
        k2.metric("Passed", pass_n)
        k3.metric("Failed", fail_n)

        f1, f2, f3 = st.columns([1, 1, 2])
        filt = f1.radio("Filter", ["all", "fail", "missing", "pass"], horizontal=True, key="cr_filter")
        hide_parents = f2.toggle("Hide parent SKUs", key="cr_hide_parents")
        search = f3.text_input("Search ASIN, SKU, or title", key="cr_search")

        html_out = _render_comparison_table(results, filt, hide_parents, search, "DOCX (source of truth)", "XLSX (uploaded)")
        st.components.v1.html(html_out, height=600, scrolling=True)

        exp1, exp2 = st.columns(2)
        export_df = _build_export_df(results)
        exp1.download_button("⬇️ Export CSV", export_df.to_csv(index=False).encode("utf-8"),
                              file_name=f"ff-pro-sync-{date.today()}.csv", mime="text/csv")
        diff_df = _build_diff_export_df(results, hide_parents)
        if diff_df is not None:
            exp2.download_button("⬇️ Export Differences", diff_df.to_csv(index=False).encode("utf-8"),
                                  file_name=f"ff-pro-sync-differences-{date.today()}.csv", mime="text/csv")
    st.stop()


# =============================================================== QA
if page == "QA":
    st.header("✅ Listing QA")
    st.caption("Upload your Amazon listing export (or a copy template DOCX). Each ASIN is fetched live "
               "from Amazon and compared field by field — anything that doesn't match is flagged.")

    qa_upload = st.file_uploader("Expected content file (.xlsx or .docx)", type=["xlsx", "docx"], key="qa_file")
    qa_label = st.text_input("Run label (optional)", placeholder="e.g. Earth Echo June upload", key="qa_label")
    qa_title_mode = st.session_state.get("qa_title_mode")

    if st.button("Run QA check", type="primary", disabled=not qa_upload):
        with st.spinner("Parsing file and fetching live listings…"):
            try:
                tmp_path = f"/tmp/{qa_upload.name}"
                with open(tmp_path, "wb") as f:
                    f.write(qa_upload.getvalue())

                if qa_upload.name.lower().endswith(".docx"):
                    parsed = docx_parser.parse(tmp_path, qa_title_mode or "auto")
                    if parsed["needs_title_choice"]:
                        st.session_state["qa_needs_choice"] = True
                        st.session_state["qa_title_modes"] = parsed["title_modes"]
                        st.stop()
                    st.session_state["qa_needs_choice"] = False
                    expected_data = parsed["asins"]
                else:
                    expected_data = xlsx_export_parser.parse(tmp_path)

                progress = st.progress(0.0, text="Fetching listings from Amazon…")
                results = {}
                asins = list(expected_data.items())
                for i, (asin, expected) in enumerate(asins):
                    progress.progress((i + 1) / len(asins), text=f"Fetching {asin} ({i + 1}/{len(asins)})…")
                    try:
                        scraped = amazon_scraper.fetch(asin, "US")
                    except amazon_scraper.ScrapeError as e:
                        results[asin] = {"asin": asin, "sku": expected.get("sku", ""),
                                          "title": expected.get("title", ""), "error": str(e), "fields": []}
                        continue

                    fields_to_compare = {"title": "Title", "brand": "Brand", "bullet_1": "Bullet 1",
                                          "bullet_2": "Bullet 2", "bullet_3": "Bullet 3",
                                          "bullet_4": "Bullet 4", "bullet_5": "Bullet 5"}
                    comparisons = []
                    for field, flabel in fields_to_compare.items():
                        source = (expected.get(field) or "").strip()
                        if not source:
                            continue
                        actual = (scraped.get(field) or "").strip()
                        cmp = diffing.compare_field(source, actual)
                        comparisons.append({"field": field, "label": flabel, "expected": source,
                                             "actual": actual, "source": source, "uploaded": actual, **cmp})
                    results[asin] = {"asin": asin, "sku": expected.get("sku", ""),
                                      "title": expected.get("title", asin), "fields": comparisons}
                progress.empty()

                run_id = store.save_qa_run(qa_label or f"Run {date.today()}", qa_upload.name, results)
                st.session_state["qa_results"] = results
                st.session_state["qa_run_id"] = run_id
                st.session_state["qa_title_mode"] = None
                st.success(f"Checked {len(results)} ASIN(s).")
            except Exception as e:  # noqa: BLE001
                st.error(f"QA run failed: {e}")

    if st.session_state.get("qa_needs_choice"):
        st.warning("📋 This document has both TEST and CURRENT titles — which should be used?")
        qc1, qc2 = st.columns(2)
        if qc1.button("Use TEST titles", key="qa_test_btn"):
            st.session_state["qa_title_mode"] = "test"
            st.rerun()
        if qc2.button("Use CURRENT titles", key="qa_current_btn"):
            st.session_state["qa_title_mode"] = "current"
            st.rerun()

    st.divider()
    st.subheader("QA history")
    qa_runs = store.get_qa_runs()
    if qa_runs.empty:
        st.caption("No runs yet.")
    else:
        for _, run in qa_runs.head(20).iterrows():
            rate = round(int(run["pass_count"]) / max(int(run["asin_count"]), 1) * 100)
            rc1, rc2, rc3 = st.columns([4, 1, 1])
            rc1.markdown(f"**{run['label']}** — {run['file_name']} · {run['created_at']}")
            rc2.markdown(f"{rate}% pass")
            if rc3.button("Load", key=f"loadqa_{run['run_id']}"):
                res_df = store.get_qa_run_results(run["run_id"])
                results = {}
                for _, r in res_df.iterrows():
                    a = r["asin"]
                    if a not in results:
                        results[a] = {"asin": a, "sku": r["sku"], "title": r["title"], "fields": [], "error": ""}
                    if r["field"] == "scrape_error":
                        results[a]["error"] = r["error"]
                    else:
                        results[a]["fields"].append({
                            "field": r["field"], "label": r["field"].replace("_", " ").title(),
                            "expected": r["expected"], "actual": r["actual"], "source": r["expected"],
                            "uploaded": r["actual"], "result": r["result"],
                            "similarity": int(float(r["similarity"] or 0)),
                        })
                st.session_state["qa_results"] = results
                st.session_state["qa_run_id"] = run["run_id"]
                st.rerun()

    qa_results = st.session_state.get("qa_results")
    if qa_results:
        st.divider()
        pass_n = sum(1 for r in qa_results.values() if not r.get("error") and all(f["result"] == "pass" for f in r["fields"]))
        fail_n = len(qa_results) - pass_n
        k1, k2, k3 = st.columns(3)
        k1.metric("ASINs", len(qa_results))
        k2.metric("Passed", pass_n)
        k3.metric("Failed", fail_n)

        f1, f2 = st.columns([1, 2])
        qa_filt = f1.radio("Filter", ["all", "fail", "missing", "pass"], horizontal=True, key="qa_filter")
        qa_search = f2.text_input("Search ASIN or title", key="qa_search")

        html_out = _render_comparison_table(qa_results, qa_filt, False, qa_search, "Expected (your file)", "Actual (live on Amazon)")
        st.components.v1.html(html_out, height=600, scrolling=True)

        export_df = _build_export_df(qa_results)
        st.download_button("⬇️ Export CSV", export_df.to_csv(index=False).encode("utf-8"),
                            file_name=f"qa-{date.today()}.csv", mime="text/csv")
    st.stop()


# ---- pages below need an active client ----
if active is None:
    st.info("Add a client first (Clients page).")
    st.stop()

brand = active["brand_code"]
inv, err = load_client_inventory(active)
if err:
    st.error(f"Could not load the Google Sheet: {err}")
    st.info("Check that the tab name is right and the sheet is shared with the service account email (Viewer access).")
    st.stop()

settings = store.get_settings(brand)


# =============================================================== DIGEST PREVIEW
if page == "Digest Preview":
    st.header("📧 Digest Preview")
    st.caption("Matches the Mon/Wed/Fri email digest from the WordPress plugin — one combined email for every client.")

    all_data = []
    for _, c in clients.iterrows():
        c_inv, c_err = load_client_inventory(c)
        if c_err:
            continue
        c_settings = store.get_settings(c["brand_code"])
        c_ships = store.get_shipments(c["brand_code"])
        all_data.append((c, c_inv, c_ships, c_settings["target_doi"], c_settings["lead_time"]))

    html = digest.build_digest(all_data)
    st.download_button("⬇️ Download as .html", html.encode("utf-8"),
                        file_name=f"fba_digest_{date.today()}.html", mime="text/html")

    st.subheader("Recipients")
    recipients = store.get_digest_recipients()
    recipients_text = st.text_area("One email per line", value="\n".join(recipients), height=100)
    if st.button("Save recipients"):
        store.save_digest_recipients(recipients_text.splitlines())
        st.success("Saved.")
        st.rerun()

    if st.button("📤 Send now", type="primary"):
        recips = store.get_digest_recipients()
        if not recips:
            st.error("Add at least one recipient above first.")
        else:
            ok, msg = digest.send_email_now(html, f"📦 Emplicit FBA Digest — {date.today():%b %d, %Y}", recips)
            (st.success if ok else st.error)(msg)

    st.divider()
    st.components.v1.html(html, height=900, scrolling=True)
    st.stop()


# =============================================================== HEALTH REPORT
if page == "Health Report":
    st.header(f"🩺 Health Report — {active['client_name']}")
    folder_id = store.get_health_folder(brand)
    if not folder_id:
        st.warning("No Drive folder configured for this client yet. Set it on the Clients page.")
        st.stop()

    if st.button("🔄 Sync health report from Drive"):
        with st.spinner("Reading the latest report from Drive…"):
            try:
                result = health.sync_and_save(brand, folder_id)
                st.success(f"Synced from **{result['file_name']}** — "
                           f"{result['stranded_count']} stranded, {result['unfulfillable_count']} unfulfillable.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Sync failed: {e}")

    data = health.get_health_data(brand)
    if data["file_name"]:
        st.caption(f"Last synced from **{data['file_name']}** at {data['synced_at']}")

    st.subheader(f"📦 Stranded Inventory ({len(data['stranded'])})")
    if data["stranded"].empty:
        st.info("No stranded inventory on file. Sync above to pull the latest report.")
    else:
        st.dataframe(data["stranded"].drop(columns=["brand_code"]), use_container_width=True, hide_index=True)

    st.subheader(f"🚫 Unfulfillable Inventory ({len(data['unfulfillable'])})")
    if data["unfulfillable"].empty:
        st.info("No unfulfillable inventory on file.")
    else:
        st.dataframe(data["unfulfillable"].drop(columns=["brand_code"]), use_container_width=True, hide_index=True)
    st.stop()


# =============================================================== SHIPMENTS
if page == "Shipments":
    st.header(f"🚚 Shipments — {active['client_name']}")
    active_inv = calc.active_only(inv)

    tab1, tab2, tab3 = st.tabs(["Shipment Log", "Case Packs & Dimensions", "Build Shipment File"])

    with tab1:
        ships = store.get_shipments(brand)
        if ships.empty:
            st.info("No shipments logged yet.")
        else:
            for idx, s in ships.reset_index(drop=True).iterrows():
                carrier = s["carrier"] or (calc.detect_carrier(s["tracking_number"]) if s["tracking_number"] else "")
                status_icon = {"pending": "⏳", "shipped": "🚚", "delivered": "✅"}.get(s["status"], "•")
                with st.expander(f"{status_icon} {s['sku']} — {int(float(s['units'] or 0)):,} units — {s['status']}"):
                    c1, c2 = st.columns(2)
                    fba_id = c1.text_input("FBA shipment ID", value=s["fba_shipment_id"], key=f"fba_{idx}")
                    tracking = c2.text_input("Tracking number", value=s["tracking_number"], key=f"trk_{idx}")
                    detected = calc.detect_carrier(tracking) if tracking else ""
                    if detected:
                        color = calc.CARRIER_COLORS.get(detected, "#6b7280")
                        url = calc.carrier_track_url(detected, tracking)
                        st.markdown(f'<span style="background:{color};color:white;padding:2px 8px;'
                                    f'border-radius:10px;font-size:12px">{detected}</span> '
                                    f'[Track package]({url})' if url else f"Carrier: {detected}",
                                    unsafe_allow_html=True)
                    new_status = st.selectbox("Status", ["pending", "shipped", "delivered"],
                                               index=["pending", "shipped", "delivered"].index(s["status"]) if s["status"] in ("pending", "shipped", "delivered") else 0,
                                               key=f"status_{idx}")
                    if st.button("Save", key=f"save_ship_{idx}"):
                        store.update_shipment_tracking(brand, s["sku"], s["date_ordered"], fba_id, tracking, detected or "Other")
                        store.update_shipment_status(brand, s["sku"], s["date_ordered"], new_status)
                        st.toast("Shipment updated.")
                        st.rerun()

        st.subheader("Log a new shipment")
        prefill = st.session_state.get("prefill_sku")
        if prefill and "prefill_sku" in st.session_state:
            del st.session_state["prefill_sku"]
        with st.form("ship_form"):
            sku_options = active_inv["sku"].tolist()
            default_idx = sku_options.index(prefill) if prefill in sku_options else 0
            sku = st.selectbox("SKU", sku_options, index=default_idx)
            c1, c2, c3 = st.columns(3)
            units = c1.number_input("Units", min_value=1, value=100)
            fba_id = c2.text_input("FBA shipment ID", placeholder="FBA15XYZ...")
            ordered = c3.date_input("Date ordered", value=date.today())
            expected = st.date_input("Expected receive date (optional)", value=None)
            tracking = st.text_input("Tracking number (optional)")
            notes = st.text_input("Notes", placeholder="optional")
            if st.form_submit_button("Save shipment", type="primary"):
                title = active_inv.loc[active_inv["sku"] == sku, "title"].iloc[0]
                carrier = calc.detect_carrier(tracking) if tracking else ""
                store.add_shipment(brand, sku, title, units, fba_id, tracking, carrier,
                                    ordered, expected or "", notes)
                st.success("Shipment saved.")
                st.rerun()

    with tab2:
        st.markdown("**Sync dimensions & case packs from the Google Sheet**")
        st.caption("Pulls product weight/dimensions and a 'Master Carton' case pack for every SKU from a "
                   "'SKU Sheet' tab in this client's spreadsheet. Doesn't touch any other custom case packs "
                   "you've added below — only the one named exactly 'Master Carton'.")
        sku_sheet_tab = st.text_input("SKU Sheet tab name", value="SKU Sheet", key="sku_sheet_tab_name")
        if st.button("🔄 Sync Dimensions"):
            try:
                with st.spinner("Reading the SKU Sheet tab…"):
                    dims_data = sheets.fetch_sku_sheet(active["sheet_id"], sku_sheet_tab)
                if not dims_data:
                    st.error(f"No data found in the '{sku_sheet_tab}' tab. Check that: (1) the tab is named "
                             f"exactly '{sku_sheet_tab}', (2) it has a SKU column header, and (3) the sheet "
                             f"is shared with the service account.")
                else:
                    size_tier_map = {}
                    if "size_tier" in inv.columns:
                        for _, r in inv.iterrows():
                            if r.get("size_tier"):
                                size_tier_map[r["sku"]] = r["size_tier"]

                    count = 0
                    for sku_val, data in dims_data.items():
                        size_tier = size_tier_map.get(sku_val, "")
                        store.save_sku_dimension(brand, sku_val, data["weight_lb"], data["longest_side"],
                                                  data["median_side"], data["shortest_side"],
                                                  data["units_per_case"], size_tier)
                        has_cp_data = (data["units_per_case"] > 0 or data["cp_weight_lb"] > 0
                                       or data["cp_longest_side"] > 0)
                        if has_cp_data:
                            existing_id = store.get_case_pack_id_by_name(brand, sku_val, "Master Carton")
                            store.save_case_pack(brand, sku_val, "Master Carton", data["units_per_case"],
                                                  data["cp_longest_side"], data["cp_median_side"],
                                                  data["cp_shortest_side"], data["cp_weight_lb"],
                                                  pack_id=existing_id)
                        count += 1
                    st.success(f"Synced dimensions for {count} SKU(s).")
                    st.rerun()
            except ValueError as e:
                st.error(str(e))

        st.divider()
        sku = st.selectbox("SKU", active_inv["sku"].tolist(), key="dims_sku")
        dims = store.get_sku_dimensions(brand, sku) or {}
        with st.form("dims_form"):
            c1, c2, c3, c4 = st.columns(4)
            weight = c1.number_input("Weight (lb)", value=float(dims.get("weight_lb", 0) or 0))
            longest = c2.number_input("Longest side (in)", value=float(dims.get("longest_side", 0) or 0))
            median = c3.number_input("Median side (in)", value=float(dims.get("median_side", 0) or 0))
            shortest = c4.number_input("Shortest side (in)", value=float(dims.get("shortest_side", 0) or 0))
            c5, c6 = st.columns(2)
            upc = c5.number_input("Units per case", min_value=0, value=int(float(dims.get("units_per_case", 0) or 0)))
            size_tier = c6.text_input("Size tier", value=dims.get("size_tier", "") or "")
            if st.form_submit_button("Save dimensions"):
                store.save_sku_dimension(brand, sku, weight, longest, median, shortest, upc, size_tier)
                st.success("Saved.")
                st.rerun()

        st.subheader(f"Case packs for {sku}")
        packs = store.get_case_packs(brand, sku)
        if not packs.empty:
            st.dataframe(packs.drop(columns=["id", "brand_code", "sku"]), use_container_width=True, hide_index=True)
            del_id = st.selectbox("Delete a pack", packs["pack_name"].tolist(), key="del_pack") if len(packs) else None
            if del_id and st.button("Delete selected pack"):
                pid = packs[packs["pack_name"] == del_id].iloc[0]["id"]
                store.delete_case_pack(pid)
                st.rerun()

        with st.form("pack_form"):
            pack_name = st.text_input("Pack name", placeholder="e.g. Case of 24")
            c1, c2, c3, c4 = st.columns(4)
            p_units = c1.number_input("Units in case", min_value=1, value=24)
            p_l = c2.number_input("Length (in)", value=0.0)
            p_w = c3.number_input("Width (in)", value=0.0)
            p_h = c4.number_input("Height (in)", value=0.0)
            p_wt = st.number_input("Case weight (lb)", value=0.0)
            if st.form_submit_button("Add case pack"):
                store.save_case_pack(brand, sku, pack_name, p_units, p_l, p_w, p_h, p_wt)
                st.success("Added.")
                st.rerun()

    with tab3:
        st.caption("Select SKUs and quantities to generate a downloadable shipment planning spreadsheet.")
        sku_list = active_inv["sku"].tolist()
        selected = st.multiselect("SKUs to include", sku_list)
        line_items = []
        for s in selected:
            row = active_inv[active_inv["sku"] == s].iloc[0]
            dims = store.get_sku_dimensions(brand, s) or {}
            packs = store.get_case_packs(brand, s)
            if not packs.empty:
                pack_choice = st.selectbox(f"Case pack — {s} ({row['title'][:30]})",
                                            packs["pack_name"].tolist(), key=f"packchoice_{s}")
                chosen = packs[packs["pack_name"] == pack_choice].iloc[0]
                pack_name = chosen["pack_name"]
                upc = int(float(chosen["units"])) if float(chosen["units"]) > 0 else int(dims.get("units_per_case", 0) or 0)
                pack_l, pack_w, pack_h, pack_wt = chosen["length_in"], chosen["width_in"], chosen["height_in"], chosen["weight_lb"]
            else:
                pack_name = ""
                upc = int(dims.get("units_per_case", 0) or 0)
                pack_l = pack_w = pack_h = pack_wt = ""
            units = st.number_input(f"Units — {s} ({row['title'][:40]})", min_value=0,
                                     value=int(row.get("order_units_calc", 0)) if "order_units_calc" in row else 0,
                                     key=f"buildunits_{s}")
            cases = (units // upc) if upc else 0
            line_items.append({
                "SKU": s, "Title": row["title"], "Units": units, "Case Pack": pack_name,
                "Units/Case": upc, "Cases": cases,
                "Case Length (in)": pack_l, "Case Width (in)": pack_w,
                "Case Height (in)": pack_h, "Case Weight (lb)": pack_wt,
                "Product Weight (lb)": dims.get("weight_lb", ""),
            })

        if line_items:
            build_df = pd.DataFrame(line_items)
            st.dataframe(build_df, use_container_width=True, hide_index=True)
            if st.button("📄 Generate shipment file (.xlsx)", type="primary"):
                import openpyxl
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = "Shipment Plan"
                ws.append(list(build_df.columns))
                for _, r in build_df.iterrows():
                    ws.append([csv_safe(v) if isinstance(v, str) else v for v in r.tolist()])
                buf = io.BytesIO()
                wb.save(buf)
                st.download_button("⬇️ Download shipment_plan.xlsx", buf.getvalue(),
                                    file_name=f"{brand}_shipment_plan_{date.today()}.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.stop()


# =============================================================== DASHBOARD
render_dashboard_module()
