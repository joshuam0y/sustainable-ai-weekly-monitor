import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape
from zoneinfo import ZoneInfo

from db import get_conn

EASTERN = ZoneInfo("America/New_York")

CATEGORY_LABELS = {
    "conversation": "General Conversation",
    "scope3_ai_audit": "Scope 3 AI Audits",
    "scope3_cloud": "Scope 3 Cloud Emissions",
    "northeastern": "Northeastern Mentions",
}
CATEGORY_ORDER = ["northeastern", "scope3_ai_audit", "scope3_cloud", "conversation"]

WATCHLIST = [
    "Northeastern", "MIT", "Stanford", "Harvard", "Microsoft", "Amazon",
    "Meta", "OpenAI", "Anthropic", "Nvidia", "Apple", "IBM", "Salesforce", "AWS",
]

OUT_DIR = "docs"
DISPLAY_WINDOW_DAYS = 30
NEW_WINDOW_HOURS = 26


def parse_dt(value, fallback):
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return fallback


def resolve_dt(row):
    """The article's real publish date when available, else when we first saw it."""
    dt = None
    if row["published"]:
        try:
            dt = parsedate_to_datetime(row["published"])
        except (TypeError, ValueError):
            dt = None
    if dt is None and row["first_seen"]:
        try:
            dt = datetime.fromisoformat(row["first_seen"])
        except ValueError:
            dt = None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_articles(conn, since):
    rows = conn.execute(
        "SELECT * FROM articles WHERE first_seen >= ? ORDER BY first_seen DESC", (since.isoformat(),)
    ).fetchall()
    return rows


def last_run_at(conn):
    row = conn.execute("SELECT run_at FROM runs ORDER BY run_at DESC LIMIT 1").fetchone()
    return row["run_at"] if row else None


def article_card(row, is_new, dt):
    badge = '<span class="badge">NEW</span> ' if is_new else ""
    src = escape(row["source"] or "Unknown source")
    local_dt = dt.astimezone(EASTERN) if dt else None
    date_txt = escape(local_dt.strftime("%b %d, %Y")) if local_dt else "Unknown date"
    date_attr = local_dt.strftime("%Y-%m-%d") if local_dt else ""
    ai_summary = row["ai_summary"]
    summary_html = f'<div class="card-summary">{escape(ai_summary)}</div>' if ai_summary else ""
    return f"""
    <li class="card" data-date="{date_attr}">
      <div class="card-title">{badge}<a href="{escape(row['link'])}" target="_blank" rel="noopener">{escape(row['title'])}</a></div>
      <div class="card-meta">{src} &middot; {date_txt}</div>
      {summary_html}
    </li>"""


def trending_orgs(rows):
    counts = Counter()
    for row in rows:
        text = row["title"] or ""
        for org in WATCHLIST:
            if org.lower() in text.lower():
                counts[org] += 1
    return counts.most_common(8)


def render():
    conn = get_conn()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=DISPLAY_WINDOW_DAYS)
    rows = load_articles(conn, since)
    run_at = last_run_at(conn)

    new_cutoff = now - timedelta(hours=NEW_WINDOW_HOURS)
    new_rows = [r for r in rows if parse_dt(r["first_seen"], since) >= new_cutoff]
    new_links = {r["link"] for r in new_rows}

    by_category = {cat: [] for cat in CATEGORY_ORDER}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row)

    trending = trending_orgs(rows)

    tabs = []
    panels = []

    def add_tab(tab_id, label, tab_rows, active=False):
        dated = [(resolve_dt(r), r) for r in tab_rows]
        dated.sort(key=lambda pair: pair[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        cards = "".join(article_card(r, r["link"] in new_links, dt) for dt, r in dated)
        active_cls = " active" if active else ""
        display = "block" if active else "none"
        tabs.append(f'<button class="tab-btn{active_cls}" data-tab="{tab_id}" onclick="showTab(\'{tab_id}\', this)">{escape(label)} ({len(tab_rows)})</button>')
        panels.append(f"""
        <div class="tab-panel" id="tab-{tab_id}" style="display:{display}">
          <ul class="card-list">{cards or '<li class="empty">Nothing here yet.</li>'}<li class="empty filter-empty" style="display:none">No articles match this filter.</li></ul>
        </div>""")

    add_tab("new", "New Since Last Check", new_rows, active=True)
    for cat in CATEGORY_ORDER:
        add_tab(cat, CATEGORY_LABELS.get(cat, cat), by_category.get(cat, []))

    trending_html = ""
    if trending:
        chips = "".join(f'<span class="chip">{escape(name)} &middot; {count}</span>' for name, count in trending)
        trending_html = f"""
        <div class="trending">
          <strong>Trending:</strong> {chips}
        </div>"""

    now_eastern = now.astimezone(EASTERN)
    today_str = now_eastern.strftime("%Y-%m-%d")
    d3_str = (now_eastern - timedelta(days=3)).strftime("%Y-%m-%d")
    d7_str = (now_eastern - timedelta(days=7)).strftime("%Y-%m-%d")
    d30_str = (now_eastern - timedelta(days=30)).strftime("%Y-%m-%d")
    filter_html = f"""
    <div class="filter-row">
      <label for="dateFilter">Filter by date:</label>
      <select id="dateFilter" onchange="applyFilters()">
        <option value="">All time</option>
        <option value="{today_str}">Today</option>
        <option value="{d3_str}">Last 3 days</option>
        <option value="{d7_str}">Last 7 days</option>
        <option value="{d30_str}">Last 30 days</option>
      </select>
    </div>"""

    search_html = """
    <div class="search-row">
      <input type="text" id="searchBox" placeholder="Search titles, sources, and summaries (e.g. water usage, Northeastern, Scope 3)..." oninput="applyFilters()">
      <div id="searchStatus" class="search-status"></div>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sustainable AI Weekly Monitor</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f4f7f5; color: #1b1f1c; margin: 0; padding: 0; }}
  header {{ background: #1b4332; color: #fff; padding: 20px 20px; }}
  header h1 {{ margin: 0 0 4px 0; font-size: 1.5em; }}
  header p {{ margin: 0; color: #cde5d6; font-size: 0.9em; }}
  main {{ max-width: 900px; margin: 0 auto; padding: 18px 20px 30px; }}
  .trending {{ background: #fff; border: 1px solid #e0e6e2; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; font-size: 0.9em; }}
  .chip {{ display: inline-block; background: #e4efe8; color: #1b4332; padding: 3px 9px; border-radius: 16px; margin: 2px 4px; font-size: 0.85em; }}
  .filter-row {{ margin-bottom: 12px; font-size: 0.9em; }}
  .filter-row select {{ margin-left: 6px; padding: 5px 8px; border-radius: 6px; border: 1px solid #cfe0d6; background: #fff; color: #1b4332; }}
  .search-row {{ margin-bottom: 12px; }}
  .search-row input {{ width: 100%; padding: 9px 12px; border-radius: 6px; border: 1px solid #cfe0d6; background: #fff; color: #1b1f1c; font-size: 0.9em; }}
  .search-row input:focus {{ outline: 2px solid #2e7d55; }}
  .search-status {{ font-size: 0.8em; color: #6b7a70; margin-top: 6px; }}
  .tabs {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }}
  .tab-btn {{ background: #fff; border: 1px solid #cfe0d6; color: #1b4332; padding: 8px 12px; border-radius: 6px; font-size: 0.85em; cursor: pointer; }}
  .tab-btn.active {{ background: #2e7d55; color: #fff; border-color: #2e7d55; }}
  .tab-panel {{ max-height: 68vh; overflow-y: auto; border: 1px solid #e0e6e2; border-radius: 8px; background: #fff; padding: 4px 12px; }}
  .card-list {{ list-style: none; margin: 0; padding: 0; }}
  .card {{ border-bottom: 1px solid #eef1ee; padding: 12px 2px; }}
  .card:last-child {{ border-bottom: none; }}
  .card-title a {{ color: #14532d; text-decoration: none; font-weight: 600; }}
  .card-title a:hover {{ text-decoration: underline; }}
  .card-meta {{ font-size: 0.78em; color: #6b7a70; margin-top: 3px; }}
  .card-summary {{ font-size: 0.88em; color: #384038; margin-top: 5px; line-height: 1.4; }}
  .badge {{ background: #2e7d55; color: #fff; font-size: 0.68em; padding: 2px 6px; border-radius: 4px; vertical-align: middle; }}
  .empty {{ color: #7c887f; padding: 14px 0; }}
  footer {{ text-align: center; font-size: 0.78em; color: #7c887f; padding: 16px; }}
</style>
</head>
<body>
<header>
  <h1>Sustainable AI Weekly Monitor</h1>
  <p>AI &amp; sustainability news, Scope 3 emissions audits, and cloud computing carbon reporting &middot; last updated {escape(run_at or "never")} UTC</p>
</header>
<main>
{trending_html}
{search_html}
{filter_html}
<div class="tabs">{''.join(tabs)}</div>
{''.join(panels)}
</main>
<footer>Automatically refreshed on a schedule via GitHub Actions. Sources: Google News RSS, summarized with Gemini.</footer>
<script>
function activeTabId() {{
  var btn = document.querySelector('.tab-btn.active');
  return btn ? btn.getAttribute('data-tab') : 'new';
}}

function showTab(id, btn) {{
  document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  btn.classList.add('active');
  applyFilters();
}}

function applyFilters() {{
  var cutoff = document.getElementById('dateFilter').value;
  var query = document.getElementById('searchBox').value.trim().toLowerCase();
  var tokens = query.split(/\\s+/).filter(Boolean);
  var searching = tokens.length > 0;
  var active = activeTabId();

  document.querySelector('.tabs').style.display = searching ? 'none' : 'flex';

  var totalMatches = 0;
  document.querySelectorAll('.tab-panel').forEach(function(panel) {{
    var isNewTab = panel.id === 'tab-new';

    if (searching && isNewTab) {{
      panel.style.display = 'none';
      return;
    }}

    var cards = panel.querySelectorAll('.card');
    var visible = 0;
    cards.forEach(function(card) {{
      var dateOK = !cutoff || (card.dataset.date && card.dataset.date >= cutoff);
      var text = searching ? card.textContent.toLowerCase() : '';
      var searchOK = !searching || tokens.every(function(t) {{ return text.indexOf(t) !== -1; }});
      var show = dateOK && searchOK;
      card.style.display = show ? '' : 'none';
      if (show) {{ visible++; if (searching) totalMatches++; }}
    }});

    var emptyMsg = panel.querySelector('.filter-empty');
    if (searching) {{
      // Hide categories with zero matches entirely -- an empty box next to
      // real results reads as broken, not as "nothing here."
      panel.style.display = visible > 0 ? 'block' : 'none';
      if (emptyMsg) emptyMsg.style.display = 'none';
    }} else {{
      panel.style.display = (panel.id === 'tab-' + active) ? 'block' : 'none';
      if (emptyMsg) emptyMsg.style.display = (cards.length > 0 && visible === 0) ? 'block' : 'none';
    }}
  }});

  var status = document.getElementById('searchStatus');
  if (searching) {{
    status.textContent = totalMatches > 0
      ? 'Showing ' + totalMatches + ' result' + (totalMatches === 1 ? '' : 's') + ' for "' + query + '" across all topics'
      : 'No articles match "' + query + '"';
  }} else {{
    status.textContent = '';
  }}
}}
</script>
</body>
</html>"""

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w") as f:
        f.write(html)

    conn.close()
    print(f"Rendered report with {len(rows)} articles ({len(new_rows)} new)")


if __name__ == "__main__":
    render()
