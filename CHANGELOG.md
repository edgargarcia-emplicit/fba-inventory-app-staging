# Changelog

This file tracks what changed and why — especially fixes — so it's clear
what to check when redeploying an update.

## ⚠️ Files that must be updated together

Some files depend on exact column/data names in another file. If only one
of a pair gets updated in your GitHub repo, the app can throw a `KeyError`
or similar crash. When Claude gives you an update, **always replace every
file mentioned**, not just the one that seems most relevant.

| If you're changing... | Also check... | Why |
|---|---|---|
| `calc.py` | `streamlit_app.py`, `digest.py` | Both read specific column names calc.py produces (e.g. `trend`, `action`, `current_doi`). If calc.py stops producing a column the others expect, it crashes. |
| `store.py` (the `TABS` dict) | `streamlit_app.py`, `doi_history.py`, `health.py` | These all read/write specific tabs and columns in the App Data sheet. |
| `sheets.py` (`HEADER_MAP`) | `calc.py` | calc.py's formulas assume certain raw columns exist on the inventory data. |
| `docx_parser.py`, `xlsx_export_parser.py`, `amazon_scraper.py`, `diffing.py` | `streamlit_app.py` | The FF Pro Sync / QA pages call these directly and expect specific dict keys back (`title`, `bullet_1`..`bullet_5`, `skus`, etc.). |

As of this version, the app **no longer crashes outright** if columns go
missing (see 2026-07 fix below) — it shows a warning naming exactly what's
missing instead. If you ever see that warning, it's this exact situation:
re-paste every file from the same delivery, not just one.

## 2026-07 — Round 9: FF Pro Sync + QA modules (ported from FBA Listing Monitor)

Two new, standalone modules ported from a separate WordPress plugin
("FBA Listing Monitor") — not part of the FBA Inventory Sync client flow,
these are their own top-level pages since the original was its own
separate plugin, not tied to a client roster.

- **FF Pro Sync** — upload one or more copy-template `.docx` files (source
  of truth) plus your Amazon listing export `.xlsx`, and it cross-checks
  title, bullets 1-5, description, and generic keywords per ASIN. New
  files needed vs. the original: `docx_parser.py` (DOCX parsing — parent/
  child titles, shared vs. per-ASIN "Unique Bullet Point N for X" bullets,
  description, keywords) and `xlsx_export_parser.py` (handles the new
  745-column Amazon bulk-export format, with the old simpler format still
  supported as a fallback). Tested directly against the real copy-doc and
  spreadsheet files provided — correctly parsed 4-25 ASINs per DOCX file
  and 196 ASINs from the real export, and correctly surfaced a genuine
  title/bullet divergence between the two sources.
- **QA** — upload the same kind of file (xlsx or docx) and it fetches each
  ASIN's *live* Amazon listing (`amazon.com/dp/{ASIN}`) and compares
  title/brand/bullets against it. `amazon_scraper.py` ports the original's
  regex-based HTML parsing faithfully, including its rotating user-agents,
  CAPTCHA/bot-block detection, and the specific out-of-stock false-positive
  fix the original plugin had already made (checking only the first clause
  of the availability text, not the whole string, so "other sellers may be
  unavailable" doesn't wrongly flag an in-stock item as OOS).
- **Diff highlighting** — `diffing.py` ports the exact word-level
  highlighting from the original (red strikethrough for words missing from
  the target, yellow highlight for words not in the source, plus a
  similarity % bar), rendered as a real HTML table via
  `st.components.v1.html` rather than Streamlit's native table — this
  sidesteps the "can't color a cell based on its value" limitation
  entirely, since this table is genuinely read-only with no need for
  inline editing.
- **Run history** for both, persisted the same way as everything else (new
  `crossref_runs`/`crossref_results` and `qa_runs`/`qa_results` tabs in
  the App Data sheet).
- Found and fixed one real bug while porting the DOCX parser: reading all
  paragraphs first and all tables second (rather than in true document
  order) broke the description/keyword section-boundary detection, since
  those sections' actual content lives inside tables interleaved between
  paragraph headers. Fixed by walking the document body in true reading
  order.
- Honest constraint carried over from the original: live Amazon scraping
  is inherently fragile (Amazon can block, CAPTCHA, or change its markup
  at any time) — this is ported faithfully from what the WordPress plugin
  already did, not a more robust approach.

## 2026-07 — Round 8: grouped header strip

- **Added a grouped header row above the grid**, since the naming-prefix
  approach from Round 7 wasn't the same thing as a real visual grouping.
  Streamlit's table still has no true merged/spanning header cell (checked
  its source again to be sure) — so this builds one out of `st.columns()`,
  sized by how many grid columns each group spans (Item, Inventory,
  Inbound, Sales, DOI, Trend, Replenishment, What-If, Status). Since every
  data column shares the same width preset, count-based proportions line
  up reasonably well against the real columns below, though it isn't
  pixel-perfect (no awareness of horizontal scroll position, and very
  narrow/wide browser windows can drift it slightly). Confirmed it
  recomputes correctly if you reorder columns via "Customize grid layout."

## 2026-07 — Round 7: units back to plain columns, DOI bar+number combined, inbound columns, checkbox popup trigger

- **Reverted** the Units Trend sparkline — back to plain separate `Sales:
  7d/30d/60d/90d Units` columns, per feedback that the chart wasn't the
  right call after all.
- **DOI column now combines the bar and the number** — one `ProgressColumn`
  showing the bar first, then the DOI value as text ("60d"), instead of
  a separate `DOI %` column. The color-coded `Flag` column (🟢/🟡/🔴) is
  back as its own column next to it — genuine constraint, confirmed in
  Streamlit's source: `ProgressColumn`'s color is one fixed color for the
  whole column, so it can't turn red/yellow/green per row on its own;
  the Flag is what actually carries that signal.
- **Added the missing replenishment-sheet columns:** `Inbound: Working`,
  `Inbound: Shipped`, `Inbound: Receiving`, `Inbound: FC Transfer`,
  `Inbound: FC Processing` — these were already being read from the sheet
  and summed into Fulf.+Inbound, just never shown individually.
- **Grouped headers:** checked directly in Streamlit's dataframe/data_editor
  source — there's no multi-row/grouped column header feature anywhere in
  the API. Used a shared name prefix instead (`Inbound: ...`, `Sales: ...`)
  and kept those columns adjacent, with a caption explaining why, rather
  than presenting a fake solution.
- **SKU deep-dive popup trigger:** added a `🔎` checkbox column at the far
  left of the grid — check it and the popup opens for that row, then the
  checkbox resets on its own (behaves like a momentary button rather than
  a toggle you have to uncheck). This replaces the selectbox + button.
  Still not a literal click on the SKU/ASIN cell itself — confirmed
  data_editor has no click/selection event of any kind (unlike the
  read-only `st.dataframe`), so a dedicated trigger column is the closest
  real equivalent available.

## 2026-07 — Round 6: trend clarity, DOI display, aged table, richer popup

- **Fixed trend confusion:** removed the unlabeled percentage that had been
  folded into the Trend flag text (e.g. "🔥 Hot +45%" — unclear whether
  that was vs 30d or vs 90d). The flag is plain again; `vs 30d %` and
  `vs 90d %` are separate, clearly-labeled columns right next to it.
  Checked the actual installed Streamlit library first: there's no
  multi-line/wrapped-text support for table cells at all (no such option
  exists anywhere in its column config code), so true "stack two labeled
  lines in one cell" like the WordPress version isn't achievable — clear
  separate columns are the real fix for the ambiguity, not a stacking
  trick that wouldn't render as expected anyway.
- **Added — Units Trend column:** replaces the four separate 7d/30d/60d/90d
  unit columns with one mini bar-chart cell (`BarChartColumn`) showing the
  daily sell-through rate across all four windows at a glance — chosen
  over fake text-stacking specifically because a real chart conveys the
  trend direction better than four raw totals would have anyway.
- **Added — 7-day DOI:** the "7d Avg" column is now a DOI projection (what
  DOI would be if the last 7 days' sell-through rate continued) instead of
  a raw units-per-day number, per request.
- **Fixed DOI display:** the percent-of-target now shows as visible text
  directly in the DOI column ("🟢 60d (133%)"), alongside the progress bar
  in its own column. Real constraint, checked directly in Streamlit's
  source: `ProgressColumn`'s `color` is one fixed color for the *entire*
  column, not conditional per row — there's no way to make individual bars
  red/yellow/green based on their own value. The DOI column's 🟢/🟡/🔴 flag
  right next to the bar is the actual color-coded signal.
- **Moved aged inventory** out of the main grid into its own table at the
  bottom of the page (full 91-180/181-270/271-365/365+/AIS breakdown, only
  for SKUs that have any). The main grid keeps a single "Aged" alert
  column (⚠️ or blank) instead of four detail columns.
- **Expanded the SKU deep-dive popup:** DOI history and daily-average-sold
  charts now show side by side; added the SKU's dimensions and case pack
  list, plus a form to add a new case pack, right there in the popup.
  Still triggered by selectbox + button, not a row click — confirmed this
  is a hard limit (Streamlit's editable grid has no click/selection event
  at all, unlike its read-only table), not a preference.

## 2026-07 — Round 5: Beta removed, trend %/DOI display fixed, layout persistence, popup, dimension sync

- **Removed** "Inventory Report (Beta)" entirely — a genuine platform limit
  (Streamlit's editable grid can't render colored badge pills per cell)
  meant it could never actually deliver what was asked for, so rather than
  keep iterating on something structurally impossible, it's gone. The
  `render_dashboard_module()` function stays (it's the only place the
  Dashboard's logic lives) but is simpler now with the dead theming code
  removed.
- **Fixed trend visibility:** the actual percentage is now embedded
  directly in the Trend text itself (e.g. "🔥 Hot +45%"), not just in the
  separate `vs 30d %` / `vs 90d %` columns — impossible to miss regardless
  of column order.
- **Fixed DOI display:** Flag and DOI number are now one combined column
  ("🟢 60d") since Streamlit's editable grid can't color a cell's text
  based on its value — merging them was the closest real equivalent.
- **Added:** a "DOI %" progress-bar column (DOI as a percent of target),
  restoring the percentage-bar look from the WordPress version.
- **Added:** a "⚙️ Customize grid layout" settings panel — column order
  and width (Compact/Comfortable/Wide) now persist across sessions.
  Honest caveat: Streamlit has no way to capture a column you drag-resize
  or drag-reorder directly in the table itself — that part of the browser
  interaction isn't exposed to the code at all. This settings panel is the
  practical alternative: set it once, it's remembered from then on.
- **Changed:** SKU deep-dive is now a real popup (`st.dialog`) instead of
  a section opening below the table.
- **Added — Sync Dimensions:** pulls product weight/dimensions and a
  "Master Carton" case pack automatically from a "SKU Sheet" tab in the
  client's spreadsheet (ported faithfully from the original PHP, including
  its trickiest bit: the sheet has "Longest/Median/Shortest side" columns
  twice — first occurrence is the product, second is the case pack —
  confirmed against synthetic data matching that exact layout). Never
  touches custom case packs you've added by hand — only ever
  creates/updates the one named exactly "Master Carton".
- **Fixed:** the shipment builder previously always used a SKU's *first*
  case pack silently. It now shows a dropdown when a SKU has more than one
  case pack size, so you can pick which one to use per shipment.

## 2026-07 — Round 4: Beta page corrected to be a themed clone, not a static summary

- **Fixed misunderstanding:** "Inventory Report (Beta)" had been built as a
  read-only rendering of the email digest's HTML — not what was asked for.
  It's now a **fully functional duplicate of the Dashboard** (same editable
  Mock Units, live Future DOI, editable Notes, everything), restyled with
  the Emplicit digest's branding (dark header banner, accent-red buttons,
  card-style KPI metrics).
- **Architecture:** both pages now call one shared function,
  `render_dashboard_module(theme=...)`, instead of duplicating the logic —
  exactly the kind of duplication that caused bugs in the original
  WordPress plugin (two copies of the Google auth flow, etc.). Editing a
  note or Mock Units value on one page shows up on the other immediately,
  since it's the same underlying state, not a separate copy.
- **Known platform limit:** Streamlit's editable table widget can't render
  colored badge pills inside individual cells the way the HTML digest
  table can — that part still looks like a native table either way.
  Flag/Alerts/Trend/Status still show as emoji/text. Everything *around*
  the grid (header, KPI cards, buttons) is what changes between themes.

## 2026-07 — Round 3: inline editing, trend %, alternate report view

- **Fixed:** Dashboard crashed with a redacted `KeyError` on
  `table[list(display_cols)]`. Root cause: `calc.py` had been updated
  (adding `trend`/`action`/`d7_avg` columns) but the deployed `streamlit_app.py`
  and `calc.py` had briefly fallen out of sync across two separate paste
  operations. Fixed by adding a `safe_view()` helper in `streamlit_app.py`
  that never crashes on a missing column — it shows what's available plus
  a plain-language warning telling you which files are likely out of sync.
- **Added:** Trend column now shows the actual 7-day-vs-30-day and
  7-day-vs-90-day percentage changes (`vs 30d %`, `vs 90d %`), matching the
  original digest email's trending table instead of just an emoji flag.
- **Added:** The main grid is now genuinely editable (`st.data_editor`)
  instead of read-only:
  - **Mock Units** — type a hypothetical incoming quantity directly on a
    SKU's row
  - **Future DOI** — recalculates automatically from Mock Units, right
    next to it, same row
  - **Note** — editable directly in the grid; click "💾 Save note changes"
    to persist (batched, not saved on every keystroke, to avoid hammering
    the Google Sheets API)
  - Everything else on the grid stays read-only/computed, same as before.
- **Changed:** Since `st.data_editor` doesn't support click-a-row like
  `st.dataframe` did, DOI history + the "log a shipment" quick action moved
  from a row-click panel to a small selectbox-driven "SKU deep-dive"
  section right below the grid. Functionally the same access, just
  triggered by a dropdown instead of a click.
- **Added:** New "Inventory Report (Beta)" page — the same data as the
  Dashboard, rendered in the branded card/table style from the email
  digest, for side-by-side comparison with the main grid view. Read-only.

## 2026-07 — Round 2: bug fixes after first real deployment

- **Fixed:** `KeyError` on zero active SKUs — `calc.compute_derived()` skipped
  adding its output columns entirely when given 0 rows, so the display code
  crashed looking for columns that were never created. Fixed by always
  returning the full expected column set regardless of row count.
- **Fixed:** `StreamlitAPIException` when clicking "Open [client] →" on the
  Overview page — tried to set `session_state["nav_page"]` after that
  widget had already rendered this run, which Streamlit forbids. Fixed by
  moving the state change into an `on_click=` callback (the supported
  pattern for this).
- **Fixed:** Duplicate blank column names (`""` used twice) crashed the
  main table's pyarrow conversion. Gave the flag and alerts columns
  distinct labels.

## Initial conversion

WordPress → Streamlit port covering the full plugin feature set: dashboard,
Prime Day mode, health report, shipments (case packs, dimensions, carrier
detection), DOI history, portfolio overview, and the email digest builder.
See README.md for the full feature list.
