# Chewy Organic Rank Tracker
# ============================
# Runs once daily, searches Chewy for each target keyword,
# records where each Purrfect Portal product appears in results.
#
# Uses real Chrome via CDP (remote debugging) to bypass Kasada bot protection.
# Chrome is launched automatically if not already running with debug port.
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
import random
import logging
import subprocess
import socket
import urllib.request
import io
from pathlib import Path
from datetime import datetime, timedelta

# Fix Windows console encoding for Unicode product names in log output
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ── Paths ──────────────────────────────────────────────────────────────────
DASHBOARD_DIR    = Path(r"C:\Users\retai\OneDrive\Desktop\Claude Code")
DASHBOARD_HTML   = DASHBOARD_DIR / "index.html"
RANK_HISTORY     = DASHBOARD_DIR / "scripts" / "rank_history.json"
LOG_FILE         = DASHBOARD_DIR / "scripts" / "rank_tracker.log"

CHROME_EXE            = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
# Dedicated profile for rank tracker — never conflicts with your main Chrome session
RANK_TRACKER_PROFILE  = r"C:\Users\retai\AppData\Local\Google\Chrome\RankTrackerProfile"
DEBUG_PORT            = 9223   # use 9223 to avoid conflict with any other debug sessions

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
KEYWORDS = [
    "cat door",
    "cat door for interior door",
    "extra large cat door",
    "cat doors",
    "cat door flap",
    "interior cat door",
    "cat door for door",
]

BRAND_SLUG = "purrfect-portal"

PRODUCTS = {
    "Meow Manor XL":       "meow-manor-plastic",         # /purrfect-portal-meow-manor-plastic/dp/...
    "Meow Manor":          "meow-manor-interior",         # /purrfect-portal-meow-manor-interior/dp/...
    "Wall Entry Meow Manor": "wall-entry-meow-manor",    # /purrfect-portal-wall-entry-meow-manor/dp/...
    "Gnome Door":          "gnome-plastic",              # /purrfect-portal-gnome-plastic/dp/...
    "Beacon Hill":         "beacon-hill-plastic",         # /purrfect-portal-beacon-hill-plastic/dp/...
    "Fairy Door":          "fairy-plastic",               # /purrfect-portal-fairy-plastic/dp/...
}

PAGES_TO_SCAN          = 3
DELAY_BETWEEN_PAGES    = 8    # base seconds between page fetches (jitter added)
DELAY_BETWEEN_KEYWORDS = 25   # base seconds between keywords (jitter added)

# A current, real Chrome UA string — sterile automation UAs are an easy bot tell
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

def _jitter(base: float, spread: float = 0.4) -> float:
    """Return base seconds ± up to `spread` fraction, to avoid robotic fixed timing."""
    return base * (1 + random.uniform(-spread, spread))

# ── History helpers ────────────────────────────────────────────────────────
def load_history() -> dict:
    try:
        return json.loads(RANK_HISTORY.read_text()) if RANK_HISTORY.exists() else {}
    except Exception:
        return {}

def save_history(h: dict):
    RANK_HISTORY.write_text(json.dumps(h, indent=2, sort_keys=True))

# ── Chrome CDP helpers ─────────────────────────────────────────────────────
def _is_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False

def _is_chrome_cdp_ready() -> bool:
    """Check if Chrome's CDP endpoint is responding."""
    if not _is_port_open(DEBUG_PORT):
        return False
    try:
        with urllib.request.urlopen(f"http://localhost:{DEBUG_PORT}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

def ensure_chrome_running() -> subprocess.Popen | None:
    """
    Make sure Chrome is running with CDP on DEBUG_PORT.
    Returns a Popen handle if WE started Chrome (caller must kill it), else None.
    """
    if _is_chrome_cdp_ready():
        log.info("Chrome already running with CDP on port %d", DEBUG_PORT)
        return None

    log.info("Starting Chrome with --remote-debugging-port=%d ...", DEBUG_PORT)
    # Use a dedicated profile so we never conflict with the user's running Chrome
    import os
    os.makedirs(RANK_TRACKER_PROFILE, exist_ok=True)
    proc = subprocess.Popen([
        CHROME_EXE,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={RANK_TRACKER_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        # Anti-detection: hide the automation fingerprint Kasada looks for.
        # (Dropped --no-sandbox / --disable-extensions — both are bot tells.)
        "--disable-blink-features=AutomationControlled",
        "--exclude-switches=enable-automation",
        "--disable-features=IsolateOrigins,site-per-process",
        f"--user-agent={USER_AGENT}",
        "--window-size=1280,900",
        "about:blank",
    ])

    # Wait up to 30s for CDP to become ready
    for i in range(30):
        time.sleep(1)
        if _is_chrome_cdp_ready():
            log.info("Chrome CDP ready after %ds", i + 1)
            return proc
        log.debug("Waiting for Chrome CDP... (%d/30)", i + 1)

    log.error("Chrome CDP not ready after 30s — aborting")
    proc.terminate()
    return None

# ── Human-like behavior ────────────────────────────────────────────────────
def _human_dwell(page, lo: float, hi: float):
    """Pause for a human-ish interval and scroll the page a little."""
    try:
        steps = random.randint(2, 4)
        for _ in range(steps):
            page.mouse.wheel(0, random.randint(300, 700))
            time.sleep(random.uniform(0.4, 1.1))
    except Exception:
        pass
    time.sleep(random.uniform(lo, hi))

# ── Kasada challenge handling ──────────────────────────────────────────────
def clear_kasada_challenge(page, url: str, label: str = "page", max_cycles: int = 4) -> bool:
    """
    Navigate to `url` and clear Kasada's JS challenge if served.

    Kasada returns HTTP 429 + a JavaScript proof-of-work on the first request of a
    fresh session. A real browser runs that JS, gets a clearance cookie, and the
    next load returns 200. We replicate that: on 429 we let the challenge JS run
    for a bit, then reload — up to `max_cycles` times. Returns True once we see a
    200 (cleared), False if 429 persists (genuine block).
    """
    for cycle in range(max_cycles):
        try:
            resp = (page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if cycle == 0 else
                    page.reload(wait_until="domcontentloaded", timeout=30000))
            status = resp.status if resp else None
        except Exception as e:
            log.warning("  %s nav attempt %d errored: %s", label, cycle + 1, e)
            status = None

        log.info("  %s attempt %d: HTTP %s", label, cycle + 1, status)

        if status == 200:
            return True

        # 429 or transient error → let Kasada's challenge JS execute, then reload.
        solve_wait = random.uniform(9, 15)
        log.info("    challenge served — letting it solve (%ds)...", int(solve_wait))
        time.sleep(solve_wait)

    return False

# ── Fetch one search results page via Playwright CDP ──────────────────────
def fetch_search_page_cdp(page, keyword: str, pagenum: int) -> str | None:
    """Navigate the CDP page to Chewy search and return HTML, or None on failure."""
    url = f"https://www.chewy.com/s?query={keyword.replace(' ', '+')}&page={pagenum}"
    for attempt in range(3):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response is None:
                log.warning("No response for '%s' page %d", keyword, pagenum)
                time.sleep(10)
                continue

            status = response.status
            if status == 429:
                # A 429 mid-session means Kasada re-challenged us. Let the challenge
                # JS solve in place and reload, same as the warm-up — don't just wait
                # and re-navigate (which keeps fetching fresh challenges).
                log.warning("429 on '%s' page %d — re-solving challenge in place", keyword, pagenum)
                if clear_kasada_challenge(page, url, label=f"'{keyword}' p{pagenum}", max_cycles=3):
                    html = page.content()
                    if len(html) > 5000:
                        return html
                time.sleep(_jitter(20))
                continue

            if status != 200:
                log.warning("HTTP %d on '%s' page %d", status, keyword, pagenum)
                time.sleep(10)
                continue

            # Wait for product links to appear
            try:
                page.wait_for_selector('a[href*="/dp/"]', timeout=10000)
            except Exception:
                pass  # some pages may load differently

            html = page.content()
            if len(html) > 5000:
                return html
            else:
                log.warning("Page too short (%d chars) for '%s' page %d", len(html), keyword, pagenum)
                time.sleep(10)

        except Exception as e:
            log.error("CDP fetch error on '%s' page %d (attempt %d): %s", keyword, pagenum, attempt + 1, e)
            time.sleep(15)

    return None

# ── Parse product ranks from HTML ─────────────────────────────────────────
def parse_ranks(html: str, pagenum: int) -> list:
    """
    Extract product ranks from JSON-LD ItemList (primary method).
    Falls back to href scraping if JSON-LD not found.
    Returns list of {rank, url_path}.
    """
    results = []

    # Primary: parse schema.org ItemList embedded in JSON-LD
    blocks = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                        html, re.DOTALL)
    for block in blocks:
        try:
            data = json.loads(block)
        except Exception:
            continue
        items = None
        if data.get("@type") == "ItemList":
            items = data.get("itemListElement", [])
        elif data.get("@type") == "CollectionPage":
            main = data.get("mainEntity", {})
            if main.get("@type") == "ItemList":
                items = main.get("itemListElement", [])
        if not items:
            continue
        for item in items:
            pos = item.get("position")
            product = item.get("item", {})
            url = product.get("url", "")
            if pos and url:
                path = url.replace("https://www.chewy.com", "")
                results.append({"rank": pos, "url_path": path})

    if results:
        return results

    # Fallback: href scraping (less reliable on Chewy's React pages)
    log.debug("No JSON-LD product list found, falling back to href scraping")
    links = re.findall(r'href="(/[^"]+/dp/\d+[^"]*)"', html)
    seen = {}
    for link in links:
        base = link.split("?")[0]
        if base not in seen:
            seen[base] = len(seen) + 1
    base_rank = (pagenum - 1) * 20
    return [{"rank": base_rank + pos, "url_path": path} for path, pos in seen.items()]

# ── Classify a result as a Purrfect Portal product ────────────────────────
def classify_product(url_path: str) -> str | None:
    path_lower = url_path.lower()
    if BRAND_SLUG not in path_lower:
        return None
    for name, slug in PRODUCTS.items():
        if slug in path_lower:
            return name
    slug_part = path_lower.split("/dp/")[0].lstrip("/")
    return slug_part.replace("-", " ").title()[:40]

# ── Run one keyword ────────────────────────────────────────────────────────
def track_keyword(cdp_page, keyword: str) -> dict:
    """Returns {product_name: rank} for all PP products found in top results."""
    found = {}
    for pnum in range(1, PAGES_TO_SCAN + 1):
        html = fetch_search_page_cdp(cdp_page, keyword, pnum)
        if not html:
            log.warning("No HTML for '%s' page %d — stopping", keyword, pnum)
            break

        results = parse_ranks(html, pnum)
        log.info("  '%s' page %d: %d unique product links", keyword, pnum, len(results))

        for item in results:
            product = classify_product(item["url_path"])
            if product and product not in found:
                found[product] = item["rank"]
                log.info("    FOUND '%s' at rank %d", product, item["rank"])

        if pnum < PAGES_TO_SCAN:
            _human_dwell(cdp_page, DELAY_BETWEEN_PAGES * 0.6, DELAY_BETWEEN_PAGES * 1.4)

    return found

# ── Dashboard HTML update ──────────────────────────────────────────────────
def _spark(values: list) -> str:
    if len(values) < 2:
        return ""
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
    diff = yesterday - today
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

    today_iso     = datetime.now().strftime("%Y-%m-%d")
    yesterday_iso = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    today_data    = history.get(today_iso, {})
    yesterday_data = history.get(yesterday_iso, {})

    recent_dates = sorted(history.keys())[-7:]

    kw_products: dict = {}
    for kw in KEYWORDS:
        kw_products[kw] = {}
        for d in recent_dates:
            day_kw = history.get(d, {}).get(kw, {})
            for prod, rank in day_kw.items():
                kw_products[kw].setdefault(prod, [None] * len(recent_dates))
                idx = recent_dates.index(d)
                kw_products[kw][prod][idx] = rank

    rows_html = ""
    for kw in KEYWORDS:
        products = kw_products.get(kw, {})
        if not products:
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
    log.info("Using Chrome CDP on port %d", DEBUG_PORT)
    log.info("=" * 60)

    history = load_history()

    if today_iso in history and len(history[today_iso]) >= len(KEYWORDS):
        log.info("Already ran today — skipping. Delete %s entry from rank_history.json to re-run.", today_iso)
        return

    # ── Import playwright (only when needed) ──────────────────────────────
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("playwright not installed — run: pip install playwright && playwright install chromium")
        sys.exit(1)

    # ── Start Chrome if needed ─────────────────────────────────────────────
    chrome_proc = ensure_chrome_running()
    if chrome_proc is None and not _is_chrome_cdp_ready():
        log.error("Could not start Chrome with CDP. Aborting.")
        sys.exit(1)

    we_opened_chrome = chrome_proc is not None

    # ── Connect via CDP ────────────────────────────────────────────────────
    log.info("Connecting to Chrome via CDP...")
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # Strip the automation fingerprint before any page loads.
        # navigator.webdriver===true is the single biggest bot tell Kasada checks.
        try:
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = window.chrome || { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            """)
        except Exception as e:
            log.debug("Could not add init script: %s", e)

        page = context.new_page()

        # Warm up — Kasada serves a "429 + JS challenge" on first contact with a
        # fresh session. The browser must EXECUTE the challenge JS (a proof-of-work),
        # receive its clearance cookie, then a reload succeeds. So a first-hit 429 is
        # expected: we give the challenge time to solve and reload, rather than
        # aborting. Only a 429 that survives several solve-and-reload cycles is a real
        # block.
        log.info("Warming up on Chewy homepage (solving Kasada challenge if served)...")
        warm_ok = clear_kasada_challenge(page, "https://www.chewy.com/", label="homepage")

        if not warm_ok:
            log.error("Could not clear Kasada challenge on homepage after retries — "
                      "treating as a hard block. Aborting cleanly; will retry next schedule.")
            page.close()
            browser.close()
            if we_opened_chrome and chrome_proc:
                chrome_proc.terminate()
            sys.exit(2)

        # Human-like settle + light scroll on the homepage before searching
        _human_dwell(page, 4, 7)

        today_results = history.get(today_iso, {})

        for i, keyword in enumerate(KEYWORDS):
            log.info("Searching: '%s' (%d/%d)", keyword, i + 1, len(KEYWORDS))
            found = track_keyword(page, keyword)
            today_results[keyword] = found
            if not found:
                log.info("  -> No Purrfect Portal products found in top %d", PAGES_TO_SCAN * 20)
            else:
                for prod, rank in found.items():
                    log.info("  -> %-35s  rank #%d", prod, rank)

            if i < len(KEYWORDS) - 1:
                wait = _jitter(DELAY_BETWEEN_KEYWORDS)
                log.info("  Waiting %ds before next keyword...", int(wait))
                time.sleep(wait)

        page.close()
        browser.close()

    history[today_iso] = today_results
    save_history(history)
    log.info("Saved rank history (%d days total)", len(history))

    update_rank_section(history)
    git_push()

    # Close Chrome if we opened it
    if we_opened_chrome and chrome_proc:
        log.info("Closing Chrome (we opened it)")
        chrome_proc.terminate()

    log.info("Done.")

if __name__ == "__main__":
    main()
