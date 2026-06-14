# Chewy Dashboard Auto-Updater
# ==============================
# Watches the Downloads folder for new Chewy Ads CSV reports.
# When one lands, it processes the data and pushes an updated dashboard to GitHub.
#
# Run once: setup_watcher.bat (registers as a Windows startup task)
# Log file: scripts/watcher.log

import os
import sys
import time
import shutil
import logging
import subprocess
import csv
import re
import json
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Campaign ID → canonical short name ────────────────────────────────────
# Campaign IDs never change on rename. Add new entries here whenever a
# campaign is renamed or a new one is created.
CAMPAIGN_ID_MAP = {
    "658335191": "Meow Manor",
    "658381530": "GD",
    "658582346": "FRD",
    "658582354": "MMXL",
    "658582356": "FD",
}

# Fuzzy keyword fallback — maps any campaign name/ID to a stable short key
def canonical_campaign(name: str, cid: str = "") -> str:
    """Return a stable short key regardless of how the campaign was renamed."""
    if cid and cid in CAMPAIGN_ID_MAP:
        return CAMPAIGN_ID_MAP[cid]
    n = name.lower()
    if "frd" in n or "fairy" in n:
        return "FRD"
    if "mmxl" in n or "meow manor xl" in n or "extra large" in n:
        return "MMXL"
    if "meow manor" in n:
        return "Meow Manor"
    if n.startswith("fd") or "french door" in n:
        return "FD"
    if n.startswith("gd") or "gnome door" in n:
        return "GD"
    return name  # unknown — use raw name

# ── Live campaign display names (persisted across runs) ────────────────────
# Maps canonical key -> current raw name as seen in the latest report.
# Updated whenever a report contains a Campaign ID we recognise.
DISPLAY_NAMES_FILE  = Path(r"C:\Users\retai\OneDrive\Desktop\Claude Code\scripts\campaign_display_names.json")
DAILY_HISTORY_FILE  = Path(r"C:\Users\retai\OneDrive\Desktop\Claude Code\scripts\daily_history.json")

def load_display_names() -> dict:
    try:
        return json.loads(DISPLAY_NAMES_FILE.read_text()) if DISPLAY_NAMES_FILE.exists() else {}
    except Exception:
        return {}

def save_display_names(d: dict):
    DISPLAY_NAMES_FILE.write_text(json.dumps(d, indent=2))

def update_display_names(rows: list) -> bool:
    """Record the latest raw name for each recognised campaign. Returns True if anything changed."""
    current = load_display_names()
    changed = False
    for r in rows:
        cid  = r.get("cid", "")
        raw  = r.get("raw_name", "") or r.get("name", "")
        key  = CAMPAIGN_ID_MAP.get(cid, "")
        if key and raw and current.get(key) != raw:
            current[key] = raw
            changed = True
    if changed:
        save_display_names(current)
    return changed

def patch_campaign_display_names(html: str) -> str:
    """If a campaign has been renamed in Chewy (raw name no longer starts with the canonical key),
    replace the canonical key label in the dashboard with the new display name (truncated to 30 chars)."""
    names = load_display_names()
    for key, raw in names.items():
        # Only patch if the raw name doesn't start with the canonical key
        # (i.e. the campaign was actually renamed to something different)
        if raw and not raw.upper().startswith(key.upper()):
            display = raw[:30].rstrip() + ("…" if len(raw) > 30 else "")
            html = re.sub(
                r'(?<=>)' + re.escape(key) + r'(?=\s*[<(—·])',
                display, html
            )
            log.info("Patched campaign label: '%s' -> '%s'", key, display)
    return html

# ── Paths ──────────────────────────────────────────────────────────────────
DOWNLOADS_DIR   = Path(r"C:\Users\retai\Downloads")
DASHBOARD_DIR   = Path(r"C:\Users\retai\OneDrive\Desktop\Claude Code")
DASHBOARD_HTML  = DASHBOARD_DIR / "index.html"
REPORTS_ARCHIVE = DASHBOARD_DIR / "scripts" / "reports_archive"
LOG_FILE        = DASHBOARD_DIR / "scripts" / "watcher.log"
STATE_FILE      = DASHBOARD_DIR / "scripts" / "processed_files.json"

REPORTS_ARCHIVE.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("chewy_watcher")

# ── Chewy report filename patterns ────────────────────────────────────────
# Matches both the Chewy Ads portal export names AND the dated report names
CHEWY_FILENAME_PATTERNS = [
    # Portal exports (Chewy Ads - Campaigns.csv etc.)
    re.compile(r"(?i)chewy\s*ads?\s*[-–]"),
    # Dated report exports (Promoted_Products_2026-05-05_to_2026-06-03.csv etc.)
    re.compile(r"(?i)^promoted_products_\d{4}-\d{2}-\d{2}"),
    re.compile(r"(?i)^purchased_products_\d{4}-\d{2}-\d{2}"),
    re.compile(r"(?i)^ca?maign_performance.*\d{4}-\d{2}-\d{2}"),   # typo "Camaign" tolerated
    re.compile(r"(?i)^campaign_performance.*\d{4}-\d{2}-\d{2}"),
    re.compile(r"(?i)^keyword.*position.*\d{4}-\d{2}-\d{2}"),
    re.compile(r"(?i)^keyword.*\d{4}-\d{2}-\d{2}"),
]

def _peek_is_purrfect(path: Path) -> bool:
    """Check if the file's first data row has 'purrfect-portal' as advertiser."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            row = next(reader, {})
        return row.get("Advertiser", "").strip().lower() == "purrfect-portal"
    except Exception:
        return False

def is_chewy_report(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    name = path.name
    # First check by filename
    if any(p.search(name) for p in CHEWY_FILENAME_PATTERNS):
        return True
    # Fallback: peek at content (catches any new report types Chewy adds)
    return _peek_is_purrfect(path)

def detect_report_type(path: Path) -> str:
    """Return 'campaigns', 'keywords', 'products', 'purchased', 'campaign_weekly',
    'campaign_daily', or 'unknown'."""
    name = path.name.lower()
    # Dated export names
    if name.startswith("promoted_products_"):
        return "products"
    if name.startswith("purchased_products_"):
        return "purchased"
    if "keyword" in name:
        return "keywords"
    if "campaign" in name or "camaign" in name:
        if "week" in name:
            return "campaign_weekly"
        if "day" in name:
            return "campaign_daily"
        return "campaigns"
    # Portal export names / fallback via header
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            header_lower = " ".join(h.lower() for h in headers)
        if "keyword" in header_lower:
            return "keywords"
        if "purchased product" in header_lower:
            return "purchased"
        if "week start date" in header_lower:
            return "campaign_weekly"
        if "promoted product" in header_lower:
            return "products"
        if "campaign" in header_lower:
            return "campaigns"
    except Exception:
        pass
    return "unknown"

# ── State tracking (skip already-processed files) ─────────────────────────
def load_processed():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
    return set()

def save_processed(processed: set):
    STATE_FILE.write_text(json.dumps(sorted(processed), indent=2))

# ── CSV Parsers ────────────────────────────────────────────────────────────
def clean_num(s):
    """'$1,234.56' or '1,234.56%' -> float"""
    if not s or s.strip() in ("", "--", "N/A", "0%"):
        return 0.0
    return float(re.sub(r"[^0-9.\-]", "", s) or "0")

def _norm_roas(v):
    """Chewy ROAS is sometimes a % string ('1025.9' = 10.26x) and sometimes a multiplier ('10.26').
    If > 20, treat as percentage points and divide by 100. Otherwise use as-is."""
    return round(v / 100, 2) if v > 20 else round(v, 2)

def _norm_pct(v):
    """CTR/CVR in new Chewy exports are raw decimals (0.0194 = 1.94%). Normalise to %."""
    return round(v * 100, 2) if v < 1.0 else round(v, 2)

def parse_campaigns(path: Path) -> dict:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            # Support both old ('Campaign name'/'Status') and new ('Campaign'/'Current Status') column names
            raw_name = (row.get("Campaign") or row.get("Campaign name") or "").strip()
            cid      = (row.get("Campaign ID") or "").strip()
            name     = canonical_campaign(raw_name, cid)
            status = (row.get("Current Status") or row.get("Status") or "").strip()
            budget = clean_num(row.get("Current Budget") or row.get("Budget") or "0")
            sales  = clean_num(row.get("Direct Sales") or row.get("Direct sales") or "0")
            orders = int(clean_num(row.get("Total Orders") or row.get("Total orders") or "0"))
            ntb    = int(clean_num(row.get("NTB Customers") or row.get("New to brand") or "0"))
            pos    = clean_num(row.get("Avg Position") or row.get("Avg position") or "0")
            ctr_raw = clean_num(row.get("CTR") or "0")
            roas_raw = clean_num(row.get("Direct ROAS") or "0")
            if not name:
                continue
            rows.append({
                "name":     name,
                "raw_name": raw_name,
                "cid":      cid,
                "status":   status,
                "budget":   budget,
                "spend":    clean_num(row.get("Spend") or "0"),
                "impr":     int(clean_num(row.get("Impressions") or "0")),
                "clicks":   int(clean_num(row.get("Clicks") or "0")),
                "roas":     _norm_roas(roas_raw),
                "sales":    sales,
                "orders":   orders,
                "ntb":      ntb,
                "ctr":      _norm_pct(ctr_raw),
                "position": pos,
                "cpc":      clean_num(row.get("CPC") or "0"),
            })
    return {"type": "campaigns", "rows": rows, "source": path.name}

def parse_keywords(path: Path) -> dict:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_camp = (row.get("Campaign") or row.get("Campaign name") or "").strip()
            cid      = (row.get("Campaign ID") or "").strip()
            campaign = canonical_campaign(raw_camp, cid)
            keyword  = (row.get("Keyword") or row.get("Search term") or "").strip()
            sales    = clean_num(row.get("Direct Sales") or row.get("Direct sales") or "0")
            orders   = int(clean_num(row.get("Total Orders") or row.get("Total orders") or "0"))
            pos      = clean_num(row.get("Avg Position") or row.get("Avg position") or "0")
            ctr_raw  = clean_num(row.get("CTR") or "0")
            cvr_raw  = clean_num(row.get("CVR") or row.get("Conversion rate") or "0")
            roas_raw = clean_num(row.get("Direct ROAS") or "0")
            # Total Boost: 0 = non-boosted, 0.1 = 10%, 0.21 = 21%, etc.
            boost_raw = clean_num(row.get("Total Boost") or "0")
            rows.append({
                "campaign": campaign,
                "keyword":  keyword,
                "match":    (row.get("Match type") or "").strip(),
                "spend":    clean_num(row.get("Spend") or "0"),
                "impr":     int(clean_num(row.get("Impressions") or "0")),
                "clicks":   int(clean_num(row.get("Clicks") or "0")),
                "roas":     _norm_roas(roas_raw),
                "sales":    sales,
                "orders":   orders,
                "ctr":      _norm_pct(ctr_raw),
                "cvr":      _norm_pct(cvr_raw),
                "position": pos,
                "cpc":      clean_num(row.get("CPC") or "0"),
                "boost":    round(boost_raw * 100) if boost_raw < 1.0 else round(boost_raw),
            })
    return {"type": "keywords", "rows": rows, "source": path.name}

def parse_products(path: Path) -> dict:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append({
                "campaign": row.get("Campaign name", "").strip(),
                "product":  row.get("Product name", row.get("Product", "")).strip(),
                "sku":      row.get("SKU", row.get("Item number", "")).strip(),
                "spend":    clean_num(row.get("Spend", "0")),
                "impr":     int(clean_num(row.get("Impressions", "0"))),
                "clicks":   int(clean_num(row.get("Clicks", "0"))),
                "roas":     clean_num(row.get("Direct ROAS", "0")),
                "sales":    clean_num(row.get("Direct sales", "0")),
                "orders":   int(clean_num(row.get("Total orders", "0"))),
                "ntb":      int(clean_num(row.get("New to brand", "0"))),
                "ctr":      clean_num(row.get("CTR", "0")),
                "cvr":      clean_num(row.get("CVR", row.get("Conversion rate", "0"))),
                "position": clean_num(row.get("Avg position", "0")),
                "cpc":      clean_num(row.get("CPC", "0")),
            })
    return {"type": "products", "rows": rows, "source": path.name}

def parse_campaign_weekly(path: Path) -> dict:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_camp = row.get("Campaign", "").strip()
            cid      = row.get("Campaign ID", "").strip()
            rows.append({
                "week":     row.get("Week Start Date", "").strip(),
                "campaign": canonical_campaign(raw_camp, cid),
                "status":   row.get("Current Status", "").strip(),
                "spend":    clean_num(row.get("Spend", "0")),
                "impr":     int(clean_num(row.get("Impressions", "0"))),
                "clicks":   int(clean_num(row.get("Clicks", "0"))),
                "roas":     clean_num(row.get("Direct ROAS", "0")),
                "sales":    clean_num(row.get("Direct Sales", "0")),
                "orders":   int(clean_num(row.get("Total Orders", "0"))),
                "ntb":      int(clean_num(row.get("NTB Customers", "0"))),
                "ctr":      clean_num(row.get("CTR", "0")),
                "position": clean_num(row.get("Avg Position", "0")),
                "cpc":      clean_num(row.get("CPC", "0")),
            })
    return {"type": "campaign_weekly", "rows": rows, "source": path.name}

def parse_campaign_daily(path: Path) -> dict:
    """Parse a Group_by_Day CSV into per-day totals (spend, sales, ROAS)."""
    from collections import defaultdict
    by_date = defaultdict(lambda: {"spend": 0.0, "sales": 0.0, "units": 0})
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            date_str = (row.get("Date") or "").strip()
            if not date_str or date_str.upper() == "NULL":
                continue
            spend = clean_num(row.get("Spend") or "0")
            sales = clean_num(row.get("Direct Sales") or "0")
            units = int(clean_num(row.get("Direct Units") or "0"))
            by_date[date_str]["spend"] += spend
            by_date[date_str]["sales"] += sales
            by_date[date_str]["units"] += units
    rows = []
    for date_str, v in sorted(by_date.items()):
        sp = round(v["spend"], 2)
        sa = round(v["sales"], 2)
        r  = round(sa / sp, 2) if sp else 0
        rows.append({"date": date_str, "spend": sp, "sales": sa, "roas": r, "units": v["units"]})
    return {"type": "campaign_daily", "rows": rows, "source": path.name}

def parse_purchased(path: Path) -> dict:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_camp = row.get("Campaign", "").strip()
            cid      = row.get("Campaign ID", "").strip()
            rows.append({
                "campaign":         canonical_campaign(raw_camp, cid),
                "promoted_sku":     row.get("Promoted Product", "").strip(),
                "promoted_name":    row.get("Promoted Product Name", "").strip(),
                "purchased_sku":    row.get("Purchased Product", "").strip(),
                "purchased_name":   row.get("Purchased Product Name", "").strip(),
                "purchased_cat":    row.get("Purchased Category", "").strip(),
                "spend":            clean_num(row.get("Spend", "0")),
                "direct_sales":     clean_num(row.get("Direct Sales", "0")),
                "direct_units":     int(clean_num(row.get("Direct Units", "0"))),
                "unique_products":  int(clean_num(row.get("Unique Products Sold", "0"))),
                "roas":             clean_num(row.get("Direct ROAS", "0")),
                "cvr":              clean_num(row.get("CVR", "0")),
                "clicks":           int(clean_num(row.get("Clicks", "0"))),
            })
    return {"type": "purchased", "rows": rows, "source": path.name}

PARSERS = {
    "campaigns":       parse_campaigns,
    "keywords":        parse_keywords,
    "products":        parse_products,
    "campaign_weekly": parse_campaign_weekly,
    "campaign_daily":  parse_campaign_daily,
    "purchased":       parse_purchased,
}


# ── Dashboard HTML updater ─────────────────────────────────────────────────
def update_dashboard(data: dict):
    """Patch index.html with the latest report data and update the header date."""
    html = DASHBOARD_HTML.read_text(encoding="utf-8")
    rtype = data["type"]
    today = datetime.now().strftime("%b %-d, %Y") if sys.platform != "win32" else datetime.now().strftime("%b %#d, %Y")

    if rtype == "campaigns":
        html = _update_campaigns(html, data)
    elif rtype == "campaign_weekly":
        html = _update_campaigns(html, _aggregate_weekly(data))
    elif rtype == "campaign_daily":
        html = _update_daily_chart(html, data)
    elif rtype == "keywords":
        html = _update_keywords(html, data)
    elif rtype == "products":
        html = _update_products(html, data)
    elif rtype == "purchased":
        html = _update_purchased(html, data)

    # Always patch campaign display names with latest names from reports
    html = patch_campaign_display_names(html)

    # Always bump the "Last updated" badge
    html = re.sub(
        r'(Last updated: )[^<"]+',
        r"\g<1>" + today,
        html,
    )

    DASHBOARD_HTML.write_text(html, encoding="utf-8")
    log.info("Dashboard HTML updated (%s report, %d rows)", rtype, len(data["rows"]))

def _roas_str(r):
    """ROAS is already normalised to a multiplier by the parsers — just round it."""
    return round(r, 2)

def _collapse_canon(name: str) -> str:
    """Collapse any sub-campaign name to one of the 5 display canonicals."""
    n = name.lower()
    if "mmxl" in n:
        return "MMXL"
    if "frd" in n or "fairy" in n:
        return "FRD"
    if n.startswith("fd") or "french" in n:
        return "FD"
    if n.startswith("gd") or "gnome" in n:
        return "GD"
    if "meow manor" in n or n.startswith("mm ") or n.startswith("mm-") or n.startswith("mmw"):
        return "Meow Manor"
    return name


def _update_campaigns(html, data):
    rows = data["rows"]
    # Record latest display names for any row that carries a Campaign ID
    if update_display_names(rows):
        log.info("Campaign display names updated: %s", load_display_names())
    active = [r for r in rows if r["status"].upper() == "ACTIVE"]
    if not active:
        log.warning("No active campaigns found in report — skipping campaign KPI update")
        return html

    total_spend  = sum(r["spend"]  for r in active)
    total_sales  = sum(r["sales"]  for r in active)
    total_orders = sum(r["orders"] for r in active)
    total_ntb    = sum(r["ntb"]    for r in active)
    total_impr   = sum(r["impr"]   for r in active)
    total_clicks = sum(r["clicks"] for r in active)
    avg_position = (sum(r["position"] * r["clicks"] for r in active) / max(total_clicks, 1))
    overall_roas = round(total_sales / total_spend, 2) if total_spend else 0
    overall_ctr  = round(total_clicks / total_impr * 100, 2) if total_impr else 0
    overall_cpc  = round(total_spend / total_clicks, 2) if total_clicks else 0

    log.info("Campaigns: spend=$%.2f  sales=$%.2f  ROAS=%.2fx  position=%.1f",
             total_spend, total_sales, overall_roas, avg_position)

    # Patch KPI values — scoped to the Ad Management block ONLY. These labels
    # (ROAS, Avg Position) also appear in the Overview/P&L tabs, so a global
    # count=1 replace would corrupt those and miss the intended block. We operate
    # only on the substring between the CAMPAIGN-KPI sentinels.
    replacements = [
        (r'(<div class="label">Spend</div><div class="value[^"]*">)\$[\d,.]+',
         r'\g<1>$' + f"{total_spend:.2f}"),
        (r'(<div class="label">Direct Sales</div><div class="value[^"]*">)\$[\d,.]+',
         r'\g<1>$' + f"{total_sales:,.2f}"),
        (r'(<div class="label">ROAS</div><div class="value[^"]*">)[\d.]+x',
         r'\g<1>' + f"{overall_roas}x"),
        (r'(<div class="label">Avg CPC</div><div class="value[^"]*">)\$[\d.]+',
         r'\g<1>$' + f"{overall_cpc:.2f}"),
        (r'(<div class="label">Avg Position</div><div class="value[^"]*">)[\d.]+',
         r'\g<1>' + f"{avg_position:.2f}"),
        (r'(<div class="label">New to Brand</div><div class="value[^"]*">)\d+\s*/\s*\d+',
         r'\g<1>' + f"{total_ntb} / {total_orders}"),
    ]
    km = re.search(r'<!-- CAMPAIGN-KPI-START -->.*?<!-- CAMPAIGN-KPI-END -->', html, re.DOTALL)
    if km:
        block = km.group(0)
        for pattern, repl in replacements:
            block = re.sub(pattern, repl, block, count=1)
        html = html[:km.start()] + block + html[km.end():]
    else:
        log.warning("CAMPAIGN-KPI sentinels not found — skipping KPI patch")

    # ── Update campaign subtitle date range from filename ─────────────────────
    src = data.get("source", "")
    m = re.search(r'(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})', src)
    if m:
        def _fmt(d):
            from datetime import datetime
            return datetime.strptime(d, "%Y-%m-%d").strftime("%b %-d, %Y") if hasattr(datetime, 'strptime') else d
        try:
            from datetime import datetime
            def _fmt_day(d, include_year=False):
                dt = datetime.strptime(d, "%Y-%m-%d")
                s = dt.strftime("%b %d, %Y") if include_year else dt.strftime("%b %d")
                return s.replace(" 0", " ")  # strip leading zero cross-platform
            d1 = _fmt_day(m.group(1))
            d2 = _fmt_day(m.group(2), include_year=True)
        except ValueError:
            d1, d2 = m.group(1), m.group(2)
        # Collapse sub-campaigns to display canonicals, dedupe, keep canonical order
        order = ["MMXL", "Meow Manor", "FRD", "GD", "FD"]
        active_canon = {_collapse_canon(r["name"]) for r in active}
        paused_canon = {_collapse_canon(r["name"]) for r in rows} - active_canon
        active_names = " + ".join(n for n in order if n in active_canon)
        paused_list  = [n for n in order if n in paused_canon]
        paused_str   = " · " + ", ".join(paused_list) + " paused" if paused_list else ""
        subtitle = f"Active Campaigns — {active_names} ({d1} – {d2}){paused_str}"
        html = re.sub(
            r'<!-- CAMPAIGN-SUBTITLE-START -->.*?<!-- CAMPAIGN-SUBTITLE-END -->',
            f'<!-- CAMPAIGN-SUBTITLE-START --><div class="sec">{subtitle}</div><!-- CAMPAIGN-SUBTITLE-END -->',
            html, flags=re.DOTALL
        )

    # ── Rebuild Campaign Status table ─────────────────────────────────────────
    html = _update_campaign_status_table(html, rows)

    # ── Rebuild Weekly Budget Decision panel (live status/budget cells) ───────
    report_date_iso = m.group(2) if m else None
    html = _update_budget_panel(html, rows, report_date_iso)
    return html


# Static notes for paused campaigns — updated manually when strategy changes
_PAUSED_NOTES = {
    "FRD": ("Manually paused — low Fairy White/Blue inventory · split White/Blue before reactivating",
            '<span class="pill pill-red">⛔ Wait for restock</span>'),
    "GD":  ("Inventory unknown for 1381262",
            '<span class="pill pill-yellow">Verify stock first</span>'),
    "FD":  ("All 3 French Door SKUs OOS",
            '<span class="pill pill-red">Activate after restock</span>'),
}

# Canonical order for the table
_CAMPAIGN_ORDER = ["MMXL", "Meow Manor", "FRD", "GD", "FD"]


def _update_campaign_status_table(html: str, rows: list) -> str:
    """Rebuild the <!-- CAMPAIGN-STATUS-START/END --> tbody from live campaign data."""
    from datetime import datetime as _dt

    # Aggregate all sub-campaigns per canonical name
    by_name: dict = {}
    for r in rows:
        n = r["name"]
        if n not in by_name:
            by_name[n] = {"spend": 0.0, "sales": 0.0, "budget": 0.0,
                          "clicks": 0, "position_sum": 0.0, "status": r["status"]}
        by_name[n]["spend"]        += r["spend"]
        by_name[n]["sales"]        += r["sales"]
        by_name[n]["budget"]       += r["budget"]
        by_name[n]["clicks"]       += r["clicks"]
        by_name[n]["position_sum"] += r["position"] * r["clicks"]
        # Mark active if ANY sub-campaign is active
        if r["status"].upper() == "ACTIVE":
            by_name[n]["status"] = "ACTIVE"
    # Compute derived fields
    for n, v in by_name.items():
        v["roas"]     = round(v["sales"] / v["spend"], 2) if v["spend"] else 0.0
        v["position"] = round(v["position_sum"] / v["clicks"], 1) if v["clicks"] else 0.0

    def _next_action(r):
        roas = r["roas"]
        if roas >= 8:
            return '<span class="pill pill-green">Scale budget</span>'
        if roas >= 5:
            return '<span class="pill pill-green">Raise boost on top keywords</span>'
        if roas >= 3:
            return '<span class="pill pill-yellow">Monitor</span>'
        if r["sales"] == 0:
            return '<span class="pill pill-yellow">Give it 2–3 more weeks</span>'
        return '<span class="pill pill-red">Review keywords — low ROAS</span>'

    tbody_rows = []
    for name in _CAMPAIGN_ORDER:
        r = by_name.get(name)
        is_active = r and r["status"].upper() == "ACTIVE"

        if is_active:
            roas_str = f"{r['roas']}x" if r["roas"] else "0x"
            pos_str  = f"pos {r['position']:.1f} · " if r["position"] else ""
            budget   = f"${r['budget']:.0f}" if r["budget"] else "—"
            notes    = f"{budget} budget · {roas_str} ROAS · {pos_str}30d data"
            action   = _next_action(r)
            bg       = "background:rgba(34,197,94,.06);"
            status   = '<span class="pill pill-green">✅ Active</span>'
        else:
            static   = _PAUSED_NOTES.get(name, ("—", '<span class="pill pill-yellow">Check status</span>'))
            roas_part = f" · was {r['roas']}x ROAS" if r and r["roas"] else ""
            notes    = static[0] + roas_part
            action   = static[1]
            bg       = "background:rgba(245,158,11,.04);" if name != "FD" else ""
            status   = '<span class="pill pill-red">⛔ Paused</span>' if name == "FD" else '<span class="pill pill-yellow">⏸ Paused</span>'

        tbody_rows.append(
            f'        <tr style="{bg}"><td style="font-weight:600;">{name}</td>'
            f'<td>{status}</td><td>{notes}</td><td>{action}</td></tr>'
        )

    month_label = _dt.now().strftime("%b %Y")
    tbody_html  = "\n".join(tbody_rows)
    block       = f"\n      <tbody>\n{tbody_html}\n      </tbody>\n      "

    placeholder = "___CS_PLACEHOLDER___"
    new_html = re.sub(
        r'<!-- CAMPAIGN-STATUS-START -->.*?<!-- CAMPAIGN-STATUS-END -->',
        '<!-- CAMPAIGN-STATUS-START -->' + placeholder + '<!-- CAMPAIGN-STATUS-END -->',
        html, flags=re.DOTALL, count=1
    )
    if placeholder in new_html:
        html = new_html.replace(placeholder, block, 1)
        # Update the month in the heading
        html = re.sub(r'Campaign Status — \w+ \d{4}',
                      f'Campaign Status — {month_label}', html, count=1)
        log.info("Campaign status table updated (%d campaigns)", len(tbody_rows))
    else:
        log.warning("CAMPAIGN-STATUS-START/END sentinels not found")
    return html


# ── Weekly Budget Decision panel ────────────────────────────────────────────
# Live cells (Status, Current Budget, header total, freshness date) come from the
# daily report. The other columns — products, inventory gate, recommended budget,
# priority action — are curated strategy and live here in config. The Inventory
# Gate column stays manual until the inventory/CPFR feed lands (task t1).
# Each row: (label, canon, products, budget_mode, inv_pill, rec_text, action_pill)
#   budget_mode: "live" → show canonical current budget; any other string is shown literally.
_BUDGET_PANEL = [
    ("FRD — Fairy White", "FRD", "SKU 1381150 · $34.95", "live",
     ("pill-red", "⛔ Low inventory"), "$250/mo when restocked",
     ("pill-red", "Wait for restock → split → reactivate")),
    ("FRD — Fairy Blue", "FRD", "SKU 1381166 · $29.95", "Shared w/ White",
     ("pill-red", "⛔ Low inventory"), "$50/mo when restocked",
     ("pill-yellow", 'Split campaign, pause "cat door" kw when reactivating')),
    ("MMXL — Meow Manor XL", "MMXL", "XL White 167 OH · XL Brown 102 OH", "live",
     ("pill-green", "✓ Healthy (167 + 102 OH)"), "Scale with ROAS",
     ("pill-green", "✅ Live — monitor ROAS")),
    ("GD — Gnome Door", "GD", "SKU 1381262 (Blue/Green active · Black discontinued)", "live",
     ("pill-yellow", "⚠️ Inventory unknown"), "$50/mo (test)",
     ("pill-yellow", "Verify stock first, then test")),
    ("MM — White — general bucket", "Meow Manor", "Lg variants — Black has shortfall", "live",
     ("pill-red", "⚠️ Black: 19 OH shortfall"), "Watch closely",
     ("pill-yellow", "Monitor; restock Black asap")),
    ("FD — French Door", "FD", "SKUs 1381182, 1381198, 1381190", "live",
     ("pill-red", "⛔ All 3 SKUs OOS"), "$0 — hold",
     ("pill-red", "Activate only after restock")),
]


def _canon_status_budget(rows: list) -> dict:
    """Aggregate live status + current budget per canonical campaign name."""
    agg: dict = {}
    for r in rows:
        n = r["name"]
        a = agg.setdefault(n, {"budget": 0.0, "active": False})
        a["budget"] += r["budget"]
        if r["status"].upper() == "ACTIVE":
            a["active"] = True
    return agg


def _update_budget_panel(html: str, rows: list, report_date_iso: str | None) -> str:
    """Rebuild the <!-- BUDGET-PANEL-START/END --> tbody + header total + freshness date."""
    from datetime import datetime as _dt
    agg = _canon_status_budget(rows)

    tbody = []
    for label, canon, products, budget_mode, inv, rec, action in _BUDGET_PANEL:
        a = agg.get(canon, {"budget": 0.0, "active": False})
        active = a["active"]
        if budget_mode == "live":
            budget_disp = f"${a['budget']:.0f}/mo" + ("" if active else " (paused)")
            budget_cell = budget_disp
        else:
            budget_cell = f'<span style="color:var(--muted);">{budget_mode}</span>'

        if active:
            status_pill = '<span class="pill pill-green">✅ Active</span>'
            row_style = "background:rgba(34,197,94,.06);"
        else:
            status_pill = '<span class="pill pill-yellow">⏸ Paused</span>'
            row_style = "opacity:0.6;"

        tbody.append(
            f'        <tr style="{row_style}">'
            f'<td style="font-weight:600;">{label}</td>'
            f'<td style="font-size:11px;color:var(--muted);">{products}</td>'
            f'<td>{budget_cell}</td>'
            f'<td>{status_pill}</td>'
            f'<td><span class="pill {inv[0]}">{inv[1]}</span></td>'
            f'<td style="font-weight:700;color:var(--muted);">{rec}</td>'
            f'<td><span class="pill {action[0]}">{action[1]}</span></td></tr>'
        )

    block = "\n      <tbody>\n" + "\n".join(tbody) + "\n      </tbody>\n      "
    placeholder = "___BP_PLACEHOLDER___"
    new_html = re.sub(
        r'<!-- BUDGET-PANEL-START -->.*?<!-- BUDGET-PANEL-END -->',
        '<!-- BUDGET-PANEL-START -->' + placeholder + '<!-- BUDGET-PANEL-END -->',
        html, flags=re.DOTALL, count=1
    )
    if placeholder not in new_html:
        log.warning("BUDGET-PANEL-START/END sentinels not found")
        return html
    html = new_html.replace(placeholder, block, 1)

    # Header: current active monthly budget (sum of active canonical budgets)
    active_total = sum(a["budget"] for a in agg.values() if a["active"])
    html = re.sub(r'(<span id="budget-active-total">)\$[\d,]+(</span>)',
                  rf'\g<1>${active_total:,.0f}\g<2>', html, count=1)

    # Freshness stamp: update data-updated + the trailing "updated <date>" text
    if report_date_iso:
        try:
            d = _dt.strptime(report_date_iso, "%Y-%m-%d")
            disp = d.strftime("%b %d, %Y").replace(" 0", " ")
        except ValueError:
            disp = report_date_iso
        html = re.sub(r'(id="budget-stamp"[^>]*data-updated=")[\d-]+(")',
                      rf'\g<1>{report_date_iso}\g<2>', html, count=1)
        html = re.sub(r'(id="budget-stamp".*?updated )[A-Za-z]{3} \d{1,2}, \d{4}',
                      rf'\g<1>{disp}', html, count=1, flags=re.DOTALL)

    log.info("Budget panel updated (active monthly budget $%.0f)", active_total)
    return html


def _update_keywords(html, data):
    """Rebuild the full keyword table, action summary bar, callout panel, and date label."""
    rows = sorted(data["rows"], key=lambda r: r["spend"], reverse=True)
    if not rows:
        return html

    # ── Negate classification ──────────────────────────────────────────────
    NEGATE_TOKENS = ("screen", "dog", " wall", "exterior", "gate", "patio", "sliding",
                     "outdoor", "window screen", "small pet")

    def _is_negate(kw: str) -> bool:
        kl = kw.lower()
        return any(tok in kl for tok in NEGATE_TOKENS)

    # ── Per-row action classification ──────────────────────────────────────
    def _classify(r) -> str:
        """Return one of: 'negate', 'raise', 'keep_scale', 'keep', 'monitor'"""
        kl = r["keyword"].lower().strip('"\'')
        if _is_negate(r["keyword"]):
            return "negate"
        roas = round(r["roas"], 2)
        pos  = r["position"]
        spend = r["spend"]
        # Not enough data → monitor
        if spend < 2.0 and r["clicks"] < 5:
            return "monitor"
        if roas >= 2.42:
            if pos > 8:         # good ROAS but buried on pg 2
                return "raise"
            if roas >= 10:
                return "keep_scale"
            return "keep"
        return "monitor"

    def _action_cell(cls: str, kw: str, r) -> str:
        pos = r["position"]
        roas = round(r["roas"], 2)
        spend = r["spend"]
        if cls == "negate":
            return '<span class="pill pill-red">⛔ Negate</span>'
        if cls == "raise":
            return f'<span class="pill pill-green">↑ Raise boost (pos {pos:.1f})</span>'
        if cls == "keep_scale":
            return '<span class="pill pill-green">✓ Keep + Scale</span>'
        if cls == "keep":
            return '<span class="pill pill-green">✓ Keep</span>'
        if spend < 2.0 and r["clicks"] < 5:
            return '<span class="pill pill-yellow">Monitor (low data)</span>'
        return '<span class="pill pill-yellow">Monitor</span>'

    # ── Row bg colour ──────────────────────────────────────────────────────
    def _row_bg(cls: str) -> str:
        if cls == "negate":
            return "background:rgba(239,68,68,.05);"
        if cls in ("keep_scale", "raise", "keep"):
            return "background:rgba(34,197,94,.04);"
        return "background:rgba(245,158,11,.03);"

    # ── Pill helpers ───────────────────────────────────────────────────────
    def kw_pill(kw: str, cls: str) -> str:
        kw = kw.strip('"').strip("'")
        if not kw or kw.lower() in ("non-boosted", "", "--"):
            return '<span class="pill pill-purple">Non-Boosted</span>'
        pill_cls = "pill-red" if cls == "negate" else "pill-blue"
        return f'<span class="pill {pill_cls}">{kw[:24]}</span>'

    def roas_cell(r_val: float) -> str:
        r_val = round(r_val, 2)
        if r_val == 0:
            return '—'
        cls = "pill-green" if r_val >= 8 else "pill-yellow" if r_val >= 2.42 else "pill-red"
        return f'<span class="pill {cls}">{r_val}x</span>'

    def pos_cell(pos: float) -> str:
        if pos == 0:
            return '<td>—</td>'
        colour = 'var(--green)' if pos <= 8 else 'var(--yellow)'
        return f'<td style="color:{colour};">{pos:.1f}</td>'

    def boost_cell(boost: int) -> str:
        if boost == 0:
            return '—'
        return f'{boost}%'

    # ── Build tbody ────────────────────────────────────────────────────────
    classified = [(r, _classify(r)) for r in rows]

    def _build_row(r, cls):
        cvr_str = f'{r["cvr"]:.1f}%' if r["cvr"] else '—'
        return (
            f'<tr style="{_row_bg(cls)}">'
            f'<td>{kw_pill(r["keyword"], cls)}</td>'
            f'<td style="color:var(--muted);font-size:11px;">{r["campaign"][:16]}</td>'
            f'<td>{boost_cell(r.get("boost", 0))}</td>'
            + pos_cell(r["position"]) +
            f'<td>${r["spend"]:.2f}</td>'
            f'<td>{r["impr"]:,}</td>'
            f'<td>{r["ctr"]:.2f}%</td>'
            f'<td>{roas_cell(r["roas"])}</td>'
            f'<td>{cvr_str}</td>'
            f'<td>{_action_cell(cls, r["keyword"], r)}</td>'
            '</tr>'
        )

    tbody_rows = "\n".join(_build_row(r, cls) for r, cls in classified)

    # ── Action counts for summary bar ──────────────────────────────────────
    n_raise   = sum(1 for _, c in classified if c == "raise")
    n_negate  = sum(1 for _, c in classified if c == "negate")
    n_monitor = sum(1 for _, c in classified if c == "monitor")

    # ── Collect callout lists ──────────────────────────────────────────────
    raise_items = [
        f'{r["campaign"]} "{r["keyword"].strip(chr(34)).strip(chr(39))}": {r.get("boost",0)}% → ?? (pos {r["position"]:.1f}, {round(r["roas"],2)}x ROAS)'
        for r, c in classified if c == "raise"
    ]
    negate_items = [
        f'"{r["keyword"].strip(chr(34)).strip(chr(39))}"'
        for r, c in classified if c == "negate"
    ]
    monitor_items = [
        f'{r["campaign"]} "{r["keyword"].strip(chr(34)).strip(chr(39))}" ({round(r["roas"],2)}x, {r["clicks"]} clicks)'
        for r, c in classified if c == "monitor"
    ]

    raise_html  = "<br>".join(raise_items)  if raise_items  else "None this period"
    negate_html = " · ".join(negate_items)   if negate_items  else "None this period"
    monitor_html = "<br>".join(monitor_items[:6]) if monitor_items else "None"

    # ── Extract date range from source filename ────────────────────────────
    src = data.get("source", "")
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})', src)
    if date_match:
        def _fmt(d):
            from datetime import datetime as _dt
            try:
                return _dt.strptime(d, "%Y-%m-%d").strftime("%b %-d, %Y") if sys.platform != "win32" else _dt.strptime(d, "%Y-%m-%d").strftime("%b %#d, %Y")
            except Exception:
                return d
        kw_date = f"{_fmt(date_match.group(1))} – {_fmt(date_match.group(2))}"
    else:
        kw_date = datetime.now().strftime("%b %#d, %Y") if sys.platform == "win32" else datetime.now().strftime("%b %-d, %Y")

    # ── Patch action summary bar (id="kw-action-summary") ─────────────────
    summary_html = (
        f'<span style="background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);border-radius:6px;padding:3px 10px;font-size:11px;color:var(--green);font-weight:600;">↑ Raise boost: {n_raise}</span>'
        f'<span style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:6px;padding:3px 10px;font-size:11px;color:#f87171;font-weight:600;">⛔ Negate: {n_negate}</span>'
        f'<span style="background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:6px;padding:3px 10px;font-size:11px;color:var(--yellow);font-weight:600;">👁 Monitor: {n_monitor}</span>'
    )

    # ── Callout panel HTML ─────────────────────────────────────────────────
    callout_html = (
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:12px;">'
        '<div style="background:rgba(34,197,94,.05);border:1px solid rgba(34,197,94,.2);border-radius:8px;padding:10px 14px;font-size:12px;">'
        '<div style="font-weight:700;color:var(--green);margin-bottom:6px;">↑ Raise Boost</div>'
        f'<div style="color:var(--text);line-height:1.6;">{raise_html}</div>'
        '</div>'
        '<div style="background:rgba(239,68,68,.05);border:1px solid rgba(239,68,68,.2);border-radius:8px;padding:10px 14px;font-size:12px;">'
        '<div style="font-weight:700;color:#f87171;margin-bottom:6px;">⛔ Add as Negative Keywords</div>'
        f'<div style="color:var(--text);line-height:1.6;">{negate_html}</div>'
        '</div>'
        '<div style="background:rgba(245,158,11,.05);border:1px solid rgba(245,158,11,.2);border-radius:8px;padding:10px 14px;font-size:12px;">'
        '<div style="font-weight:700;color:var(--yellow);margin-bottom:6px;">👁 Watch Next Period</div>'
        f'<div style="color:var(--text);line-height:1.6;">{monitor_html}</div>'
        '</div>'
        '</div>'
    )

    placeholder = "___KW_PLACEHOLDER___"

    # 1. Update tbody (id="kw-tbody")
    new_html = re.sub(
        r'(<tbody id="kw-tbody">)(.*?)(</tbody>)',
        r'\g<1>' + placeholder + r'\3',
        html, flags=re.DOTALL, count=1
    )
    if placeholder in new_html:
        html = new_html.replace(placeholder, tbody_rows, 1)
    else:
        log.warning("kw-tbody anchor not found — keyword table not updated")
        return html

    # 2. Update action summary bar (id="kw-action-summary")
    html = re.sub(
        r'(<div[^>]+id="kw-action-summary"[^>]*>)(.*?)(</div>)',
        r'\g<1>' + placeholder + r'\3',
        html, flags=re.DOTALL, count=1
    )
    if placeholder in html:
        html = html.replace(placeholder, summary_html, 1)

    # 3. Update date label (id="kw-date")
    html = re.sub(
        r'(<span[^>]+id="kw-date"[^>]*>)[^<]*(</span>)',
        r'\g<1>' + kw_date + r'\2',
        html, count=1
    )

    # 4. Replace three-column callout panel (between sentinel comments)
    html = re.sub(
        r'(<!-- KW-CALLOUT-START -->).*?(<!-- KW-CALLOUT-END -->)',
        r'\g<1>' + placeholder + r'\g<2>',
        html, flags=re.DOTALL, count=1
    )
    if placeholder in html:
        html = html.replace(placeholder, '\n    ' + callout_html + '\n    ', 1)

    log.info("Keyword table updated: %d rows | ↑raise=%d ⛔negate=%d 👁monitor=%d",
             len(rows), n_raise, n_negate, n_monitor)
    return html

def _aggregate_weekly(data: dict) -> dict:
    """Roll up weekly rows into a single campaigns-style summary for KPI patching."""
    from collections import defaultdict
    by_camp = defaultdict(lambda: {"spend":0,"sales":0,"orders":0,"ntb":0,"impr":0,"clicks":0,"pos_weighted":0,"status":"ACTIVE","name":""})
    for r in data["rows"]:
        c = by_camp[r["campaign"]]
        c["name"] = r["campaign"]
        c["status"] = r["status"]
        c["spend"]  += r["spend"]
        c["sales"]  += r["sales"]
        c["orders"] += r["orders"]
        c["ntb"]    += r["ntb"]
        c["impr"]   += r["impr"]
        c["clicks"] += r["clicks"]
        c["pos_weighted"] += r["position"] * r["clicks"]
    rows = []
    for camp, v in by_camp.items():
        clicks = v["clicks"] or 1
        rows.append({
            "name": v["name"], "status": v["status"],
            "spend": v["spend"], "sales": v["sales"], "orders": v["orders"],
            "ntb": v["ntb"], "impr": v["impr"], "clicks": v["clicks"],
            "roas": (v["sales"] / v["spend"] * 100) if v["spend"] else 0,
            "position": v["pos_weighted"] / clicks,
            "ctr": (v["clicks"] / v["impr"] * 100) if v["impr"] else 0,
            "cpc": (v["spend"] / v["clicks"]) if v["clicks"] else 0,
        })
    return {"type": "campaigns", "rows": rows, "source": data["source"]}

def _load_daily_history() -> dict:
    """Load ISO-date keyed history from disk. Returns {} if not found."""
    try:
        return json.loads(DAILY_HISTORY_FILE.read_text()) if DAILY_HISTORY_FILE.exists() else {}
    except Exception:
        return {}

def _save_daily_history(history: dict):
    DAILY_HISTORY_FILE.write_text(json.dumps(history, indent=2, sort_keys=True))

def _update_daily_chart(html, data):
    """Merge new daily rows into history, then rebuild the dd array and date range title."""
    new_rows = data["rows"]
    if not new_rows:
        return html

    # ── Merge new rows into persistent history (new data wins for same date) ──
    history = _load_daily_history()
    for r in new_rows:
        history[r["date"]] = {"sp": r["spend"], "sa": r["sales"], "r": r["roas"], "u": r["units"]}
    _save_daily_history(history)

    # ── Update historical monthly chart ───────────────────────────────────────
    html = _update_historical_chart(html, history)

    # ── Build sorted full-history rows ────────────────────────────────────────
    all_rows = sorted(history.items())   # [(iso_date, {...}), ...]

    def _fmt_day(iso: str) -> str:
        from datetime import datetime as _dt
        try:
            dt = _dt.strptime(iso, "%Y-%m-%d")
            return dt.strftime("%b %#d") if sys.platform == "win32" else dt.strftime("%b %-d")
        except Exception:
            return iso

    entries = ",".join(
        f"{{d:'{_fmt_day(iso)}',sp:{v['sp']},sa:{v['sa']},r:{v['r']},u:{v['u']}}}"
        for iso, v in all_rows
    )
    dd_js = f"  const dd=[{entries}];"

    first = _fmt_day(all_rows[0][0])
    last  = _fmt_day(all_rows[-1][0])
    date_range = f"{first} – {last}"

    placeholder = "___DD_PLACEHOLDER___"

    # Replace dd array between sentinels
    html = re.sub(
        r'(// DD-START\s*\n).*?(\s*// DD-END)',
        r'\g<1>' + placeholder + r'\2',
        html, flags=re.DOTALL, count=1
    )
    if placeholder in html:
        html = html.replace(placeholder, dd_js + "\n  ", 1)
    else:
        log.warning("DD-START/END sentinels not found — daily chart not updated")
        return html

    # Update the date range span
    html = re.sub(
        r'(<span id="daily-range">)[^<]*(</span>)',
        r'\g<1>' + date_range + r'\2',
        html, count=1
    )

    log.info("Daily chart updated: %d days total (%s), %d new/updated",
             len(all_rows), date_range, len(new_rows))
    return html

def _update_historical_chart(html, history: dict) -> str:
    """Roll up daily_history.json into monthly totals and rebuild the HIST-START/END block."""
    from datetime import datetime as _dt

    # Hardcoded pre-tracking months (Aug–Nov 2025) — no source CSV available
    SEED = [
        ("Aug '25", "2025-08", 527.76,  5501.43),
        ("Sep '25", "2025-09", 551.67,  5644.41),
        ("Oct '25", "2025-10", 607.38,  5631.33),
        ("Nov '25", "2025-11", 1024.04, 7291.28),
    ]
    SEED_MONTHS = {s[1] for s in SEED}

    # Roll up daily history by month
    monthly: dict = {}
    for iso, v in history.items():
        ym = iso[:7]  # "2026-04"
        if ym in SEED_MONTHS:
            continue
        if ym not in monthly:
            monthly[ym] = {"sp": 0.0, "sa": 0.0}
        monthly[ym]["sp"] += v["sp"]
        monthly[ym]["sa"] += v["sa"]

    # Build contiguous month list from Dec 2025 to latest month in history
    latest_ym = max(monthly.keys()) if monthly else "2026-06"
    start = _dt(2025, 12, 1)
    end   = _dt(int(latest_ym[:4]), int(latest_ym[5:7]), 1)
    gap_months = []
    cur = start
    while cur <= end:
        gap_months.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = _dt(cur.year + 1, 1, 1)
        else:
            cur = _dt(cur.year, cur.month + 1, 1)

    def _label(ym: str) -> str:
        dt = _dt(int(ym[:4]), int(ym[5:7]), 1)
        yr = "'" + ym[2:4]
        return f"{dt.strftime('%b')} {yr}"

    labels, spends, sales, roas_vals = [], [], [], []

    # Seed months first
    for lbl, ym, sp, sa in SEED:
        labels.append(lbl)
        spends.append(sp)
        sales.append(sa)
        roas_vals.append(round(sa / sp, 2) if sp else None)

    # Gap / dynamic months
    for ym in gap_months:
        labels.append(_label(ym))
        if ym in monthly:
            sp = round(monthly[ym]["sp"], 2)
            sa = round(monthly[ym]["sa"], 2)
            r  = round(sa / sp, 2) if sp else None
            spends.append(sp)
            sales.append(sa)
            roas_vals.append(r)
        else:
            spends.append(None)
            sales.append(None)
            roas_vals.append(None)

    def _js(lst):
        parts = []
        for v in lst:
            parts.append("null" if v is None else str(v))
        return "[" + ", ".join(parts) + "]"

    def _js_str(lst):
        return "[" + ", ".join(f'"{v}"' for v in lst) + "]"

    block = (
        f"  // HIST-START\n"
        f"  const hLabels = {_js_str(labels)};\n"
        f"  const hSpend  = {_js(spends)};\n"
        f"  const hSales  = {_js(sales)};\n"
        f"  const hRoas   = {_js(roas_vals)};\n"
        f"  // HIST-END"
    )

    placeholder = "___HIST_PLACEHOLDER___"
    new_html = re.sub(
        r'// HIST-START.*?// HIST-END',
        placeholder,
        html, flags=re.DOTALL, count=1
    )
    if placeholder in new_html:
        html = new_html.replace(placeholder, block, 1)
        log.info("Historical chart updated: %d months", len(labels))
    else:
        log.warning("HIST-START/END sentinels not found — historical chart not updated")
    return html


def _update_purchased(html, data):
    """Update the halo / cross-sell table with purchased products data."""
    rows = sorted(data["rows"], key=lambda r: r["direct_units"], reverse=True)
    if not rows:
        return html
    tbody_rows = "\n".join(
        f'<tr><td>{r["purchased_name"][:35] or r["purchased_sku"]}</td>'
        f'<td style="color:var(--accent);font-weight:600;">{r["direct_units"]}</td>'
        f'<td>—</td>'
        f'<td><span class="pill pill-purple">{r["purchased_cat"][:20]}</span></td></tr>'
        for r in rows[:10]
    )
    html = re.sub(
        r'(<h3>[^<]*Halo[^<]*(?:<[^>]+>)*[^<]*</h3>.*?<tbody>)(.*?)(</tbody>)',
        r'\g<1>' + tbody_rows + r'\3',
        html, flags=re.DOTALL, count=1
    )
    return html

def _update_products(html, data):
    rows = sorted(data["rows"], key=lambda r: r["spend"], reverse=True)
    if not rows:
        return html

    def roas_pill(roas_pct):
        r = _roas_str(roas_pct)
        cls = "pill-green" if r >= 8 else "pill-yellow" if r >= 2.42 else "pill-red"
        return f'<span class="pill {cls}">{r}x</span>'

    tbody_rows = "\n".join(
        f'<tr><td><div style="font-weight:600;">{r["product"][:30]}</div>'
        f'<div style="font-size:11px;color:var(--muted);">SKU {r["sku"]}</div></td>'
        f'<td>${r["spend"]:.2f}</td>'
        f'<td>${r["sales"]:,.2f}</td>'
        f'<td>{roas_pill(r["roas"])}</td>'
        f'<td>{r["cvr"]:.1f}%</td>'
        f'<td>{r["orders"]}</td></tr>'
        for r in rows[:10]
    )

    html = re.sub(
        r'(<h3>Product Performance</h3>.*?<tbody>)(.*?)(</tbody>)',
        r'\g<1>' + tbody_rows + r'\3',
        html, flags=re.DOTALL, count=1
    )
    return html

# ── Git push ───────────────────────────────────────────────────────────────
def git_push(report_name: str):
    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"Auto-update: {report_name} ({today})"
    try:
        subprocess.run(["git", "add", "index.html"], cwd=DASHBOARD_DIR, check=True, capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=DASHBOARD_DIR, capture_output=True
        )
        if result.returncode == 0:
            log.info("No changes detected in index.html — skipping commit")
            return
        subprocess.run(["git", "commit", "-m", msg], cwd=DASHBOARD_DIR, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=DASHBOARD_DIR, check=True, capture_output=True)
        log.info("Pushed to GitHub: %s", msg)
    except subprocess.CalledProcessError as e:
        log.error("Git error: %s", e.stderr.decode() if e.stderr else str(e))

# ── Process a single file ──────────────────────────────────────────────────
def process_file(path: Path):
    processed = load_processed()
    key = f"{path.name}::{path.stat().st_size}"
    if key in processed:
        log.debug("Already processed: %s", path.name)
        return

    # Wait briefly for file to finish writing
    time.sleep(2)

    log.info("New Chewy report detected: %s", path.name)

    rtype = detect_report_type(path)
    parser = PARSERS.get(rtype)
    if not parser:
        log.warning("Unknown report type for %s — archived but not processed", path.name)
    else:
        try:
            data = parser(path)
            update_dashboard(data)
            git_push(path.name)
            # Archive the file
            dest = REPORTS_ARCHIVE / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.name}"
            shutil.copy2(path, dest)
            log.info("Archived to: %s", dest.name)
        except Exception as e:
            log.error("Failed to process %s: %s", path.name, e, exc_info=True)

    processed.add(key)
    save_processed(processed)

# ── File system event handler ──────────────────────────────────────────────
class ChewyReportHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if is_chewy_report(path):
            process_file(path)

    def on_moved(self, event):
        # Handles browser "download complete" rename (e.g., .crdownload → .csv)
        if event.is_directory:
            return
        path = Path(event.dest_path)
        if is_chewy_report(path):
            process_file(path)

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("Chewy Dashboard Watcher starting up")
    log.info("Watching: %s", DOWNLOADS_DIR)
    log.info("Dashboard: %s", DASHBOARD_HTML)
    log.info("=" * 60)

    # Scan for any unprocessed reports already in Downloads
    for csv_file in DOWNLOADS_DIR.glob("*.csv"):
        if is_chewy_report(csv_file):
            process_file(csv_file)

    observer = Observer()
    observer.schedule(ChewyReportHandler(), str(DOWNLOADS_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Watcher stopped by user")
        observer.stop()
    observer.join()

if __name__ == "__main__":
    # Single-instance guard: exit silently if already running
    import ctypes, sys as _sys
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "ChewyDashboardWatcherMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        _sys.exit(0)
    main()
