"""
digest.py — Builds the Emplicit-branded HTML inventory digest.

Python port of class-fba-email-digest.php. The original sends this via
WP-Cron every Mon/Wed/Fri at 8am PST. Streamlit's free tier has no
background scheduler (the app only runs while someone has it open), so:

  - This module always builds the exact same HTML content, shown on the
    "Digest Preview" page and downloadable as a .html file.
  - If SMTP secrets are configured (see secrets.toml.example), a "Send now"
    button can email it immediately.
  - For real Mon/Wed/Fri automated sends, pair this with a free scheduled
    GitHub Action (cron) that calls send_digest_email() — see README.
"""

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import calc

COLOR_BG, COLOR_CARD = "#f4f5f7", "#ffffff"
COLOR_HEADER_BG, COLOR_ACCENT = "#0d1117", "#e8404a"
COLOR_TEXT, COLOR_MUTED = "#1a1a2e", "#6b7280"
COLOR_RED, COLOR_AMBER, COLOR_GREEN, COLOR_BLUE = "#dc2626", "#d97706", "#16a34a", "#2563eb"
COLOR_BORDER = "#e5e7eb"


def _stat_cell(label, value, sub, color=None):
    c = color or COLOR_TEXT
    return (f'<td width="25%" style="padding:16px 20px;border-right:1px solid {COLOR_BORDER};vertical-align:top">'
            f'<div style="font-size:10px;color:{COLOR_MUTED};text-transform:uppercase;letter-spacing:.06em;'
            f'font-family:sans-serif">{label}</div>'
            f'<div style="font-size:22px;font-weight:700;color:{c};font-family:sans-serif;margin-top:3px">{value}</div>'
            f'<div style="font-size:11px;color:{COLOR_MUTED};font-family:sans-serif;margin-top:2px">{sub}</div></td>')


def _section_header(title):
    return (f'<div style="background:#f9fafb;border-top:1px solid {COLOR_BORDER};'
            f'border-bottom:1px solid {COLOR_BORDER};padding:10px 20px;margin:0">'
            f'<span style="font-size:12px;font-weight:700;color:{COLOR_TEXT};font-family:sans-serif;'
            f'text-transform:uppercase;letter-spacing:.05em">{title}</span></div>')


def _table_header(cols):
    cells = "".join(f'<td style="padding:8px 16px;font-size:10px;font-weight:700;color:{COLOR_MUTED};'
                     f'text-transform:uppercase;letter-spacing:.05em;font-family:sans-serif;'
                     f'border-bottom:1px solid {COLOR_BORDER};background:#f9fafb">{c}</td>' for c in cols)
    return f"<tr>{cells}</tr>"


def _table_row(cells, bg="#ffffff"):
    tds = "".join(f'<td style="padding:10px 16px;font-size:12px;color:{COLOR_TEXT};font-family:sans-serif;'
                   f'border-bottom:1px solid {COLOR_BORDER};vertical-align:middle">{c}</td>' for c in cells)
    return f'<tr style="background:{bg}">{tds}</tr>'


def _sku_cell(title, sku):
    return (f'<span style="font-weight:600;font-size:12px">{title}</span><br>'
            f'<span style="font-size:10px;color:{COLOR_MUTED};font-family:monospace">{sku}</span>')


def _badge(text, color):
    bg = {COLOR_RED: "#fef2f2", COLOR_AMBER: "#fffbeb", COLOR_GREEN: "#f0fdf4", COLOR_BLUE: "#eff6ff"}.get(color, "#f3f4f6")
    return (f'<span style="display:inline-block;background:{bg};color:{color};font-size:10px;font-weight:700;'
            f'padding:2px 8px;border-radius:10px;font-family:sans-serif">{text}</span>')


def build_client_section(client_row, inv: pd.DataFrame, target_doi: int, lead_time: int, shipments: pd.DataFrame) -> str:
    active = calc.active_only(inv)
    derived = calc.compute_derived(active, target_doi, lead_time)
    summary = calc.client_summary(inv, target_doi)
    aged = calc.aged_summary(derived)
    trending = calc.trending_skus(active)

    overdue = derived[derived["days_until_order"] < 0]
    urgent = derived[(derived["days_until_order"] >= 0) & (derived["days_until_order"] <= 7)]
    repl = derived[derived["replenish"]]
    active_ships = shipments[shipments["status"].isin(["pending", "shipped"])] if not shipments.empty else shipments

    in_stock_color = (COLOR_GREEN if summary["weighted_rate"] >= 90
                       else COLOR_AMBER if summary["weighted_rate"] >= 70 else COLOR_RED)

    html = f'''
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px">
      <tr><td style="background:{COLOR_HEADER_BG};border-radius:8px 8px 0 0;padding:16px 24px">
        <table width="100%" cellpadding="0" cellspacing="0"><tr>
          <td style="color:#fff;font-size:18px;font-weight:700;font-family:sans-serif">{client_row['client_name']}</td>
          <td align="right" style="color:#9ca3af;font-size:12px;font-family:sans-serif">
            {client_row['brand_code']} · {client_row['marketplace']}</td>
        </tr></table>
      </td></tr>
      <tr><td style="background:#fff;border:1px solid {COLOR_BORDER};border-top:none;padding:0">
        <table width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid {COLOR_BORDER}"><tr>
          {_stat_cell("In-stock rate", f"{summary['in_stock_rate']}%", f"Weighted: {summary['weighted_rate']}%", in_stock_color)}
          {_stat_cell("Active SKUs", summary['active_skus'], f"{summary['needs_reorder']} need reorder")}
          {_stat_cell("Fulfillable", f"{summary['total_fulfillable']:,}", f"+ {summary['total_inbound']:,} inbound")}
          {_stat_cell("Daily velocity", summary['daily_total'], "units/day")}
        </tr></table>'''

    if not overdue.empty or not urgent.empty:
        html += _section_header("🚨 Order Deadlines Requiring Action")
        html += '<table width="100%" cellpadding="0" cellspacing="0">' + _table_header(["SKU", "Current DOI", "Status", "Action"])
        for _, row in overdue.iterrows():
            action = f"Order {row['order_units_calc']:,.0f} units ({row['order_cases_calc']:.0f} cases)" if row['order_units_calc'] else "Replenish"
            html += _table_row([_sku_cell(row['title'], row['sku']), f"{row['current_doi']}d",
                                 _badge(f"OVERDUE {abs(row['days_until_order'])}d", COLOR_RED), action], "#fef2f2")
        for _, row in urgent.iterrows():
            action = f"Order {row['order_units_calc']:,.0f} units ({row['order_cases_calc']:.0f} cases)" if row['order_units_calc'] else "Replenish"
            html += _table_row([_sku_cell(row['title'], row['sku']), f"{row['current_doi']}d",
                                 _badge(f"URGENT — {row['days_until_order']}d left", COLOR_AMBER), action], "#fffbeb")
        html += "</table>"

    if not repl.empty:
        html += _section_header("📦 Replenishment Suggestions")
        html += '<table width="100%" cellpadding="0" cellspacing="0">' + _table_header(
            ["SKU", "Fulfillable", "Daily avg", "Current DOI", "Suggested order"])
        for _, row in repl.iterrows():
            label = f"{row['order_units_calc']:,.0f} units ({row['order_cases_calc']:.0f} cases)" if row['order_units_calc'] else "See dashboard"
            html += _table_row([_sku_cell(row['title'], row['sku']), f"{row['fulfillable']:,}",
                                 f"{row['daily_avg']:.2f}", f"{row['current_doi']}d", f"<strong>{label}</strong>"])
        html += "</table>"

    if not trending.empty:
        html += _section_header("📈 Trending SKUs")
        html += '<table width="100%" cellpadding="0" cellspacing="0">' + _table_header(
            ["SKU", "Trend", "7d daily", "30d daily", "vs 30d", "vs 90d"])
        for _, row in trending.iterrows():
            vs30 = f"+{row['vs30_pct']}%" if row['vs30_pct'] >= 0 else f"{row['vs30_pct']}%"
            vs90 = f"+{row['vs90_pct']}%" if row['vs90_pct'] >= 0 else f"{row['vs90_pct']}%"
            html += _table_row([_sku_cell(row['title'], row['sku']), row['flag'], row['d7'], row['d30'],
                                 f'<span style="color:{COLOR_GREEN};font-weight:600">{vs30}</span>',
                                 f'<span style="color:{COLOR_GREEN};font-weight:600">{vs90}</span>'])
        html += "</table>"

    if not aged.empty:
        html += _section_header("⚠️ Aged Inventory Alerts")
        html += '<table width="100%" cellpadding="0" cellspacing="0">' + _table_header(
            ["SKU", "91-180d", "181-270d", "271d+", "AIS units", "Action"])
        for _, row in aged.iterrows():
            action = _badge("Review/Remove", COLOR_RED) if row['total_aged'] > 0 else _badge("Monitor", COLOR_AMBER)
            bg = "#fef2f2" if row['total_aged'] > 0 else "#fffbeb"
            html += _table_row([
                _sku_cell(row['title'], row['sku']),
                f'<span style="color:{COLOR_AMBER};font-weight:600">{row["inv_age_91_180"]:,}</span>' if row['inv_age_91_180'] > 0 else "—",
                f'<span style="color:{COLOR_RED};font-weight:600">{row["inv_age_181_270"]:,}</span>' if row['inv_age_181_270'] > 0 else "—",
                f'<span style="color:#7f1d1d;font-weight:600">{row["inv_age_271_365"] + row["inv_age_365_plus"]:,}</span>' if (row['inv_age_271_365'] + row['inv_age_365_plus']) > 0 else "—",
                f'<span style="color:{COLOR_RED};font-weight:600">{row["ais_qty_total"]:,} units</span>' if row['ais_qty_total'] > 0 else "0",
                action,
            ], bg)
        html += "</table>"

    if not active_ships.empty:
        html += _section_header("🚚 Shipments In Transit")
        html += '<table width="100%" cellpadding="0" cellspacing="0">' + _table_header(
            ["SKU", "Units", "Date ordered", "Status"])
        for _, s in active_ships.iterrows():
            color = COLOR_BLUE if s['status'] == "shipped" else COLOR_MUTED
            html += _table_row([_sku_cell(s['title'] or s['sku'], s['sku']), f"{int(s['units']):,}",
                                 s['date_ordered'] or "—",
                                 f'<span style="color:{color};font-weight:600;text-transform:capitalize">{s["status"]}</span>'])
        html += "</table>"

    html += _section_header("📊 DOI Snapshot — All Active SKUs")
    html += '<table width="100%" cellpadding="0" cellspacing="0">' + _table_header(
        ["SKU", "Fulfillable", "Inbound", "Daily avg", "Current DOI", "Status"])
    for _, row in derived.iterrows():
        doi_color = COLOR_GREEN if row['current_doi'] >= 60 else COLOR_AMBER if row['current_doi'] >= 30 else COLOR_RED
        repl_label = _badge("Reorder", COLOR_AMBER) if row['replenish'] else f'<span style="color:{COLOR_GREEN}">✓ OK</span>'
        html += _table_row([_sku_cell(row['title'], row['sku']), f"{row['fulfillable']:,}",
                             f"{row['inbound_total']:,}" if row['inbound_total'] > 0 else "—",
                             f"{row['daily_avg']:.2f}",
                             f'<span style="color:{doi_color};font-weight:700">{row["current_doi"]}d</span>',
                             repl_label])
    html += "</table></td></tr></table>"
    return html


def wrap_email(date_str, time_str, content) -> str:
    return f'''<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Emplicit FBA Digest</title></head>
<body style="margin:0;padding:0;background:{COLOR_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{COLOR_BG};padding:32px 16px"><tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%">
  <tr><td style="background:{COLOR_HEADER_BG};border-radius:12px 12px 0 0;padding:28px 32px">
    <table width="100%" cellpadding="0" cellspacing="0"><tr><td>
      <div style="color:{COLOR_ACCENT};font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase">FBA Inventory Digest</div>
      <div style="color:#fff;font-size:22px;font-weight:700;margin-top:4px">{date_str}</div>
      <div style="color:#9ca3af;font-size:12px;margin-top:4px">Generated at {time_str}</div>
    </td></tr></table>
  </td></tr>
  <tr><td style="padding:24px 0">{content}</td></tr>
  <tr><td style="background:{COLOR_HEADER_BG};border-radius:0 0 12px 12px;padding:20px 32px">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="color:#6b7280;font-size:11px;font-family:sans-serif">
        Sent by <strong style="color:#9ca3af">Emplicit FBA Inventory Sync</strong> · Mon / Wed / Fri at 8 AM PST</td>
      <td align="right" style="color:#6b7280;font-size:11px;font-family:sans-serif">© {datetime.now().year} Emplicit</td>
    </tr></table>
  </td></tr>
</table></td></tr></table></body></html>'''


def build_digest(clients_with_data: list) -> str:
    """clients_with_data: list of (client_row, inv_df, shipments_df, target_doi, lead_time)."""
    tz = ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)
    sections = "".join(
        build_client_section(c, inv, target_doi, lead_time, ships)
        for c, inv, ships, target_doi, lead_time in clients_with_data
    )
    return wrap_email(now.strftime("%A, %B %d, %Y"), now.strftime("%-I:%M %p PST"), sections)


def send_email_now(html: str, subject: str, recipients: list[str]) -> tuple[bool, str]:
    """Send via SMTP using secrets under [smtp]. Returns (success, message)."""
    if "smtp" not in st.secrets:
        return False, "SMTP isn't configured yet — add a [smtp] section to Secrets first (see README)."
    cfg = st.secrets["smtp"]
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg["from_address"]
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587))) as server:
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["from_address"], recipients, msg.as_string())
        return True, f"Sent to {len(recipients)} recipient(s)."
    except Exception as e:  # noqa: BLE001
        return False, f"Send failed: {e}"
