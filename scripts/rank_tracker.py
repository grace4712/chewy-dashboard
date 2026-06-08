# Chewy Organic Rank Tracker
# ============================
# Runs once daily, searches Chewy for each target keyword,
# records where each Purrfect Portal product appears in results.
#
# Output:  scripts/rank_history.json
#          updates index.html with a rank table + trend sparklines
#
# Schedule: Windows Task Scheduler — run daily at 9:00 AM
# Log:      scripts/rank_tracker.log

import re
import json
import sys
import time
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

import curl_cffi.requests as httpx

# ── Paths ──────────────────────────────────────────────────────────────────
DASHBOARD_DIR    = Path(r"C:\Users\retai\OneDrive\Desktop\Claude Code")
DASHBOARD_HTML   = DASHBOARD_DIR / "index.html"
RANK_HISTORY     = DASHBOARD_DIR / "scripts" / "rank_history.json"
LOG_FILE         = DASHBOARD_DIR / "scripts" / "rank_tracker.log"

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("rank_tracker")

# ── Config: keywords + products to track ──────────────────────────────────
# Keywords: what to search on Chewy
KEYWORDS = [
    "cat door",
    "cat door for interior door",
    "extra large cat door",
    "cat doors",
    "cat door flap",
    "interior cat door",
    "cat door for door",
]

# Product identifier: any Chewy URL containing this string = Purrfect Portal
BRAND_SLUG = "purrfect-portal"

# Specific products we want to track by URL slug keyword
# Maps display name → slug fragment to look for in the URL
PRODUCTS = {
    "Meow Manor XL":      "meow-manor-extra-large",
    "Meow Manor XL Alt":  "meow-manor-xl",
    "Meow Manor":         "meow-manor",
    "Fairy Door":         "fairy-door",
    "French Door":        "french-door",
    "Gnome Door":         "gnome-door",
}

# Pages to scan per keyword (20 results/page → 3 pages = top 60)
PAGES_TO_SCAN = 3

# Delay between requests (seconds) — be polite to Chewy
DELAY_BETWEEN_PAGES    = 5   # seconds between page fetches
DELAY_BETWEEN_KEYWORDS = 20  # seconds between keywords

# ── History helpers ────────────────────────────────────────────────────────
def load_history() -> dict:
    try:
        return json.loads(RANK_HISTORY.read_text()) if RANK_HISTORY.exists() else {}
    except Exception:
        return {}

def save_history(h: dict):
    RANK_HISTORY.write_text(json.dumps(h, indent=2, sort_keys=True))

# ── Fetch one search results page ─────────────────────────────────────────
def fetch_search_page(session, keyword: str, page: int) -> str | None:
    """Return HTML of the search results page, or None on failure."""
    url = f"https://www.chewy.com/s?query={keyword.replace(' ', '+')}&page={page}"
    headers = {
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         "https://www.chewy.com/",
        "Cache-Control":   "no-cache",
    }
    for attempt in range(3):
        try:
            resp = session.get(url, headers=headers, timeout=30)
            if resp.status_code == 200 and len(resp.text) > 10000:
                return resp.text
            elif resp.status_code == 429:
                wait = 60 * (attempt + 1)
                log.warning("429 rate limit on '%s' page %d — waiting %ds", keyword, page, wait)
                time.sleep(wait)
            else:
                log.warning("HTTP %d on '%s' page %d", resp.status_code, keyword, page)
                time.sleep(10)
        except Exception as e:
            log.error("Fetch error on '%s' page %d: %s", keyword, page, e)
            time.sleep(15)
    return None

# ── Parse product ranks from HTML ─────────────────────────────────────────
def parse_ranks(html: str, page: int) -> list[dict]:
    """
    Return list of {rank, url_path, name} for every product on the page.
    Rank is 1-based across pages (page 1 = 1..20, page 2 = 21..40, etc.)
    """
    results = []

    # Chewy search results embed product links as href="/SLUG/dp/ID"
    # Extract in document order — that order IS the rank
    pattern = re.compile(
        r'href="(/([^"]+)/dp/(\d+)[^"]*?)"[^>]*>.*?'
        r'(?:<[^>]+>)*\s*([^<]{5,80})',
        re.DOTALL
    )

    # Simpler: just extract all /*/dp/* hrefs in order
    links = re.findall(r'href="(/[^"]+/dp/\d+)"', html)

    # Deduplicate while preserving order (each product appears multiple times)
    seen = {}
    for link in links:
        if link not in seen:
            seen[link] = len(seen) + 1  # position within this page

    base_rank = (page - 1) * 20
    for path, pos in seen.items():
        results.append({
            "rank":     base_rank + pos,
            "url_path": path,
        })

    return results

# ── Classify a result as a Purrfect Portal product ────────────────────────
def classify_product(url_path: str) -> str | None:
    """
    Return a display name if this URL belongs to Purrfect Portal, else None.
    Checks specific products first, then falls back to brand slug.
    """
    path_lower = url_path.lower()
    if BRAND_SLUG not in path_lower:
        return None
    # Match specific products
    for name, slug in PRODUCTS.items():
        if slug in path_lower:
            return name
    # Generic Purrfect Portal product
    # Extract a readable name from the URL slug
    slug_part = path_lower.split("/dp/")[0].lstrip("/")
    return slug_part.replace("-", " ").title()[:40]

# ── Run one keyword ────────────────────────────────────────────────────────
def track_keyword(session, keyword: str) -> dict:
    """
    Returns {product_name: rank} for all PP products found in top results.
    """
    found = {}
    for page in range(1, PAGES_TO_SCAN + 1):
        html = fetch_search_page(session, keyword, page)
        if not html:
            log.warning("No HTML for '%s' page %d — stopping", keyword, page)
            break

        results = parse_ranks(html, page)
        log.info("  '%s' page %d: %d unique product links", keyword, page, len(results))

        for item in results:
            product = classify_product(item["url_path"])
            if product and product not in found:
                found[product] = item["rank"]
                log.info("    FOUND '%s' at rank %d", product, item["rank"])

        if page < PAGES_TO_SCAN:
            time.sleep(DELAY_BETWEEN_PAGES)

    return found

# ── Dashboard HTML update ──────────────────────────────────────────────────
def _spark(values: list) -> str:
    """Generate a tiny SVG sparkline for a list of rank values (lower=better)."""
    if len(values) < 2:
        return ""
    # Invert so lower rank = higher on chart
    inv = [1 / v if v else 0 for v in values]
    mn, mx = min(inv), max(inv)
    rng = mx - mn or 1
    w, h = 60, 20
    pts = []
    for i, v in enumerate(inv):
        x = i / (len(inv) - 1) * w
        y = h - ((v - mn) / rng) * (h - 2) - 1
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg width="{w}" height="{h}" style="vertical-align:middle">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="#6c63ff" stroke-width="1.5"/>'
        f'</svg>'
    )

def _rank_pill(rank) -> str:
    if rank is None or rank == 0:
        return '<span style="color:var(--muted);font-size:11px;">not found</span>'
    cls = ("#22c55e" if rank <= 10 else
           "#f59e0b" if rank <= 20 else
           "#f97316" if rank <= 40 else "#ef4444")
    return f'<span style="color:{cls};font-weight:700;">#{rank}</span>'

def _change_badge(today, yesterday) -> str:
    if today is None or yesterday is None:
        return ""
    diff = yesterday - today   # positive = improved (rank went down numerically)
    if diff == 0:
        return '<span style="color:var(--muted);font-size:10px;">—</span>'
    color = "#22c55e" if diff > 0 else "#ef4444"
    arrow = "▲" if diff > 0 else "▼"
    return f'<span style="color:{color};font-size:10px;">{arrow}{abs(diff)}</span>'

def update_rank_section(history: dict):
    """Rebuild the <!-- RANK-TRACKER-START/END --> section in index.html."""
    if not DASHBOARD_HTML.exists():
        log.error("index.html not found")
        return

    today_iso = datetime.now().strftime("%Y-%m-%d")
    yesterday_iso = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    today_data    = history.get(today_iso, {})
    yesterday_data = history.get(yesterday_iso, {})

    # Collect all keyword × product combinations seen in last 7 days
    recent_dates = sorted(history.keys())[-7:]

    # Build keyword → {product: [rank_d-6 .. rank_today]} mapping
    kw_products: dict[str, dict[str, list]] = {}
    for kw in KEYWORDS:
        kw_products[kw] = {}
        for d in recent_dates:
            day_kw = history.get(d, {}).get(kw, {})
            for prod, rank in day_kw.items():
                kw_products[kw].setdefault(prod, [None] * len(recent_dates))
                idx = recent_dates.index(d)
                kw_products[kw][prod][idx] = rank

    # Build table rows
    rows_html = ""
    for kw in KEYWORDS:
        products = kw_products.get(kw, {})
        if not products:
            # No PP products found for this keyword at all
            rows_html += (
                f'<tr>'
                f'<td style="font-size:12px;padding:6px 8px;">{kw}</td>'
                f'<td style="color:var(--muted);font-size:11px;font-style:italic;">not ranked (top {PAGES_TO_SCAN * 20})</td>'
                f'<td></td><td></td><td></td>'
                f'</tr>'
            )
            continue
        first = True
        for prod, ranks in sorted(products.items(), key=lambda x: (x[1][-1] or 999)):
            today_rank     = today_data.get(kw, {}).get(prod)
            yesterday_rank = yesterday_data.get(kw, {}).get(prod)
            rows_html += (
                f'<tr style="{"border-top:1px solid rgba(255,255,255,0.04);" if first else ""}">'
                f'<td style="font-size:12px;padding:6px 8px;{"color:var(--muted);" if not first else ""}">'
                f'{"" if not first else kw}</td>'
                f'<td style="font-size:11px;">{prod}</td>'
                f'<td style="text-align:center;">{_rank_pill(today_rank)}</td>'
                f'<td style="text-align:center;">{_change_badge(today_rank, yesterday_rank)}</td>'
                f'<td style="text-align:right;">{_spark(ranks)}</td>'
                f'</tr>'
            )
            first = False

    # Timestamp
    ts = datetime.now().strftime("%b %#d, %Y" if sys.platform == "win32" else "%b %-d, %Y")

    section_html = f"""
    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
        <h3 style="margin:0;">🔍 Organic Search Rank — Chewy</h3>
        <span style="font-size:11px;color:var(--muted);">Updated {ts} · top {PAGES_TO_SCAN * 20} results · {len(KEYWORDS)} keywords</span>
      </div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:10px;">
        Rank of each Purrfect Portal product in Chewy organic search. Lower # = better.
        <span style="color:#22c55e;">■</span> top 10 &nbsp;
        <span style="color:#f59e0b;">■</span> 11–20 &nbsp;
        <span style="color:#f97316;">■</span> 21–40 &nbsp;
        <span style="color:#ef4444;">■</span> 41+
      </div>
      <div style="overflow-x:auto;">
      <table id="rank-table">
        <thead>
          <tr>
            <th>Keyword</th>
            <th>Product</th>
            <th style="text-align:center;">Today</th>
            <th style="text-align:center;">vs Yesterday</th>
            <th style="text-align:right;">7-day trend</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
      </div>
    </div>"""

    html = DASHBOARD_HTML.read_text(encoding="utf-8")
    placeholder = "___RANK_PLACEHOLDER___"

    # Replace between sentinels
    new_html = re.sub(
        r'<!-- RANK-TRACKER-START -->.*?<!-- RANK-TRACKER-END -->',
        '<!-- RANK-TRACKER-START -->' + placeholder + '<!-- RANK-TRACKER-END -->',
        html, flags=re.DOTALL, count=1
    )
    if placeholder in new_html:
        html = new_html.replace(placeholder, section_html, 1)
        DASHBOARD_HTML.write_text(html, encoding="utf-8")
        log.info("Dashboard rank section updated")
    else:
        log.warning("<!-- RANK-TRACKER-START/END --> sentinels not found in index.html")

# ── Git push ───────────────────────────────────────────────────────────────
def git_push():
    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"Rank update: {today}"
    try:
        subprocess.run(["git", "add", "index.html", "scripts/rank_history.json"],
                       cwd=DASHBOARD_DIR, check=True, capture_output=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                cwd=DASHBOARD_DIR, capture_output=True)
        if result.returncode == 0:
            log.info("No rank changes — skipping commit")
            return
        subprocess.run(["git", "commit", "-m", msg],
                       cwd=DASHBOARD_DIR, check=True, capture_output=True)
        subprocess.run(["git", "push"],
                       cwd=DASHBOARD_DIR, check=True, capture_output=True)
        log.info("Pushed rank update to GitHub")
    except subprocess.CalledProcessError as e:
        log.error("Git error: %s", e.stderr.decode() if e.stderr else str(e))

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    today_iso = datetime.now().strftime("%Y-%m-%d")
    log.info("=" * 60)
    log.info("Chewy Rank Tracker — %s", today_iso)
    log.info("Keywords: %d  |  Pages/keyword: %d  |  Max rank: %d",
             len(KEYWORDS), PAGES_TO_SCAN, PAGES_TO_SCAN * 20)
    log.info("=" * 60)

    history = load_history()

    # Skip if already ran today
    if today_iso in history and len(history[today_iso]) >= len(KEYWORDS):
        log.info("Already ran today — skipping. Delete %s entry to re-run.", today_iso)
        return

    session = httpx.Session(impersonate="chrome124")

    # Warm up with a homepage visit
    log.info("Warming up session...")
    try:
        r = session.get("https://www.chewy.com/", timeout=25,
                        headers={"Accept-Language": "en-US,en;q=0.9"})
        log.info("Homepage: HTTP %d", r.status_code)
    except Exception as e:
        log.warning("Homepage warm-up failed: %s", e)
    time.sleep(5)

    today_results: dict[str, dict] = history.get(today_iso, {})

    for i, keyword in enumerate(KEYWORDS):
        log.info("Searching: '%s' (%d/%d)", keyword, i + 1, len(KEYWORDS))
        found = track_keyword(session, keyword)
        today_results[keyword] = found
        if not found:
            log.info("  → No Purrfect Portal products found in top %d", PAGES_TO_SCAN * 20)
        else:
            for prod, rank in found.items():
                log.info("  → %-35s  rank #%d", prod, rank)

        if i < len(KEYWORDS) - 1:
            log.info("  Waiting %ds before next keyword...", DELAY_BETWEEN_KEYWORDS)
            time.sleep(DELAY_BETWEEN_KEYWORDS)

    history[today_iso] = today_results
    save_history(history)
    log.info("Saved rank history (%d days total)", len(history))

    update_rank_section(history)
    git_push()
    log.info("Done.")

if __name__ == "__main__":
    main()
