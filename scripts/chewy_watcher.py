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
    """Return 'campaigns', 'keywords', 'products', 'purchased', 'campaign_weekly', or 'unknown'."""
    name = path.name.lower()
    # Dated export names
    if name.startswith("promoted_products_"):
        return "products"
    if name.startswith("purchased_products_"):
        return "purchased"
    if "keyword" in name:
        return "keywords"
    if "campaign" in name or "camaign" in name:
        # Distinguish weekly vs daily vs summary
        return "campaign_weekly" if "week" in name else "campaigns"
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
            name   = (row.get("Campaign") or row.get("Campaign name") or "").strip()
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
            campaign = (row.get("Campaign") or row.get("Campaign name") or "").strip()
            keyword  = (row.get("Keyword") or row.get("Search term") or "").strip()
            sales    = clean_num(row.get("Direct Sales") or row.get("Direct sales") or "0")
            orders   = int(clean_num(row.get("Total Orders") or row.get("Total orders") or "0"))
            pos      = clean_num(row.get("Avg Position") or row.get("Avg position") or "0")
            ctr_raw  = clean_num(row.get("CTR") or "0")
            cvr_raw  = clean_num(row.get("CVR") or row.get("Conversion rate") or "0")
            roas_raw = clean_num(row.get("Direct ROAS") or "0")
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
            rows.append({
                "week":     row.get("Week Start Date", "").strip(),
                "campaign": row.get("Campaign", "").strip(),
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

def parse_purchased(path: Path) -> dict:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append({
                "campaign":         row.get("Campaign", "").strip(),
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
    elif rtype == "keywords":
        html = _update_keywords(html, data)
    elif rtype == "products":
        html = _update_products(html, data)
    elif rtype == "purchased":
        html = _update_purchased(html, data)

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

def _update_campaigns(html, data):
    rows = data["rows"]
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

    # Patch KPI values in the Active Campaign section
    replacements = [
        (r'(<div class="label">Spend</div><div class="value[^"]*">)\$[\d,.]+',
         r'\g<1>$' + f"{total_spend:.2f}"),
        (r'(<div class="label">Direct Sales</div><div class="value">)\$[\d,.]+',
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
    for pattern, repl in replacements:
        new_html = re.sub(pattern, repl, html, count=1)
        if new_html != html:
            html = new_html

    return html

def _update_keywords(html, data):
    # Keyword data is displayed in a table — rebuild the tbody
    rows = sorted(data["rows"], key=lambda r: r["spend"], reverse=True)
    if not rows:
        return html

    def kw_pill(kw):
        kw = kw.strip('"').strip("'")
        if not kw or kw.lower() in ("non-boosted", "", "--"):
            return '<span class="pill pill-purple">Non-Boosted</span>'
        return f'<span class="pill pill-blue">{kw[:20]}</span>'

    def roas_pill(r):
        r = round(r, 2)
        if r >= 8:
            return f'<span class="pill pill-green">{r}x</span>'
        elif r >= 2.42:
            return f'<span class="pill pill-yellow">{r}x</span>'
        else:
            return f'<span class="pill pill-red">{r}x</span>'

    def action(r, kw):
        r = round(r, 2)
        kw_l = kw.lower().strip('"\'')
        if r >= 10:
            return '<span class="pill pill-green">✓ Keep + Scale</span>'
        elif r >= 5:
            return '<span class="pill pill-green">✓ Keep</span>'
        elif r >= 2.42:
            return '<span class="pill pill-yellow">Monitor</span>'
        else:
            return '<span class="pill pill-red">⛔ Pause</span>'

    tbody_rows = "\n".join(
        '<tr>'
        f'<td>{kw_pill(r["keyword"])}</td>'
        f'<td style="color:var(--muted);font-size:11px;">{r["campaign"][:14]}</td>'
        f'<td>${r["spend"]:.2f}</td>'
        f'<td>{r["impr"]:,}</td>'
        f'<td>{r["ctr"]:.2f}%</td>'
        f'<td>{roas_pill(r["roas"])}</td>'
        f'<td>{r["cvr"]:.1f}%</td>'
        f'<td>{action(r["roas"], r["keyword"])}</td>'
        '</tr>'
        for r in rows[:12]
    )

    # Replace the tbody inside "Keyword Performance" table
    # Use a placeholder to avoid regex backreference issues with backslashes in replacement
    placeholder = "___TBODY_PLACEHOLDER___"
    new_html = re.sub(
        r'(<h3>Keyword Performance[^<]*(?:<[^>]+>)*[^<]*</h3>.*?<tbody>)(.*?)(</tbody>)',
        r'\g<1>' + placeholder + r'\3',
        html, flags=re.DOTALL, count=1
    )
    if placeholder in new_html:
        html = new_html.replace(placeholder, tbody_rows, 1)
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

    log.info("New Chewy report detected: %s", path.name)

    # Wait briefly for file to finish writing
    time.sleep(2)

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
