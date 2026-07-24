# FBA Inventory Sync — Python (Streamlit) Edition

Python conversion of the WordPress FBA Inventory Sync plugin — same job —
per-client Amazon FBA inventory dashboards fed by Google Sheets — in a
fraction of the code, hosted for free.

**See `CHANGELOG.md`** for what changed each round, and — importantly —
which files need to be updated together when you deploy an update.

## What's included

**Core dashboard — one consolidated, editable grid**
- Every SKU's data lives on its own row: a `🔎` checkbox that opens the SKU details popup, health/aged alert flags, a color-coded DOI flag (🟢/🟡/🔴) next to a combined DOI bar+number, a plain Trend flag with clearly-labeled `vs 30d %` / `vs 90d %` columns, title/SKU/ASIN, fulfillable, the five inbound breakdown columns (`Inbound: Working/Shipped/Receiving/FC Transfer/FC Processing`), fulfillable+inbound, `Sales: 7d/30d/60d/90d Units`, a 7-day-DOI projection, daily average, Order-By date, status, a plain-English recommended action, order units/cases, % sales mix, and the sheet's sync date. Related columns share a name prefix (`Inbound: `, `Sales: `) since Streamlit's table has no grouped/multi-row header feature to actually merge them visually
- **Mock Units and Note are directly editable in the grid**, and **Future DOI recalculates live** right next to Mock Units on the same row. Click "💾 Save note changes" to persist edited notes (batched, not saved on every keystroke)
- **A separate Aged Inventory table** at the bottom of the page with the full 91-180/181-270/271-365/365+/AIS breakdown, only for SKUs that have any
- **"⚙️ Customize grid layout"** — set your preferred column order and width once; it persists across sessions (Streamlit can't capture a live column drag-resize/reorder, so this is the explicit alternative)
- **Check the `🔎` box on any row** to open a "SKU Deep-Dive" popup (a real modal, via `st.dialog`) with DOI history and daily-average-sold charts side by side, that SKU's dimensions and case packs, a form to add a new case pack, and a "log a shipment" quick action
- Multi-client selector, hourly-cached sync + Sync Now button, per-client Target DOI / Lead Time settings
- CSV export — with the formula-injection fix from the security review built in

**Portfolio & reporting**
- **Overview page** — every client at a glance (in-stock rate, OOS count, overdue orders, aged SKUs, shipments in transit), with one-click jump into any client's dashboard
- **Health Report** — stranded + unfulfillable inventory, synced from a Google Drive folder per client (same as the original; see setup below) — flagged inline on the dashboard's Alerts column too
- DOI history is tracked automatically (one snapshot per SKU per day) and shows up in the row-click detail panel on the Dashboard — no separate page to visit

**Planning tools**
- **Prime Day mode** — per-client event settings (multiplier, dates, cutoff, lead times, recovery days, per-SKU overrides) with a dedicated projection view and CSV export mode
- **Shipments page** — shipment log with FBA shipment ID, tracking number (auto-detects UPS/FedEx/USPS/Amazon/DHL and links to the carrier's tracking page), and status; SKU case-pack and dimension management with a **"🔄 Sync Dimensions"** button that pulls product weight/dimensions and a "Master Carton" case pack automatically from a "SKU Sheet" tab (never touches custom case packs you add by hand); a shipment-file builder that lets you **choose which case pack size to use per SKU** when more than one exists, and generates a downloadable `.xlsx`

**Digest**
- **Digest Preview page** — rebuilds the exact branded HTML email from the WordPress version (order deadlines, replenishment, trending, aged inventory, shipments, full DOI snapshot) for every client combined
- Download as `.html`, or send immediately via the "Send now" button once SMTP is configured (see below)

**FF Pro Sync & QA** (ported from a separate WordPress plugin, "FBA Listing Monitor" — standalone pages, not tied to the client list)
- **FF Pro Sync** — upload one or more copy-template `.docx` files (source of truth, ASIN in parens at the end of titles/bullets) plus your Amazon listing export `.xlsx`. Cross-checks title, bullet points 1–5, description, and generic keywords per ASIN, with word-level diff highlighting (red strikethrough = missing from the upload, yellow = extra/wrong) and a similarity % bar on anything that doesn't match. Filter by pass/fail/missing, hide parent SKUs, search, and export a CSV of everything or just the differences.
- **QA** — upload the same kind of file and it fetches each ASIN's *live* Amazon listing and compares title/brand/bullets against it, with the same diff highlighting, filtering, and export.
- Both keep a run history (persisted the same way as everything else, in the App Data sheet) so past comparisons can be reloaded later.
- Honest limitation: live Amazon scraping is inherently fragile — Amazon can block, CAPTCHA, or change its page markup at any time. This is ported faithfully from what the original WordPress plugin did, not a more bulletproof approach.

## How it stores data (no database needed)

Everything the app needs to remember lives in one Google Sheet you create,
called **"FBA App Data."** The app auto-creates its tabs in it on first run
(clients, settings, notes, shipments, DOI history, health reports, case
packs, dimensions, Prime Day config, client profiles, digest recipients).
Open that spreadsheet anytime to see the data directly.

- Client inventory sheets (Ordering Template): service account needs **Viewer** — unchanged from today
- The one App Data sheet: service account needs **Editor**
- Health report Drive folders (see below): service account needs **Viewer**

---

## One-time setup

### Step 1 — GitHub
Same as before: create a private repo (`fba-inventory-app`), upload every
file in this folder, commit.

### Step 2 — The App Data sheet
Same as before: blank Google Sheet named "FBA App Data," shared with your
service account email as **Editor**, grab its ID from the URL.

### Step 3 — Streamlit Community Cloud
Same as before: sign in with GitHub, create the app pointing at
`streamlit_app.py`, then go to **Settings → Secrets** and paste in your
filled-out `secrets.toml.example` (password, sheet ID, service account
JSON). Restrict sharing to your team's emails.

### Step 4 — Health report folders (new, optional)
For each client that gets stranded/unfulfillable reports, share that
client's Google Drive report folder with your service account email as
**Viewer**, then go to the **Clients** page in the app and paste the
folder ID (from the folder's URL) into "Health report folder."

### Step 5 — Email digest (new, optional)
- **Just want to see/download it:** nothing to configure — open **Digest
  Preview**, it builds automatically.
- **Want to actually send it:** add a `[smtp]` section to your Secrets
  (a free Gmail account + an "App Password" works well — see the comments
  in `secrets.toml.example`), add recipient emails on the Digest Preview
  page, and use "Send now."
- **Want it to send itself automatically every Mon/Wed/Fri, with nobody
  opening the app:** this needs one more small piece I haven't built yet —
  Streamlit's free tier only runs code while the app is open, so true
  hands-off scheduling needs a separate free scheduled job (a GitHub
  Action is the natural fit) that calls the same digest-building code on
  a cron schedule. I designed `digest.py` so that piece can plug in
  cleanly, but wiring and testing it is a good next step to tackle
  together rather than something to guess at blind — let me know if
  you'd like to do that next.

---

## Making changes (same workflow as before)

1. Ask Claude for the change you want
2. Paste the updated file into GitHub → **pencil icon** → **Commit changes**
3. The live app updates itself within about a minute
4. If something breaks: GitHub → **History** on that file → restore the
   previous version

**Tip for bigger changes:** this version has more files than the first
pass (`calc.py` holds all the DOI/aging/trending/Prime-Day math in one
place, `digest.py` builds the email, `health.py` handles the Drive-based
health report, `doi_history.py` handles snapshots, `store.py` is the data
layer). For anything that touches numbers or business rules, paste
`calc.py` alongside your request — that's where the formulas live.

---

## Running it on your own computer (optional)

```
pip install -r requirements.txt
mkdir .streamlit
copy secrets.toml.example .streamlit/secrets.toml   (then fill it in)
streamlit run streamlit_app.py
```

---

## What I tested vs. what still needs a real run-through

I tested the core formulas (DOI math, Prime Day projections, aging,
trending, carrier detection) against synthetic data and confirmed they
match the original plugin's math exactly, and I rendered the email digest
to confirm it displays correctly. I have not been able to click through
every page against your real Google Sheets and Drive folders — that first
real run is the best way to catch anything sheet-layout-specific (a
renamed column, a differently-named health report tab, etc.), and any
error message you see there tells us exactly what to fix.

## Notes & limits (free tier)

- The app **sleeps** after ~12 hours with no visitors; the first visit of
  the day takes ~30–60 seconds to wake.
- Inventory and health data are cached (1 hour and until next Sync,
  respectively); use **Sync now** / the sync buttons on each page to force
  a refresh.
- DOI history writes append-only (cheap); most other data is small enough
  that full-tab rewrites on save are fine. If SKU counts grow very large
  across many clients, the App Data sheet is the first place to watch for
  slowness — a real database would be the natural next step at that point.
- Never commit `secrets.toml` or any credential file to GitHub — the
  included `.gitignore` blocks the common mistakes.
