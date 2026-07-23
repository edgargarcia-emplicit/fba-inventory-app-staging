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


def goto(page_name, brand_code=None):
    st.session_state["nav_page"] = page_name
    if brand_code is not None:
        clients = store.get_clients()
        match = clients[clients["brand_code"] == brand_code]
        if not match.empty:
            st.session_state["client_selector"] = match.iloc[0]["client_name"]
    st.rerun()


# =============================================================== sidebar
clients = store.get_clients()
PAGES = ["Overview", "Dashboard", "Health Report", "DOI History", "Shipments", "Digest Preview", "Clients"]

with st.sidebar:
    st.title("📦 FBA Inventory")
    page = st.radio("Page", PAGES, label_visibility="collapsed", key="nav_page")
    st.divider()

    active = None
    if clients.empty:
        st.info("No clients yet — add one on the Clients page.")
    elif page not in ("Overview", "Clients"):
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
            if st.button(f"Open {c['client_name']} →", key=f"open_{c['brand_code']}", use_container_width=True):
                goto("Dashboard", c["brand_code"])
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


# =============================================================== DOI HISTORY
if page == "DOI History":
    st.header(f"📈 DOI History — {active['client_name']}")
    stats = doi_history.get_tracking_stats(brand)
    if stats["days_tracked"] == 0:
        st.info("No history yet — visit the Dashboard page at least once to start tracking "
                 "(a snapshot is saved automatically once per day).")
        st.stop()

    c1, c2, c3 = st.columns(3)
    c1.metric("Days tracked", stats["days_tracked"])
    c2.metric("SKUs tracked", stats["skus_tracked"])
    c3.metric("Tracking since", stats["first_snapshot"])

    days = st.radio("Range", [30, 60, 90], horizontal=True, index=2)
    hist = doi_history.get_history_by_client(brand, days)
    skus = sorted(hist["sku"].unique().tolist())
    sku = st.selectbox("SKU", skus)
    sku_hist = hist[hist["sku"] == sku].sort_values("snapshot_date")

    stats_box = doi_history.sku_stats(sku_hist)
    if stats_box:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current DOI", stats_box["last_doi"])
        c2.metric("Avg DOI", stats_box["avg_doi"])
        c3.metric("Min / Max", f"{stats_box['min_doi']} / {stats_box['max_doi']}")
        trend = stats_box["trend"]
        c4.metric("Trend", f"{'+' if trend >= 0 else ''}{trend}d", delta=trend)

    chart_df = sku_hist.set_index("snapshot_date")[["doi", "future_doi"]]
    chart_df.columns = ["Current DOI", "Future DOI"]
    st.line_chart(chart_df)
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
        with st.form("ship_form"):
            sku_options = active_inv["sku"].tolist()
            sku = st.selectbox("SKU", sku_options)
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
            pack_name = packs.iloc[0]["pack_name"] if not packs.empty else ""
            upc = int(float(packs.iloc[0]["units"])) if not packs.empty else int(dims.get("units_per_case", 0) or 0)
            units = st.number_input(f"Units — {s} ({row['title'][:40]})", min_value=0,
                                     value=int(row.get("order_units_calc", 0)) if "order_units_calc" in row else 0,
                                     key=f"buildunits_{s}")
            cases = (units // upc) if upc else 0
            line_items.append({
                "SKU": s, "Title": row["title"], "Units": units, "Case Pack": pack_name,
                "Units/Case": upc, "Cases": cases,
                "Length (in)": dims.get("longest_side", ""), "Width (in)": dims.get("median_side", ""),
                "Height (in)": dims.get("shortest_side", ""), "Weight (lb)": dims.get("weight_lb", ""),
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
st.header(f"{active['client_name']} — Inventory Dashboard")
snapshot = inv["snapshot_date"].iloc[0] if len(inv) else ""
if snapshot:
    st.caption(f"Sheet snapshot date: {snapshot}")

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

# ---- Notifications banner ----
health_counts = health.get_notification_counts(brand)
overdue_n = int((derived["days_until_order"] < 0).sum()) if not derived.empty else 0
aged = calc.aged_summary(derived)
aged_fee_n = int((aged["ais_qty_total"] > 0).sum()) if not aged.empty else 0
stranded_n = sum(1 for v in health_counts.values() if "stranded" in v)
unful_n = sum(1 for v in health_counts.values() if "unfulfillable" in v)
attention_total = overdue_n + aged_fee_n + stranded_n + unful_n
if attention_total > 0:
    with st.container(border=True):
        st.markdown(f"🔔 **{attention_total} item(s) need attention**")
        parts = []
        if overdue_n: parts.append(f"{overdue_n} overdue reorder(s)")
        if aged_fee_n: parts.append(f"{aged_fee_n} SKU(s) incurring aged-inventory fees")
        if stranded_n: parts.append(f"{stranded_n} stranded SKU(s)")
        if unful_n: parts.append(f"{unful_n} unfulfillable SKU(s)")
        st.caption(" · ".join(parts) + "  (see Health Report / DOI History pages for detail)")

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
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Active SKUs", total_active)
k2.metric("In-stock rate", f"{(in_stock / total_active * 100):.1f}%" if total_active else "—")
if mode == "prime_day":
    k3.metric("Need Prime Day order", int((derived["pd_units_to_order"] > 0).sum()) if not derived.empty else 0)
    k4.metric("Too late for FBA", int(derived["pd_status"].str.contains("TOO LATE").sum()) if not derived.empty else 0)
else:
    k3.metric("Below target DOI", int((derived["current_doi"] < target_doi).sum()) if not derived.empty else 0)
    k4.metric("Need reorder now", int((derived["order_units_calc"] > 0).sum()) if not derived.empty else 0)
k5.metric("Daily units (all SKUs)", f"{derived['daily_avg'].sum():.0f}" if not derived.empty else "0")

st.divider()

# ---- Aged inventory panel ----
if not aged.empty:
    with st.expander(f"⚠️ Aged Inventory Alerts ({len(aged)} SKUs)"):
        show_cols = ["title", "sku", "inv_age_91_180", "inv_age_181_270", "inv_age_271_365",
                     "inv_age_365_plus", "ais_qty_total"]
        st.dataframe(aged[show_cols].rename(columns={
            "title": "Title", "sku": "SKU", "inv_age_91_180": "91-180d", "inv_age_181_270": "181-270d",
            "inv_age_271_365": "271-365d", "inv_age_365_plus": "365d+", "ais_qty_total": "AIS units",
        }), use_container_width=True, hide_index=True)

# ---- Trending SKUs ----
trending = calc.trending_skus(active_inv)
if not trending.empty:
    with st.expander(f"📈 Trending SKUs ({len(trending)})"):
        st.dataframe(trending[["title", "sku", "flag", "d7", "d30", "vs30_pct", "vs90_pct"]].rename(columns={
            "title": "Title", "sku": "SKU", "flag": "Trend", "d7": "7d daily", "d30": "30d daily",
            "vs30_pct": "vs 30d %", "vs90_pct": "vs 90d %",
        }), use_container_width=True, hide_index=True)

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

# ---- Health badges ----
table["alerts"] = table["sku"].map(lambda s: (
    ("🟥" if health_counts.get(s, {}).get("stranded") else "")
    + ("🟧" if health_counts.get(s, {}).get("unfulfillable") else "")
))

# ---- Mock shipment simulator ----
with st.expander("🧪 Mock shipment — see how a planned shipment changes DOI"):
    m1, m2 = st.columns(2)
    mock_sku = m1.selectbox("SKU", table["sku"].tolist(), key="mock_sku")
    mock_units = m2.number_input("Hypothetical units", 0, 100000, 0, key="mock_units")
    if mock_units > 0:
        row = table[table["sku"] == mock_sku].iloc[0]
        if row["daily_avg"] > 0:
            future = round((row["fulfillable_plus_inbound"] + mock_units) / row["daily_avg"])
            st.success(f"**{mock_sku}** — current DOI {row['current_doi']}d → future DOI **{future}d** with +{mock_units:,} units")
        else:
            st.info("This SKU has no sales velocity, so DOI can't be calculated.")

# ---- Main table ----
if mode == "prime_day":
    display_cols = {
        "alerts": "", "doi_flag": "", "title": "Title", "sku": "SKU",
        "fulfillable": "Fulfillable", "fulfillable_plus_inbound": "Fulf.+Inbound",
        "daily_avg": "Daily Avg", "pd_multiplier": "PD Mult.", "pd_daily_avg": "PD Daily Avg",
        "pd_doi_event": "PD DOI (event)", "pd_units_to_order": "PD Order Units",
        "pd_cases_to_order": "PD Order Cases", "pd_status": "PD Status",
    }
else:
    display_cols = {
        "alerts": "", "doi_flag": "", "title": "Title", "sku": "SKU", "asin": "ASIN",
        "fulfillable": "Fulfillable", "fulfillable_plus_inbound": "Fulf.+Inbound",
        "daily_avg": "Daily Avg", "current_doi": "DOI", "order_by": "Order By",
        "order_status": "Status", "order_units_calc": "Order Units",
        "order_cases_calc": "Order Cases", "pct_sales_mix": "% Mix",
    }
view = table[list(display_cols)].rename(columns=display_cols)
st.dataframe(view, use_container_width=True, hide_index=True, height=520,
             column_config={"Daily Avg": st.column_config.NumberColumn(format="%.1f"),
                            "% Mix": st.column_config.NumberColumn(format="%.1f%%")})

# ---- CSV export (formula-injection safe, mode-aware) ----
export = table.copy()
for col in ("title", "sku", "asin", "sku_status"):
    if col in export.columns:
        export[col] = export[col].map(csv_safe)
mode_label = "primeday_" if mode == "prime_day" else ""
st.download_button("⬇️ Export CSV", export.to_csv(index=False).encode("utf-8"),
                    file_name=f"{brand}_inventory_{mode_label}{date.today()}.csv", mime="text/csv")

# ---- SKU notes ----
st.divider()
st.subheader("📝 SKU notes")
notes = store.get_notes(brand)
n1, n2 = st.columns([1, 2])
note_sku = n1.selectbox("SKU", table["sku"].tolist(), key="note_sku")
existing = notes[notes["sku"] == note_sku]
current_note = existing.iloc[0]["note"] if not existing.empty else ""
new_note = n2.text_area("Note (empty = delete)", value=current_note, key=f"note_{note_sku}")
if st.button("Save note"):
    store.save_note(brand, note_sku, new_note, updated_by="team")
    st.toast("Note saved.")
    st.rerun()
if not notes.empty:
    st.dataframe(notes[["sku", "note", "updated_by", "updated_at"]], use_container_width=True, hide_index=True)
