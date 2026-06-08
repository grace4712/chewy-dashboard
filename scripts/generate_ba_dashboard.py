"""
generate_ba_dashboard.py
========================
Reads brand_analytics_data.json (multi-week format) and writes
amazon_sqr_dashboard.html with all data + trends embedded as JavaScript.

Usage:
    python scripts/generate_ba_dashboard.py
"""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(r"C:\Users\retai\OneDrive\Desktop\Claude Code")
DATA_FILE   = PROJECT_DIR / "scripts" / "brand_analytics_data.json"
DASHBOARD   = PROJECT_DIR / "amazon_sqr_dashboard.html"


def safe(v):
    if isinstance(v, str):
        return v.replace('"', "'").replace("\\", "")
    return v


def compute_trends(weeks):
    """Join weeks by query, compute WoW deltas and series data."""
    n = len(weeks)
    by_query = {}
    for i, week in enumerate(weeks):
        for r in week["rows"]:
            q = r["q"]
            if q not in by_query:
                by_query[q] = [None] * n
            by_query[q][i] = r

    trends = []
    for q, series in by_query.items():
        present = [r for r in series if r is not None]
        if len(present) < 2:
            continue
        latest = series[-1]
        prev   = next((r for r in reversed(series[:-1]) if r is not None), None)
        if not latest or not prev:
            continue

        def delta(field):
            return round((latest.get(field) or 0) - (prev.get(field) or 0), 1)

        trends.append({
            "q":        safe(q),
            "vol":      latest["vol"],
            "action":   latest.get("action", "monitor"),
            "cat":      latest.get("cat", "other"),
            "is":       latest["impr_shr"],
            "cs":       latest["clk_shr"],
            "ps":       latest["pur_shr"],
            "cb":       latest["clk_brd"],
            "pb":       latest["pur_brd"],
            "dI":       delta("impr_shr"),
            "dC":       delta("clk_shr"),
            "dP":       delta("pur_shr"),
            "is_s":     [r["impr_shr"] if r else None for r in series],
            "cs_s":     [r["clk_shr"]  if r else None for r in series],
            "ps_s":     [r["pur_shr"]  if r else None for r in series],
        })

    # Rising = purchase share up most WoW (meaningful volume)
    rising  = sorted([t for t in trends if t["dP"] > 3  and t["vol"] >= 50 and t["cb"] >= 3], key=lambda x: -x["dP"])[:20]
    falling = sorted([t for t in trends if t["dP"] < -3 and t["vol"] >= 50 and t["cb"] >= 3], key=lambda x:  x["dP"])[:20]
    return trends, rising, falling


def generate(stored: dict) -> str:
    weeks  = stored["weeks"]   # sorted oldest → newest
    n_wks  = len(weeks)
    latest = weeks[-1]
    prev   = weeks[-2] if n_wks >= 2 else None

    rows = latest["rows"]
    s    = latest["summary"]
    m    = latest["meta"]

    week_labels = [w["meta"]["week"] for w in weeks]

    # ── Overall share series (for trend line chart) ──────────────────────────
    wk_impr = [round(w["summary"]["impr_share_pct"], 2) for w in weeks]
    wk_clk  = [round(w["summary"]["clk_share_pct"],  2) for w in weeks]
    wk_pur  = [round(w["summary"]["pur_share_pct"],  2) for w in weeks]

    # ── WoW deltas for KPI cards ─────────────────────────────────────────────
    def wow(field):
        if not prev:
            return 0
        return round(s[field] - prev["summary"][field], 2)

    d_impr = wow("impr_share_pct")
    d_clk  = wow("clk_share_pct")
    d_pur  = wow("pur_share_pct")

    def delta_html(d, unit="%"):
        if abs(d) < 0.05:
            return f'<span style="color:var(--muted);font-size:11px;">→ 0{unit}</span>'
        color = "var(--green)" if d > 0 else "var(--red)"
        arrow = "↑" if d > 0 else "↓"
        return f'<span style="color:{color};font-size:11px;">{arrow} {abs(d):.1f}{unit} WoW</span>'

    # ── Query-level trends ───────────────────────────────────────────────────
    trends, rising, falling = compute_trends(weeks)

    # ── Compact JS rows (latest week only for main tables) ───────────────────
    js_rows = json.dumps([{
        "q":     safe(r["q"]),
        "score": r["score"],
        "vol":   r["vol"],
        "cat":   r["cat"],
        "action":r["action"],
        "active":r["active"],
        "is":    r["impr_shr"],
        "cs":    r["clk_shr"],
        "cas":   r["cart_shr"],
        "ps":    r["pur_shr"],
        "ib":    r["impr_brd"],
        "cb":    r["clk_brd"],
        "pb":    r["pur_brd"],
        "mp":    r["mkt_price_clk"],
        "bp":    r["brd_price_clk"],
        "pg":    r["price_gap"],
        "bcr":   r["brd_clk_rate"],
        "bpr":   r["brd_pur_rate"],
    } for r in rows], ensure_ascii=True)

    # Trend rows (compact) for JS
    js_trends = json.dumps([{
        "q":   t["q"], "vol": t["vol"], "action": t["action"], "cat": t["cat"],
        "is":  t["is"], "cs": t["cs"], "ps": t["ps"],
        "cb":  t["cb"], "pb": t["pb"],
        "dI":  t["dI"], "dC": t["dC"], "dP": t["dP"],
        "isS": t["is_s"], "csS": t["cs_s"], "psS": t["ps_s"],
    } for t in trends], ensure_ascii=True)

    # ── Top 15 chart ─────────────────────────────────────────────────────────
    top15   = [r for r in rows if r["score"] <= 15]
    t15_lbl = json.dumps([r["q"]          for r in top15])
    t15_is  = json.dumps([r["impr_shr"]   for r in top15])
    t15_cs  = json.dumps([r["clk_shr"]    for r in top15])
    t15_cas = json.dumps([r["cart_shr"]   for r in top15])
    t15_ps  = json.dumps([r["pur_shr"]    for r in top15])

    # ── Defend chart ─────────────────────────────────────────────────────────
    defend  = sorted([r for r in rows if r["action"]=="defend"], key=lambda x: -x["vol"])[:12]
    d_lbl   = json.dumps([r["q"]        for r in defend])
    d_is    = json.dumps([r["impr_shr"] for r in defend])
    d_cs    = json.dumps([r["clk_shr"]  for r in defend])
    d_ps    = json.dumps([r["pur_shr"]  for r in defend])

    ac         = s.get("action_counts", {})
    n_defend   = ac.get("defend", 0)
    n_invest   = ac.get("invest", 0)
    n_fixpage  = ac.get("fix_page", 0)
    impr_shr   = s["impr_share_pct"]
    clk_shr    = s["clk_share_pct"]
    pur_shr    = s["pur_share_pct"]
    pur_brd    = s["total_pur_brd"]
    n_active   = s["active_queries"]
    n_queries  = s["total_queries"]

    # weighted market conversion rates (for funnel chart)
    active = [r for r in rows if r["active"]]
    def wavg(num_field, wt_field, rows_list):
        tot_wt = sum(r.get(wt_field, 0) for r in rows_list if r.get(wt_field, 0) > 0)
        return round(sum(r.get(num_field,0) * r.get(wt_field,0) for r in rows_list if r.get(wt_field,0)>0) / max(1, tot_wt), 2)

    mkt_clk_r  = wavg("mkt_clk_rate", "vol", active)
    mkt_pur_r  = wavg("mkt_pur_rate", "vol", active)
    brd_clk_r  = wavg("brd_clk_rate",  "clk_brd", [r for r in active if r["clk_brd"]>0])
    brd_cart_r = wavg("brd_cart_rate", "clk_brd", [r for r in active if r["clk_brd"]>0])
    brd_pur_r  = wavg("brd_pur_rate",  "clk_brd", [r for r in active if r["clk_brd"]>0])
    cart_shr_avg = round(sum(r["cart_shr"]*r["vol"] for r in active) / max(1, sum(r["vol"] for r in active)), 1)

    wk_labels_js = json.dumps(week_labels)
    wk_impr_js   = json.dumps(wk_impr)
    wk_clk_js    = json.dumps(wk_clk)
    wk_pur_js    = json.dumps(wk_pur)

    week_label  = m.get("week","")
    date_start  = m.get("date_start","")
    date_end    = m.get("date_end","")

    # ── Rising/falling tables ─────────────────────────────────────────────────
    def trend_row(t, delta_field, sign):
        d = t[delta_field]
        color = "var(--green)" if sign > 0 else "var(--red)"
        arrow = "↑" if sign > 0 else "↓"
        return (f'<tr><td style="font-weight:500">{t["q"]}</td>'
                f'<td>{t["vol"]:,}</td>'
                f'<td><span class="pill pill-{"green" if t["ps"]>=15 else "yellow" if t["ps"]>=5 else "muted"}">{t["ps"]:.1f}%</span></td>'
                f'<td style="color:{color};font-weight:700">{arrow} {abs(d):.1f}%</td>'
                f'<td>{t["cb"]}</td></tr>')

    rising_tbody  = "".join(trend_row(t,"dP", 1) for t in rising)  or '<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:20px;">Not enough data yet — add more weeks</td></tr>'
    falling_tbody = "".join(trend_row(t,"dP",-1) for t in falling) or '<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:20px;">No significant declines this week</td></tr>'

    # ── Build HTML ────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Purrfect Portal — Amazon Market Intelligence</title>
<script>
(function(){{
  var k=sessionStorage.getItem('pp_auth');
  if(k!=='ok'){{
    var p=prompt('Enter password to continue:');
    if(p!=='Ilovecats'){{document.documentElement.innerHTML='<body style="background:#0f1117;color:#8892a4;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;font-size:16px;">Access denied.</body>';return;}}
    sessionStorage.setItem('pp_auth','ok');
  }}
}})();
</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{--bg:#0f1117;--card:#1a1d27;--card2:#1f2335;--border:#2a2d3a;--accent:#6c63ff;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;--blue:#3b82f6;--orange:#f97316;--text:#e2e8f0;--muted:#8892a4;}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;min-height:100vh;}}
.header{{padding:18px 32px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;background:var(--bg);z-index:100;}}
.header h1{{font-size:18px;font-weight:700;color:#fff;}}
.hm{{display:flex;align-items:center;gap:12px;}}
.badge{{background:var(--card);border:1px solid var(--border);padding:4px 12px;border-radius:6px;color:var(--muted);font-size:11px;}}
.abadge{{background:rgba(255,153,0,.15);border:1px solid rgba(255,153,0,.3);color:#ff9900;padding:4px 12px;border-radius:6px;font-size:11px;font-weight:700;}}
.tabs{{display:flex;gap:2px;padding:0 32px;background:var(--bg);border-bottom:1px solid var(--border);overflow-x:auto;}}
.tab{{padding:12px 18px;font-size:13px;font-weight:500;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;white-space:nowrap;flex-shrink:0;}}
.tab:hover{{color:var(--text);}} .tab.active{{color:#fff;border-bottom-color:var(--accent);}}
.cnt{{display:inline-block;margin-left:5px;padding:1px 7px;border-radius:10px;font-size:11px;}}
.content{{padding:24px 32px;display:none;flex-direction:column;gap:20px;}} .content.active{{display:flex;}}
.kpi-grid{{display:grid;gap:14px;grid-template-columns:repeat(5,1fr);}}
.kpi{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 18px;}}
.kpi .lbl{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;}}
.kpi .val{{font-size:26px;font-weight:700;color:#fff;line-height:1;}}
.kpi .sub{{font-size:11px;margin-top:5px;color:var(--muted);}}
.kpi .wow{{margin-top:4px;}}
.kpi.good .val{{color:var(--green);}} .kpi.info .val{{color:var(--blue);}}
.ab{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;}}
.ac{{border-radius:10px;padding:16px 18px;cursor:pointer;border:1px solid;transition:opacity .15s;}}
.ac:hover{{opacity:.85;}} .ac .icon{{font-size:22px;margin-bottom:8px;}}
.ac .n{{font-size:32px;font-weight:800;line-height:1;margin-bottom:4px;}}
.ac .lbl{{font-size:13px;font-weight:700;margin-bottom:3px;}} .ac .desc{{font-size:11px;opacity:.7;line-height:1.5;}}
.ac-g{{background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.25);color:var(--green);}}
.ac-b{{background:rgba(59,130,246,.08);border-color:rgba(59,130,246,.25);color:var(--blue);}}
.ac-r{{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.25);color:var(--red);}}
.ac-y{{background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.25);color:var(--yellow);}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:20px;}}
.card h3{{font-size:14px;font-weight:600;color:#fff;margin-bottom:14px;}}
.ch{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;}} .ch h3{{margin:0;}}
.col2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
.col3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;}}
table{{width:100%;border-collapse:collapse;}}
th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.4px;padding:7px 10px;text-align:left;border-bottom:1px solid var(--border);cursor:pointer;user-select:none;white-space:nowrap;}}
th:hover{{color:var(--text);}} td{{padding:8px 10px;border-bottom:1px solid var(--border);font-size:13px;}}
tr:last-child td{{border-bottom:none;}} tr:hover td{{background:rgba(255,255,255,.025);}}
.pill{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap;}}
.pill-green{{background:rgba(34,197,94,.12);color:var(--green);}} .pill-yellow{{background:rgba(245,158,11,.12);color:var(--yellow);}}
.pill-red{{background:rgba(239,68,68,.12);color:var(--red);}} .pill-blue{{background:rgba(59,130,246,.12);color:var(--blue);}}
.pill-purple{{background:rgba(108,99,255,.12);color:var(--accent);}} .pill-orange{{background:rgba(249,115,22,.12);color:var(--orange);}}
.pill-muted{{background:rgba(136,146,164,.1);color:var(--muted);}}
.ib{{background:var(--card2);border-radius:8px;padding:14px 18px;font-size:12px;color:var(--muted);line-height:1.7;border-left:3px solid;}}
.ib-g{{border-color:var(--green);}} .ib-b{{border-color:var(--blue);}} .ib-r{{border-color:var(--red);}} .ib-y{{border-color:var(--yellow);}}
.ib strong{{color:var(--text);}}
.sb{{background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:7px 12px;color:var(--text);font-size:13px;width:240px;outline:none;}}
.sb:focus{{border-color:var(--accent);}} .sb::placeholder{{color:var(--muted);}}
select.sb{{width:auto;cursor:pointer;}}
</style>
</head>
<body>

<div class="header">
  <div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px;">Purrfect Portal · {n_wks} weeks</div>
    <h1>Amazon Market Intelligence — Brand Analytics</h1>
  </div>
  <div class="hm">
    <span class="badge">Latest: {week_label} &nbsp;·&nbsp; {date_start} – {date_end}</span>
    <span class="abadge">Brand Analytics</span>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('overview')">Overview</div>
  <div class="tab" onclick="showTab('trends')">📊 Trends <span class="cnt" style="background:rgba(108,99,255,.2);color:var(--accent);">{n_wks}w</span></div>
  <div class="tab" onclick="showTab('top15')">Top 15 Queries</div>
  <div class="tab" onclick="showTab('defend')">🛡 Defend <span class="cnt" style="background:rgba(34,197,94,.2);color:var(--green);">{n_defend}</span></div>
  <div class="tab" onclick="showTab('invest')">📈 Invest <span class="cnt" style="background:rgba(59,130,246,.2);color:var(--blue);">{n_invest}</span></div>
  <div class="tab" onclick="showTab('fixpage')">⚠️ Fix CVR <span class="cnt" style="background:rgba(239,68,68,.2);color:var(--red);">{n_fixpage}</span></div>
  <div class="tab" onclick="showTab('allq')">All Queries</div>
</div>

<!-- ═══════════ OVERVIEW ═══════════ -->
<div id="tab-overview" class="content active">
  <div class="kpi-grid">
    <div class="kpi info">
      <div class="lbl">Impression Share</div>
      <div class="val">{impr_shr:.1f}%</div>
      <div class="wow">{delta_html(d_impr)}</div>
      <div class="sub">of total market impressions</div>
    </div>
    <div class="kpi good">
      <div class="lbl">Click Share</div>
      <div class="val">{clk_shr:.1f}%</div>
      <div class="wow">{delta_html(d_clk)}</div>
      <div class="sub">{round(clk_shr/impr_shr,1) if impr_shr else 0}× your impression share</div>
    </div>
    <div class="kpi good">
      <div class="lbl">Purchase Share</div>
      <div class="val">{pur_shr:.1f}%</div>
      <div class="wow">{delta_html(d_pur)}</div>
      <div class="sub">{round(pur_shr/impr_shr,1) if impr_shr else 0}× your impression share</div>
    </div>
    <div class="kpi">
      <div class="lbl">Brand Purchases</div>
      <div class="val">{pur_brd}</div>
      <div class="sub">this week (tracked queries)</div>
    </div>
    <div class="kpi">
      <div class="lbl">Active Queries</div>
      <div class="val">{n_active}</div>
      <div class="sub">of {n_queries} total tracked</div>
    </div>
  </div>

  <div class="ib ib-g">
    <strong>Key Insight:</strong> Your impression share is {impr_shr:.1f}% but click share is {clk_shr:.1f}% — <strong>{round(clk_shr/impr_shr,1) if impr_shr else 0}× higher</strong>. Shoppers click Purrfect Portal at well above-market rates. The primary growth lever is <strong>increasing impression share</strong> — more visibility, more sales. WoW: impression share {delta_html(d_impr)}, purchase share {delta_html(d_pur)}.
  </div>

  <div>
    <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px;">Action Plan — Click to explore</div>
    <div class="ab">
      <div class="ac ac-g" onclick="showTab('defend')"><div class="icon">🛡</div><div class="n">{n_defend}</div><div class="lbl">Defend</div><div class="desc">Already winning. Protect with PPC, keep bids healthy.</div></div>
      <div class="ac ac-b" onclick="showTab('invest')"><div class="icon">📈</div><div class="n">{n_invest}</div><div class="lbl">Invest More</div><div class="desc">Low impression share but proven ROI when seen. Scale up.</div></div>
      <div class="ac ac-r" onclick="showTab('fixpage')"><div class="icon">⚠️</div><div class="n">{n_fixpage}</div><div class="lbl">Fix Conversion</div><div class="desc">Getting clicks but 0% purchase share. Review listing/price.</div></div>
      <div class="ac ac-y" onclick="showTab('trends')"><div class="icon">📊</div><div class="n">{n_wks}w</div><div class="lbl">View Trends</div><div class="desc">3-week share trend, rising and falling queries.</div></div>
    </div>
  </div>

  <div class="col2">
    <div class="card">
      <h3>Market Funnel — Click→Cart→Purchase Rate</h3>
      <div style="position:relative;height:220px;"><canvas id="funnelChart"></canvas></div>
      <p style="font-size:11px;color:var(--muted);margin-top:10px;">Blue = market average &nbsp;·&nbsp; Purple = Purrfect Portal</p>
    </div>
    <div class="card">
      <h3>Your Share at Each Funnel Stage</h3>
      <div style="position:relative;height:220px;"><canvas id="shareChart"></canvas></div>
      <p style="font-size:11px;color:var(--muted);margin-top:10px;">Across all active queries weighted by search volume</p>
    </div>
  </div>

  <div class="card">
    <h3>Top 15 Queries — Purchase Share (latest week)</h3>
    <div style="position:relative;height:280px;"><canvas id="top15PurChart"></canvas></div>
  </div>
</div>

<!-- ═══════════ TRENDS ═══════════ -->
<div id="tab-trends" class="content">

  <div class="card">
    <h3>3-Week Share Trend — Overall</h3>
    <div style="position:relative;height:260px;"><canvas id="trendLineChart"></canvas></div>
  </div>

  <div class="col2">
    <div class="card">
      <div class="ch">
        <h3 style="color:var(--green);">📈 Rising — Gaining Purchase Share WoW</h3>
        <span style="font-size:11px;color:var(--muted);">≥+3% · ≥50 vol · ≥3 clicks</span>
      </div>
      <table>
        <thead><tr>
          <th>Search Query</th><th>Volume</th><th>Purch Share</th><th>WoW Change</th><th>Clicks</th>
        </tr></thead>
        <tbody>{rising_tbody}</tbody>
      </table>
    </div>
    <div class="card">
      <div class="ch">
        <h3 style="color:var(--red);">📉 Falling — Losing Purchase Share WoW</h3>
        <span style="font-size:11px;color:var(--muted);">≤-3% · ≥50 vol · ≥3 clicks</span>
      </div>
      <table>
        <thead><tr>
          <th>Search Query</th><th>Volume</th><th>Purch Share</th><th>WoW Change</th><th>Clicks</th>
        </tr></thead>
        <tbody>{falling_tbody}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="ch">
      <h3>All Queries — Week-over-Week Deltas</h3>
      <input class="sb" type="text" placeholder="Filter…" oninput="filterTrends(this.value)" id="trendFilter">
    </div>
    <div style="overflow-x:auto;max-height:500px;overflow-y:auto;">
    <table id="trendTable">
      <thead style="position:sticky;top:0;background:var(--card);z-index:10;"><tr>
        <th onclick="tSort(0)">Search Query</th>
        <th onclick="tSort(1)">Volume</th>
        <th onclick="tSort(2)">Impr%</th>
        <th onclick="tSort(3)">ΔImpr</th>
        <th onclick="tSort(4)">Click%</th>
        <th onclick="tSort(5)">ΔClick</th>
        <th onclick="tSort(6)">Purch%</th>
        <th onclick="tSort(7)">ΔPurch</th>
        <th onclick="tSort(8)">Clicks</th>
        <th>Action</th>
      </tr></thead>
      <tbody id="trendTbody"></tbody>
    </table>
    </div>
    <div id="trendCount" style="font-size:11px;color:var(--muted);padding:10px 0;"></div>
  </div>
</div>

<!-- ═══════════ TOP 15 ═══════════ -->
<div id="tab-top15" class="content">
  <div class="ib ib-b">
    <strong>Amazon's top 15 queries most associated with your brand.</strong> Score #1 = highest brand relevance. The chart shows your share at each funnel stage: Impression → Click → Cart → Purchase. High click share vs impression share = compelling listing. Drop from click to purchase = detail page or price friction.
  </div>
  <div class="card">
    <div class="ch"><h3>Funnel Shares — Top 15 Queries</h3><span style="font-size:11px;color:var(--muted);">Lower score = higher brand relevance rank</span></div>
    <div style="position:relative;height:400px;"><canvas id="top15FunnelChart"></canvas></div>
  </div>
  <div class="card">
    <h3>Top 15 — Detailed Breakdown</h3>
    <div style="overflow-x:auto;"><table id="top15Table">
      <thead><tr>
        <th onclick="sortT('top15Table',0)">#</th><th onclick="sortT('top15Table',1)">Query</th>
        <th onclick="sortT('top15Table',2)">Vol</th><th onclick="sortT('top15Table',3)">Impr%</th>
        <th onclick="sortT('top15Table',4)">Click%</th><th onclick="sortT('top15Table',5)">Cart%</th>
        <th onclick="sortT('top15Table',6)">Purch%</th><th onclick="sortT('top15Table',7)">Mkt $</th>
        <th onclick="sortT('top15Table',8)">Your $</th><th onclick="sortT('top15Table',9)">Price Gap</th>
        <th>Verdict</th>
      </tr></thead>
      <tbody id="top15tbody"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══════════ DEFEND ═══════════ -->
<div id="tab-defend" class="content">
  <div class="ib ib-g">
    <strong>These {n_defend} queries are where Purrfect Portal is already winning</strong> — strong impression AND purchase share. Never lose position here. Keep PPC bids competitive, maintain listing quality, watch for competitor price moves. Any drop in impression share directly costs revenue.
  </div>
  <div class="card">
    <h3>Defend — Share at Each Stage</h3>
    <div style="position:relative;height:300px;"><canvas id="defendChart"></canvas></div>
  </div>
  <div class="card">
    <h3>All Defend Queries</h3>
    <div style="overflow-x:auto;"><table id="defendTable">
      <thead><tr>
        <th onclick="sortT('defendTable',0)">#</th><th onclick="sortT('defendTable',1)">Query</th>
        <th onclick="sortT('defendTable',2)">Vol</th><th onclick="sortT('defendTable',3)">Impr%</th>
        <th onclick="sortT('defendTable',4)">Click%</th><th onclick="sortT('defendTable',5)">Purch%</th>
        <th onclick="sortT('defendTable',6)">Brd Purch</th><th onclick="sortT('defendTable',7)">Mkt $</th>
        <th onclick="sortT('defendTable',8)">Your $</th><th onclick="sortT('defendTable',9)">ΔPurch WoW</th>
      </tr></thead>
      <tbody id="defendtbody"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══════════ INVEST ═══════════ -->
<div id="tab-invest" class="content">
  <div class="ib ib-b">
    <strong>These {n_invest} queries convert well when shoppers see your product</strong> — but your impression share is too low. Increase PPC bids and/or improve organic rank to capture more of this demand. Even +5% impression share on "pet door" (3,320 searches/week) = meaningful revenue.
  </div>
  <div class="card">
    <h3>Invest — Scale Up These Queries</h3>
    <div style="overflow-x:auto;"><table id="investTable">
      <thead><tr>
        <th onclick="sortT('investTable',0)">#</th><th onclick="sortT('investTable',1)">Query</th>
        <th onclick="sortT('investTable',2)">Vol</th><th onclick="sortT('investTable',3)">Impr%</th>
        <th onclick="sortT('investTable',4)">Click%</th><th onclick="sortT('investTable',5)">Purch%</th>
        <th onclick="sortT('investTable',6)">Brd Purch</th><th onclick="sortT('investTable',7)">Mkt $</th>
        <th onclick="sortT('investTable',8)">Your $</th><th>Action</th>
      </tr></thead>
      <tbody id="investtbody"></tbody>
    </table></div>
  </div>
</div>

<!-- ═══════════ FIX CVR ═══════════ -->
<div id="tab-fixpage" class="content">
  <div class="ib ib-r">
    <strong>These {n_fixpage} queries are getting real clicks but 0% purchase share.</strong> Shoppers are clicking — but not buying. Each has a hypothesis in the table. Check: which ASIN is shown, your price vs. market, and whether your main image/title matches what searchers expect.
  </div>
  <div class="card">
    <h3>Fix Conversion — Clicks With No Purchases</h3>
    <div style="overflow-x:auto;"><table id="fixpageTable">
      <thead><tr>
        <th onclick="sortT('fixpageTable',0)">#</th><th onclick="sortT('fixpageTable',1)">Query</th>
        <th onclick="sortT('fixpageTable',2)">Vol</th><th onclick="sortT('fixpageTable',3)">Impr%</th>
        <th onclick="sortT('fixpageTable',4)">Click%</th><th onclick="sortT('fixpageTable',5)">Brand Clicks</th>
        <th onclick="sortT('fixpageTable',6)">Purch%</th><th onclick="sortT('fixpageTable',7)">Mkt $</th>
        <th onclick="sortT('fixpageTable',8)">Your $</th><th>Hypothesis</th>
      </tr></thead>
      <tbody id="fixpagetbody"></tbody>
    </table></div>
  </div>
  <div class="col3">
    <div class="ib ib-r"><strong>Immediate:</strong> Pull the search term in Amazon Ads. Check which ASIN is serving for this query — is it the most relevant product?</div>
    <div class="ib ib-y"><strong>Price check:</strong> If your price is more than $5 above market median, test a coupon or temporary reduction.</div>
    <div class="ib ib-b"><strong>Listing audit:</strong> Search each query on Amazon as a customer. Does your main image answer what they need?</div>
  </div>
</div>

<!-- ═══════════ ALL QUERIES ═══════════ -->
<div id="tab-allq" class="content">
  <div class="card">
    <div class="ch">
      <h3>All Queries ({n_queries} total)</h3>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
        <select id="filterAction" class="sb" onchange="renderAllTable()">
          <option value="">All actions</option><option value="defend">Defend</option>
          <option value="invest">Invest</option><option value="fix_page">Fix CVR</option>
          <option value="monitor">Monitor</option>
        </select>
        <select id="filterCat" class="sb" onchange="renderAllTable()">
          <option value="">All categories</option><option value="core">Core (cat door)</option>
          <option value="branded">Branded</option><option value="adjacent">Adjacent</option>
          <option value="other">Other</option>
        </select>
        <input class="sb" type="text" placeholder="Filter queries…" oninput="renderAllTable()" id="allFilter">
      </div>
    </div>
    <div style="overflow-x:auto;max-height:600px;overflow-y:auto;">
    <table id="allTable">
      <thead style="position:sticky;top:0;background:var(--card);z-index:10;"><tr>
        <th onclick="allSort(0)">#</th><th onclick="allSort(1)">Query</th>
        <th onclick="allSort(2)">Vol</th><th onclick="allSort(3)">Cat</th>
        <th onclick="allSort(4)">Impr%</th><th onclick="allSort(5)">Click%</th>
        <th onclick="allSort(6)">Cart%</th><th onclick="allSort(7)">Purch%</th>
        <th onclick="allSort(8)">Clks</th><th onclick="allSort(9)">Purch</th>
        <th onclick="allSort(10)">Mkt $</th><th onclick="allSort(11)">Your $</th>
        <th onclick="allSort(12)">Gap</th><th onclick="allSort(13)">Action</th>
      </tr></thead>
      <tbody id="alltbody"></tbody>
    </table>
    </div>
    <div id="allCount" style="font-size:11px;color:var(--muted);padding:10px 0;"></div>
  </div>
</div>

<script>
const ROWS   = {js_rows};
const TRENDS = {js_trends};
const WK_LABELS = {wk_labels_js};
const WK_IMPR   = {wk_impr_js};
const WK_CLK    = {wk_clk_js};
const WK_PUR    = {wk_pur_js};
const T15_LABELS = {t15_lbl};
const T15_IS={t15_is}; const T15_CS={t15_cs}; const T15_CAS={t15_cas}; const T15_PS={t15_ps};
const D_LBL={d_lbl}; const D_IS={d_is}; const D_CS={d_cs}; const D_PS={d_ps};

// ── Tab switching ──────────────────────────────────────────────────────────
function showTab(name) {{
  const ids=['overview','trends','top15','defend','invest','fixpage','allq'];
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active',ids[i]===name));
  document.querySelectorAll('.content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  if(name==='allq') renderAllTable();
  if(name==='top15') renderTop15Table();
  if(name==='defend') renderDefendTable();
  if(name==='invest') renderInvestTable();
  if(name==='fixpage') renderFixpageTable();
  if(name==='trends') renderTrendTable();
}}

// ── Pill helpers ───────────────────────────────────────────────────────────
function sP(v){{
  if(v===0||v===null||v===undefined) return '<span class="pill pill-muted">—</span>';
  const c=v>=25?'pill-green':v>=10?'pill-yellow':'pill-muted';
  return `<span class="pill ${{c}}">${{v.toFixed(1)}}%</span>`;
}}
function dP(d){{
  if(d===null||d===undefined||Math.abs(d)<0.1) return '<span style="color:var(--muted)">—</span>';
  const c=d>0?'var(--green)':'var(--red)'; const a=d>0?'↑':'↓';
  return `<span style="color:${{c}};font-weight:600;">${{a}}${{Math.abs(d).toFixed(1)}}%</span>`;
}}
function ppP(gap){{
  if(gap===null||gap===undefined) return '—';
  const c=gap>5?'pill-red':gap<-5?'pill-green':'pill-muted';
  const s=gap>0?'+':'';
  return `<span class="pill ${{c}}">${{s}}$${{gap.toFixed(2)}}</span>`;
}}
function aP(a){{
  const m={{defend:'pill-green',invest:'pill-blue',fix_page:'pill-red',fix_listing:'pill-orange',monitor:'pill-muted'}};
  const l={{defend:'Defend',invest:'Invest',fix_page:'Fix CVR',fix_listing:'Fix CTR',monitor:'Monitor'}};
  return `<span class="pill ${{m[a]||'pill-muted'}}">${{l[a]||a}}</span>`;
}}
function cP(c){{
  const m={{core:'pill-purple',branded:'pill-blue',adjacent:'pill-yellow',other:'pill-muted'}};
  return `<span class="pill ${{m[c]||'pill-muted'}}">${{c}}</span>`;
}}

// ── Table renderers ────────────────────────────────────────────────────────
function getHyp(r){{
  if(r.pg&&r.pg>5) return 'Price above market median';
  const q=r.q.toLowerCase();
  if(q.includes('large')||q.includes('xl')) return 'Size mismatch — check ASIN shown';
  if(q.includes('bedroom')||q.includes('hollow')||q.includes('built')) return 'Specific use case — check listing copy';
  if(q.includes('gato')||q.includes('puerta')||q.includes('para')) return 'Non-English — consider Spanish A+';
  if(q.includes('hobbit')||q.includes('fairy')||q.includes('cat portal')) return 'Niche style — listing match needed';
  return 'Listing/product-fit review needed';
}}

function getTrend(q){{
  const t=TRENDS.find(x=>x.q===q);
  if(!t) return '';
  return dP(t.dP);
}}

function renderTop15Table(){{
  const t15=ROWS.filter(r=>r.score<=15).sort((a,b)=>a.score-b.score);
  document.getElementById('top15tbody').innerHTML=t15.map(r=>`
    <tr><td style="color:var(--muted)">#${{r.score}}</td><td style="font-weight:600">${{r.q}}</td>
    <td>${{r.vol.toLocaleString()}}</td><td>${{sP(r.is)}}</td><td>${{sP(r.cs)}}</td>
    <td>${{sP(r.cas)}}</td><td>${{sP(r.ps)}}</td>
    <td>${{r.mp?'$'+r.mp.toFixed(2):'—'}}</td><td>${{r.bp?'$'+r.bp.toFixed(2):'—'}}</td>
    <td>${{ppP(r.pg)}}</td><td>${{aP(r.action)}}</td></tr>`).join('');
}}

function renderDefendTable(){{
  const rows=ROWS.filter(r=>r.action==='defend').sort((a,b)=>b.vol-a.vol);
  document.getElementById('defendtbody').innerHTML=rows.map(r=>`
    <tr><td style="color:var(--muted)">#${{r.score}}</td><td style="font-weight:600">${{r.q}}</td>
    <td>${{r.vol.toLocaleString()}}</td><td>${{sP(r.is)}}</td><td>${{sP(r.cs)}}</td><td>${{sP(r.ps)}}</td>
    <td style="font-weight:600;color:var(--green)">${{r.pb}}</td>
    <td>${{r.mp?'$'+r.mp.toFixed(2):'—'}}</td><td>${{r.bp?'$'+r.bp.toFixed(2):'—'}}</td>
    <td>${{getTrend(r.q)}}</td></tr>`).join('');
}}

function renderInvestTable(){{
  const rows=ROWS.filter(r=>r.action==='invest').sort((a,b)=>b.vol-a.vol);
  document.getElementById('investtbody').innerHTML=rows.map(r=>`
    <tr><td style="color:var(--muted)">#${{r.score}}</td><td style="font-weight:600">${{r.q}}</td>
    <td>${{r.vol.toLocaleString()}}</td><td><span class="pill pill-red">${{r.is.toFixed(1)}}%↑</span></td>
    <td>${{sP(r.cs)}}</td><td>${{sP(r.ps)}}</td>
    <td style="font-weight:600;color:var(--blue)">${{r.pb}}</td>
    <td>${{r.mp?'$'+r.mp.toFixed(2):'—'}}</td><td>${{r.bp?'$'+r.bp.toFixed(2):'—'}}</td>
    <td><span class="pill pill-blue">Raise bids / improve rank</span></td></tr>`).join('');
}}

function renderFixpageTable(){{
  const rows=ROWS.filter(r=>r.action==='fix_page').sort((a,b)=>b.cb-a.cb);
  document.getElementById('fixpagetbody').innerHTML=rows.map(r=>`
    <tr><td style="color:var(--muted)">#${{r.score}}</td><td style="font-weight:600">${{r.q}}</td>
    <td>${{r.vol.toLocaleString()}}</td><td>${{sP(r.is)}}</td><td>${{sP(r.cs)}}</td>
    <td style="font-weight:600;color:var(--yellow)">${{r.cb}}</td>
    <td><span class="pill pill-red">0%</span></td>
    <td>${{r.mp?'$'+r.mp.toFixed(2):'—'}}</td><td>${{r.bp?'$'+r.bp.toFixed(2):'—'}}</td>
    <td style="font-size:12px;color:var(--yellow)">${{getHyp(r)}}</td></tr>`).join('');
}}

// ── Trend table ────────────────────────────────────────────────────────────
let tSortCol=1, tSortDir=-1;
function tSort(col){{
  if(tSortCol===col) tSortDir*=-1; else {{tSortCol=col;tSortDir=-1;}}
  renderTrendTable();
}}
function filterTrends(q){{ renderTrendTable(); }}
function renderTrendTable(){{
  const q=(document.getElementById('trendFilter')?.value||'').toLowerCase();
  let rows=TRENDS.filter(r=>!q||r.q.toLowerCase().includes(q));
  const cols=['q','vol','is','dI','cs','dC','ps','dP','cb','action'];
  const col=cols[tSortCol]||'vol';
  rows.sort((a,b)=>{{
    const av=a[col],bv=b[col];
    if(av===null||av===undefined) return 1;
    if(bv===null||bv===undefined) return -1;
    return (typeof av==='string'?av.localeCompare(bv):av-bv)*tSortDir;
  }});
  document.getElementById('trendCount').textContent=`Showing ${{rows.length}} queries`;
  document.getElementById('trendTbody').innerHTML=rows.slice(0,300).map(r=>`
    <tr><td style="font-weight:500;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${{r.q}}">${{r.q}}</td>
    <td>${{r.vol.toLocaleString()}}</td><td>${{sP(r.is)}}</td><td>${{dP(r.dI)}}</td>
    <td>${{sP(r.cs)}}</td><td>${{dP(r.dC)}}</td><td>${{sP(r.ps)}}</td><td>${{dP(r.dP)}}</td>
    <td>${{r.cb}}</td><td>${{aP(r.action)}}</td></tr>`).join('');
}}

// ── All-queries table ──────────────────────────────────────────────────────
let aSortCol=2, aSortDir=-1;
function allSort(col){{
  if(aSortCol===col) aSortDir*=-1; else {{aSortCol=col;aSortDir=-1;}}
  renderAllTable();
}}
function renderAllTable(){{
  const q=(document.getElementById('allFilter')?.value||'').toLowerCase();
  const act=document.getElementById('filterAction')?.value||'';
  const cat=document.getElementById('filterCat')?.value||'';
  let rows=ROWS.filter(r=>(!q||r.q.toLowerCase().includes(q))&&(!act||r.action===act)&&(!cat||r.cat===cat));
  const cols=['score','q','vol','cat','is','cs','cas','ps','cb','pb','mp','bp','pg','action'];
  const col=cols[aSortCol]||'vol';
  rows.sort((a,b)=>{{
    const av=a[col],bv=b[col];
    if(av===null||av===undefined) return 1;
    if(bv===null||bv===undefined) return -1;
    return (typeof av==='string'?av.localeCompare(bv):av-bv)*aSortDir;
  }});
  document.getElementById('allCount').textContent=`Showing ${{rows.length}} of ${{ROWS.length}} queries`;
  document.getElementById('alltbody').innerHTML=rows.slice(0,300).map(r=>`
    <tr><td style="color:var(--muted)">#${{r.score}}</td>
    <td style="font-weight:500;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${{r.q}}">${{r.q}}</td>
    <td>${{r.vol.toLocaleString()}}</td><td>${{cP(r.cat)}}</td>
    <td>${{sP(r.is)}}</td><td>${{sP(r.cs)}}</td><td>${{sP(r.cas)}}</td><td>${{sP(r.ps)}}</td>
    <td>${{r.cb}}</td><td>${{r.pb}}</td>
    <td>${{r.mp?'$'+r.mp.toFixed(2):'—'}}</td><td>${{r.bp?'$'+r.bp.toFixed(2):'—'}}</td>
    <td>${{ppP(r.pg)}}</td><td>${{aP(r.action)}}</td></tr>`).join('');
}}

function sortT(tableId, col){{
  const tbody=document.querySelector('#'+tableId+' tbody');
  if(!tbody) return;
  const rows=Array.from(tbody.querySelectorAll('tr'));
  const dir=sortT._dir=-(sortT._dir||1);
  rows.sort((a,b)=>{{
    const av=a.cells[col]?.textContent.trim()||'';
    const bv=b.cells[col]?.textContent.trim()||'';
    const an=parseFloat(av.replace(/[^0-9.\\-]/g,'')), bn=parseFloat(bv.replace(/[^0-9.\\-]/g,''));
    return (isNaN(an)||isNaN(bn)?av.localeCompare(bv):an-bn)*dir;
  }});
  rows.forEach(r=>tbody.appendChild(r));
}}

// ── Charts ─────────────────────────────────────────────────────────────────
Chart.defaults.color='#8892a4'; Chart.defaults.borderColor='#2a2d3a';
const co=(r,g,b,a)=>`rgba(${{r}},${{g}},${{b}},${{a}})`;

// Funnel chart
new Chart(document.getElementById('funnelChart'),{{type:'bar',data:{{
  labels:['Click Rate','Cart Add Rate','Purchase Rate'],
  datasets:[
    {{label:'Market Average',data:[{mkt_clk_r},{0},{mkt_pur_r}],backgroundColor:co(59,130,246,.5),borderColor:'#3b82f6',borderWidth:1,borderRadius:4}},
    {{label:'Purrfect Portal',data:[{brd_clk_r},{brd_cart_r},{brd_pur_r}],backgroundColor:co(108,99,255,.5),borderColor:'#6c63ff',borderWidth:1,borderRadius:4}}
  ]}},options:{{responsive:true,maintainAspectRatio:false,
  plugins:{{legend:{{labels:{{color:'#e2e8f0',font:{{size:11}}}}}}}},
  scales:{{x:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4'}}}},
           y:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',callback:v=>v+'%'}}}}}}
}}}});

// Share stages bar
new Chart(document.getElementById('shareChart'),{{type:'bar',data:{{
  labels:['Impression','Click','Cart Add','Purchase'],
  datasets:[{{label:'Brand Share %',data:[{impr_shr:.1f},{clk_shr:.1f},{cart_shr_avg},{pur_shr:.1f}],
    backgroundColor:[co(59,130,246,.6),co(34,197,94,.6),co(245,158,11,.6),co(108,99,255,.6)],
    borderColor:['#3b82f6','#22c55e','#f59e0b','#6c63ff'],borderWidth:1,borderRadius:4}}]
  }},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
  scales:{{x:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',font:{{size:10}}}}}},
           y:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',callback:v=>v+'%'}},max:20}}}}
}}}});

// Top 15 purchase share bar
new Chart(document.getElementById('top15PurChart'),{{type:'bar',data:{{
  labels:T15_LABELS,
  datasets:[{{label:'Purchase Share %',data:T15_PS,
    backgroundColor:T15_PS.map(v=>v>=30?co(34,197,94,.7):v>=15?co(245,158,11,.7):co(59,130,246,.5)),
    borderColor:T15_PS.map(v=>v>=30?'#22c55e':v>=15?'#f59e0b':'#3b82f6'),borderWidth:1,borderRadius:4}}]
  }},options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
  scales:{{x:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',callback:v=>v+'%'}}}},
           y:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#e2e8f0',font:{{size:11}}}}}}}}
}}}});

// 3-week trend line
new Chart(document.getElementById('trendLineChart'),{{type:'line',data:{{
  labels:WK_LABELS,
  datasets:[
    {{label:'Impression Share %',data:WK_IMPR,borderColor:'#3b82f6',backgroundColor:co(59,130,246,.1),tension:.3,pointRadius:5,pointBackgroundColor:'#3b82f6'}},
    {{label:'Click Share %',    data:WK_CLK, borderColor:'#22c55e',backgroundColor:co(34,197,94,.1), tension:.3,pointRadius:5,pointBackgroundColor:'#22c55e'}},
    {{label:'Purchase Share %', data:WK_PUR, borderColor:'#6c63ff',backgroundColor:co(108,99,255,.1),tension:.3,pointRadius:5,pointBackgroundColor:'#6c63ff'}}
  ]}},options:{{responsive:true,maintainAspectRatio:false,
  plugins:{{legend:{{labels:{{color:'#e2e8f0',font:{{size:12}}}}}}}},
  scales:{{x:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#e2e8f0',font:{{size:13}}}}}},
           y:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',callback:v=>v+'%'}}}}}}
}}}});

// Top 15 grouped funnel
new Chart(document.getElementById('top15FunnelChart'),{{type:'bar',data:{{
  labels:T15_LABELS,
  datasets:[
    {{label:'Impression %',data:T15_IS, backgroundColor:co(59,130,246,.6), borderColor:'#3b82f6',borderWidth:1,borderRadius:3}},
    {{label:'Click %',     data:T15_CS, backgroundColor:co(34,197,94,.6),  borderColor:'#22c55e',borderWidth:1,borderRadius:3}},
    {{label:'Cart %',      data:T15_CAS,backgroundColor:co(245,158,11,.6), borderColor:'#f59e0b',borderWidth:1,borderRadius:3}},
    {{label:'Purchase %',  data:T15_PS, backgroundColor:co(108,99,255,.6), borderColor:'#6c63ff',borderWidth:1,borderRadius:3}},
  ]}},options:{{responsive:true,maintainAspectRatio:false,
  plugins:{{legend:{{labels:{{color:'#e2e8f0',font:{{size:11}}}}}}}},
  scales:{{x:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',font:{{size:10}}}}}},
           y:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',callback:v=>v+'%'}}}}}}
}}}});

// Defend chart
new Chart(document.getElementById('defendChart'),{{type:'bar',data:{{
  labels:D_LBL,
  datasets:[
    {{label:'Impression %',data:D_IS,backgroundColor:co(59,130,246,.6),borderColor:'#3b82f6',borderWidth:1,borderRadius:3}},
    {{label:'Click %',     data:D_CS,backgroundColor:co(34,197,94,.6), borderColor:'#22c55e',borderWidth:1,borderRadius:3}},
    {{label:'Purchase %',  data:D_PS,backgroundColor:co(108,99,255,.6),borderColor:'#6c63ff',borderWidth:1,borderRadius:3}},
  ]}},options:{{responsive:true,maintainAspectRatio:false,
  plugins:{{legend:{{labels:{{color:'#e2e8f0',font:{{size:11}}}}}}}},
  scales:{{x:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',font:{{size:10}}}}}},
           y:{{grid:{{color:'#2a2d3a'}},ticks:{{color:'#8892a4',callback:v=>v+'%'}}}}}}
}}}});

// Init on load
renderTop15Table(); renderDefendTable(); renderInvestTable(); renderFixpageTable();
</script>
</body>
</html>"""


def main():
    if not DATA_FILE.exists():
        print(f"Run build_brand_analytics.py first. Expected: {DATA_FILE}")
        sys.exit(1)
    stored = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    # Support both old single-week and new multi-week format
    if "weeks" not in stored:
        stored = {"weeks": [stored]}

    # Pre-compute brd_cart_rate if missing (older data)
    for week in stored["weeks"]:
        for r in week["rows"]:
            if "brd_cart_rate" not in r:
                r["brd_cart_rate"] = round(r.get("cart_brd", 0) / r.get("clk_brd", 1) * 100, 2) if r.get("clk_brd", 0) > 0 else 0
            if "mkt_clk_rate" not in r:
                r["mkt_clk_rate"] = 0
            if "mkt_pur_rate" not in r:
                r["mkt_pur_rate"] = 0

    html = generate(stored)
    DASHBOARD.write_text(html, encoding="utf-8")
    weeks = stored["weeks"]
    print(f"Dashboard written to: {DASHBOARD}")
    print(f"Weeks included: {[w['meta']['week'] for w in weeks]}")


if __name__ == "__main__":
    main()
