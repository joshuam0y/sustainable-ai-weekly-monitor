import html as html_lib
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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

SPIKE_CURRENT_DAYS = 7
SPIKE_BASELINE_DAYS = 23  # the 23 days before the current window
SPIKE_MIN_MENTIONS = 2
SPIKE_RATIO_THRESHOLD = 1.75


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


def _mention_counts(titles):
    counts = Counter()
    for title in titles:
        for org in WATCHLIST:
            if org.lower() in title.lower():
                counts[org] += 1
    return counts


def spike_orgs(conn, now):
    """
    Flags organizations mentioned meaningfully more this week than their own
    recent baseline rate. A flat all-time mention count (the old "Trending"
    box) just rewards whichever org is talked about most overall -- it can't
    tell you what actually *changed*, which is specifically what this
    project's brief asks for ("recognizing anything new or unusual").
    """
    current_start = now - timedelta(days=SPIKE_CURRENT_DAYS)
    baseline_start = current_start - timedelta(days=SPIKE_BASELINE_DAYS)

    current_titles = [
        r["title"] for r in conn.execute(
            "SELECT title FROM articles WHERE first_seen >= ?", (current_start.isoformat(),)
        ).fetchall()
    ]
    baseline_titles = [
        r["title"] for r in conn.execute(
            "SELECT title FROM articles WHERE first_seen >= ? AND first_seen < ?",
            (baseline_start.isoformat(), current_start.isoformat()),
        ).fetchall()
    ]

    current_counts = _mention_counts(current_titles)
    baseline_counts = _mention_counts(baseline_titles)

    results = []
    for org, current in current_counts.items():
        if current < SPIKE_MIN_MENTIONS:
            continue
        baseline = baseline_counts.get(org, 0)
        if baseline == 0:
            results.append({"org": org, "count": current, "ratio": None})  # brand new this week
            continue
        ratio = (current / SPIKE_CURRENT_DAYS) / (baseline / SPIKE_BASELINE_DAYS)
        if ratio >= SPIKE_RATIO_THRESHOLD:
            results.append({"org": org, "count": current, "ratio": ratio})

    results.sort(key=lambda r: r["ratio"] if r["ratio"] is not None else float("inf"), reverse=True)
    return results[:6]


def spike_data_is_immature(conn, now):
    """
    True until this project has scraped for a full baseline+current window.
    Before that, every org looks "NEW" simply because there's no older
    first_seen data to compare against yet -- not because it's actually
    novel. Worth flagging explicitly rather than silently overclaiming.
    """
    row = conn.execute("SELECT MIN(first_seen) m FROM articles").fetchone()
    if not row or not row["m"]:
        return True
    earliest = parse_dt(row["m"], now)
    return (now - earliest).days < (SPIKE_CURRENT_DAYS + SPIKE_BASELINE_DAYS)


def summary_stats(rows):
    sources = {r["source"] for r in rows if r["source"]}
    dates = [d for d in (resolve_dt(r) for r in rows) if d]
    return {
        "total": len(rows),
        "sources": len(sources),
        "oldest": min(dates) if dates else None,
        "off_topic": sum(1 for r in rows if r["is_core_topic"] == 0),
    }


def _article_row_html(row, is_new, dt):
    cat = row["category"]
    cat_label = CATEGORY_LABELS.get(cat, cat)
    src = html_lib.escape(row["source"] or "Unknown source")
    local_dt = dt.astimezone(EASTERN) if dt else None
    date_txt = html_lib.escape(local_dt.strftime("%b %d, %Y")) if local_dt else "Unknown date"
    date_attr = local_dt.strftime("%Y-%m-%d") if local_dt else ""
    ai_summary = row["ai_summary"]
    summary_html = f'<span class="row-summary">{html_lib.escape(ai_summary)}</span>' if ai_summary else ""
    new_tag = '<span class="tag tag-new">New</span>' if is_new else ""
    is_off_topic = row["is_core_topic"] == 0
    off_topic_tag = '<span class="tag tag-offtopic">Off-topic?</span>' if is_off_topic else ""
    search_blob = html_lib.escape((row["title"] + " " + (row["source"] or "") + " " + (ai_summary or "")).lower())

    return f"""
    <a class="article-row" href="{html_lib.escape(row['link'])}" target="_blank" rel="noopener"
       data-category="{cat}" data-date="{date_attr}" data-new="{'1' if is_new else '0'}"
       data-offtopic="{'1' if is_off_topic else '0'}" data-search="{search_blob}">
      <span class="article-row-body">
        <span class="article-row-title">{new_tag}{off_topic_tag}{html_lib.escape(row['title'])}</span>
        <span class="article-row-meta">{src} &middot; {date_txt} &middot; {html_lib.escape(cat_label)}</span>
        {summary_html}
      </span>
    </a>
    """


STYLE = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@700;800&family=Source+Sans+3:wght@400;500;600;700&display=swap');

  :root {
    color-scheme: light;
    --bg: #EEF1F6; --surface: #ffffff; --surface-2: #E3E8F1;
    --ink: #10182B; --ink-dim: #4B5670; --ink-muted: #5F6988;
    --border: rgba(16,24,43,0.09); --shadow: 0 1px 2px rgba(16,24,43,.05), 0 10px 26px rgba(16,24,43,.07);
    --accent: #2A4FDE; --accent-bg: rgba(42,79,222,0.10);
    --up: #95470B; --up-bg: rgba(149,71,11,0.12);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --bg: #0A0F1C; --surface: #171F35; --surface-2: #212B45;
      --ink: #E9EDF7; --ink-dim: #AEB7CC; --ink-muted: #8D98B2;
      --border: rgba(255,255,255,0.08); --shadow: 0 1px 2px rgba(0,0,0,.35), 0 12px 30px rgba(0,0,0,.4);
      --accent: #7C97FF; --accent-bg: rgba(124,151,255,0.16);
      --up: #EF9758; --up-bg: rgba(239,151,88,0.16);
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #0A0F1C; --surface: #171F35; --surface-2: #212B45;
    --ink: #E9EDF7; --ink-dim: #AEB7CC; --ink-muted: #8D98B2;
    --border: rgba(255,255,255,0.08); --shadow: 0 1px 2px rgba(0,0,0,.35), 0 12px 30px rgba(0,0,0,.4);
    --accent: #7C97FF; --accent-bg: rgba(124,151,255,0.16);
    --up: #EF9758; --up-bg: rgba(239,151,88,0.16);
  }
  * { box-sizing: border-box; }
  html { font-size: 17px; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--ink); line-height: 1.55;
    font-family: 'Source Sans 3', -apple-system, "Segoe UI", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  h1, h2, .brand { font-family: 'Manrope', 'Source Sans 3', sans-serif; }
  a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {
    outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px;
  }

  header.site {
    display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap;
    max-width: 1220px; margin: 0 auto; padding: 26px 24px 20px; border-bottom: 1px solid var(--border);
    flex: none; width: 100%;
  }
  .brand { font-size: 27px; font-weight: 800; letter-spacing: -0.02em; margin: 0; }
  .tagline { margin: 8px 0 0; color: var(--ink-dim); font-size: 15px; max-width: 58ch; line-height: 1.6; }
  .header-meta { font-size: 13px; color: var(--ink-muted); margin-top: 9px; font-weight: 600; }
  .theme-toggle {
    background: var(--surface); color: var(--ink); border: 1px solid var(--border);
    border-radius: 8px; padding: 9px 15px; font-size: 14px; font-weight: 600; cursor: pointer; font-family: inherit;
    box-shadow: var(--shadow);
  }
  .theme-toggle:hover { border-color: var(--accent); color: var(--accent); }

  .layout {
    max-width: 1220px; margin: 0 auto; padding: 22px 24px 40px; display: flex; gap: 28px; align-items: flex-start;
    width: 100%;
  }

  .sidebar { width: 280px; flex: none; position: sticky; top: 20px; }
  .sidebar h2 { font-size: 13px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; margin: 21px 0 10px; color: var(--ink-muted); }
  .sidebar h2:first-child { margin-top: 0; }
  .side-search {
    width: 100%; padding: 10px 13px; border: 1px solid var(--border); border-radius: 9px;
    background: var(--surface); color: var(--ink); font-size: 15px; font-family: inherit;
  }
  .side-search:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }
  .side-select {
    width: 100%; padding: 9px 11px; border-radius: 9px; border: 1px solid var(--border);
    background: var(--surface); color: var(--ink); font-size: 14.5px; font-family: inherit;
  }
  .side-toggle { display: flex; align-items: center; gap: 9px; font-size: 14.5px; color: var(--ink-dim); margin-top: 12px; font-weight: 500; }
  .side-toggle input { accent-color: var(--accent); width: 17px; height: 17px; }

  .spike-box { font-size: 14px; }
  .spike-chip { display: inline-flex; align-items: center; gap: 5px; margin: 3px 4px 3px 0; }
  .spike-badge { font-size: 11px; font-weight: 700; border-radius: 5px; padding: 2px 7px; color: #fff; background: var(--up); }
  .spike-badge.spike-new { background: var(--ink-muted); }
  .spike-hint { font-size: 12.5px; color: var(--ink-muted); margin-top: 8px; line-height: 1.55; }

  .stat-line { font-size: 14px; color: var(--ink-dim); display: flex; justify-content: space-between; padding: 4px 0; font-weight: 500; }
  .stat-line b { color: var(--ink); font-weight: 700; }

  .sidebar-footer { font-size: 12.5px; color: var(--ink-muted); margin-top: 21px; padding-top: 16px; border-top: 1px solid var(--border); line-height: 1.6; }

  .main { flex: 1; min-width: 0; }

  .how-to {
    display: flex; flex-wrap: wrap; gap: 9px 22px; align-items: center; flex-shrink: 0;
    background: var(--accent-bg); border-radius: 10px; padding: 12px 17px; margin-bottom: 18px;
  }
  .how-to-item { display: flex; align-items: center; gap: 8px; font-size: 14px; color: var(--ink-dim); font-weight: 500; }
  .how-to-num {
    display: inline-flex; align-items: center; justify-content: center; width: 19px; height: 19px;
    border-radius: 50%; background: var(--accent); color: #fff; font-size: 11px; font-weight: 800; flex: none;
  }

  .type-tabs {
    display: flex; gap: 4px; margin-bottom: 15px; border-bottom: 1px solid var(--border);
    overflow-x: auto; -webkit-overflow-scrolling: touch; min-width: 0; max-width: 100%; flex-shrink: 0;
  }
  .type-tab {
    background: none; border: none; border-bottom: 2px solid transparent; padding: 10px 4px; margin-right: 18px;
    font-size: 15px; font-weight: 600; color: var(--ink-dim); cursor: pointer; font-family: inherit;
    white-space: nowrap; flex: none;
  }
  .type-tab:hover { color: var(--ink); }
  .type-tab.on { color: var(--accent); border-bottom-color: var(--accent); }
  .type-tab .count { color: var(--ink-muted); font-weight: 700; margin-left: 4px; }
  #resultCount { font-size: 13.5px; color: var(--ink-muted); margin-bottom: 13px; font-weight: 600; flex-shrink: 0; }

  .article-list { display: flex; flex-direction: column; gap: 9px; }
  .article-row {
    display: flex; align-items: flex-start; gap: 12px; padding: 16px 18px; border-radius: 12px;
    border: 1px solid var(--border); background: var(--surface);
    text-decoration: none; color: inherit; transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease;
  }
  .article-row:hover { box-shadow: var(--shadow); border-color: var(--accent); transform: translateY(-1px); }
  .article-row-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
  .article-row-title { font-size: 17.5px; font-weight: 700; color: var(--ink); min-width: 0; overflow-wrap: break-word; line-height: 1.4; }
  .article-row-meta { font-size: 13.5px; color: var(--ink-dim); min-width: 0; font-weight: 600; }
  .row-summary { font-size: 14.5px; color: var(--ink-dim); line-height: 1.6; min-width: 0; overflow-wrap: break-word; }
  .tag { font-size: 11px; font-weight: 700; border-radius: 5px; padding: 2px 8px; margin-right: 7px; vertical-align: middle; }
  .tag-new { background: var(--accent-bg); color: var(--accent); }
  .tag-offtopic { background: var(--up-bg); color: var(--up); }

  .empty-state { text-align: center; padding: 60px 20px; color: var(--ink-muted); font-size: 15px; flex-shrink: 0; }
  footer.site-footer {
    margin: 18px 0 4px; padding-top: 16px; border-top: 1px solid var(--border);
    font-size: 13px; color: var(--ink-muted); line-height: 1.6; flex-shrink: 0;
  }
  footer.site-footer a { color: var(--accent); }

  /* Contain scrolling to the sidebar/article-list panes on desktop, instead of
     the whole page growing to fit hundreds of cards -- the header and search/
     filters stay put while only the list underneath scrolls. */
  @media (min-width: 761px) {
    html, body { height: 100%; }
    body { display: flex; flex-direction: column; overflow: hidden; }
    .layout { flex: 1 1 auto; min-height: 0; align-items: stretch; }
    .sidebar { order: 2; position: static; overflow-y: auto; padding-right: 6px; }
    .main { order: 1; overflow-y: auto; padding-right: 6px; display: flex; flex-direction: column; min-height: 0; }
    .article-list { flex: 1 1 auto; }
  }

  @media (max-width: 760px) {
    .layout { flex-direction: column; align-items: stretch; }
    .main { min-width: 0; }
    .sidebar { width: 100%; position: static; }
    .article-row-title { font-size: 16px; }
  }
</style>
"""

HOW_TO_HTML = """
<div class="how-to">
  <span class="how-to-item"><span class="how-to-num">1</span>Newest first, updated hourly</span>
  <span class="how-to-item"><span class="how-to-num">2</span>Filter by category, date, or search</span>
  <span class="how-to-item"><span class="how-to-num">3</span>Off-topic toggle (sidebar) reveals hidden mentions</span>
</div>
"""

SCRIPT = """
<script>
function initTheme() {
  const saved = localStorage.getItem('theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  const btn = document.getElementById('themeToggle');
  function label() {
    const cur = document.documentElement.getAttribute('data-theme');
    const dark = cur ? cur === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    btn.textContent = dark ? 'Switch to light' : 'Switch to dark';
  }
  btn.addEventListener('click', function () {
    const cur = document.documentElement.getAttribute('data-theme');
    const dark = cur ? cur === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    label();
  });
  label();
}

let activeCategory = 'all';
function applyFilters() {
  const search = document.getElementById('searchBox').value.trim().toLowerCase();
  const cutoff = document.getElementById('dateFilter').value;
  const onlyNew = document.getElementById('onlyNew').checked;
  const showOffTopic = document.getElementById('showOffTopic').checked;
  let visible = 0;
  document.querySelectorAll('.article-row').forEach(function (row) {
    let show = true;
    if (activeCategory !== 'all' && row.dataset.category !== activeCategory) show = false;
    if (onlyNew && row.dataset.new !== '1') show = false;
    if (!showOffTopic && row.dataset.offtopic === '1') show = false;
    if (cutoff && (!row.dataset.date || row.dataset.date < cutoff)) show = false;
    if (search && row.dataset.search.indexOf(search) === -1) show = false;
    row.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  document.getElementById('resultCount').textContent = visible + ' shown';
  const empty = document.getElementById('emptyState');
  if (empty) empty.style.display = visible === 0 ? 'block' : 'none';
}

document.addEventListener('DOMContentLoaded', function () {
  initTheme();
  document.querySelectorAll('.type-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('.type-tab').forEach(function (p) { p.classList.remove('on'); });
      tab.classList.add('on');
      activeCategory = tab.dataset.category;
      applyFilters();
    });
  });
  document.getElementById('searchBox').addEventListener('input', applyFilters);
  document.getElementById('dateFilter').addEventListener('change', applyFilters);
  document.getElementById('onlyNew').addEventListener('change', applyFilters);
  document.getElementById('showOffTopic').addEventListener('change', applyFilters);
  applyFilters();
});
</script>
"""


def render():
    conn = get_conn()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=DISPLAY_WINDOW_DAYS)
    rows = load_articles(conn, since)
    run_at = last_run_at(conn)

    new_cutoff = now - timedelta(hours=NEW_WINDOW_HOURS)

    counts = {cat: 0 for cat in CATEGORY_ORDER}
    dated_rows = []
    new_count = 0
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
        dt = resolve_dt(row)
        is_new = parse_dt(row["first_seen"], since) >= new_cutoff
        if is_new:
            new_count += 1
        dated_rows.append((dt, row, is_new))
    dated_rows.sort(key=lambda triple: triple[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    spikes = spike_orgs(conn, now)
    stats = summary_stats(rows)

    type_tabs = f'<button type="button" class="type-tab on" data-category="all">All<span class="count">{len(rows)}</span></button>'
    for cat in CATEGORY_ORDER:
        label = CATEGORY_LABELS.get(cat, cat)
        type_tabs += (
            f'<button type="button" class="type-tab" data-category="{cat}">'
            f'{html_lib.escape(label)}<span class="count">{counts.get(cat, 0)}</span></button>'
        )

    rows_html = "".join(_article_row_html(row, is_new, dt) for dt, row, is_new in dated_rows)
    empty_state = (
        '<div class="empty-state" id="emptyState" style="display:none">'
        "No articles match these filters right now &mdash; try clearing the search or picking a different category."
        "</div>"
    )
    if not rows:
        empty_state = '<div class="empty-state" id="emptyState">No articles tracked yet &mdash; check back after the next scheduled scan.</div>'

    spike_html = ""
    if spikes:
        chips = []
        for s in spikes:
            if s["ratio"] is None:
                badge = '<span class="spike-badge spike-new">NEW</span>'
            else:
                badge = f'<span class="spike-badge">{s["ratio"]:.1f}x</span>'
            chips.append(f'<span class="spike-chip">{badge} {html_lib.escape(s["org"])} &middot; {s["count"]}</span>')
        if spike_data_is_immature(conn, now):
            hint = "Still building up baseline history &mdash; \"NEW\" just means no older data to compare against yet."
        else:
            hint = "Mentioned meaningfully more than each org's own recent baseline, not just whoever's talked about most."
        spike_html = f"""
        <h2>Unusual this week</h2>
        <div class="spike-box">{"".join(chips)}<div class="spike-hint">{hint}</div></div>
        """

    stats_html = ""
    if stats["total"]:
        span_days = (now - stats["oldest"]).days if stats["oldest"] else DISPLAY_WINDOW_DAYS
        stats_html = f"""
        <h2>At a glance</h2>
        <div class="stat-line"><span>Articles tracked</span><b>{stats['total']}</b></div>
        <div class="stat-line"><span>Sources</span><b>{stats['sources']}</b></div>
        <div class="stat-line"><span>Days covered</span><b>{span_days}</b></div>
        <div class="stat-line"><span>New since last check</span><b>{new_count}</b></div>
        """
        if stats["off_topic"]:
            stats_html += (
                f'<div class="stat-line"><span>Flagged off-topic</span><b>{stats["off_topic"]}</b></div>'
            )

    now_eastern = now.astimezone(EASTERN)
    today_str = now_eastern.strftime("%Y-%m-%d")
    d3_str = (now_eastern - timedelta(days=3)).strftime("%Y-%m-%d")
    d7_str = (now_eastern - timedelta(days=7)).strftime("%Y-%m-%d")

    generated_at = "never"
    if run_at:
        parsed = parse_dt(run_at, None)
        generated_at = parsed.strftime("%B %-d, %Y at %-I:%M %p UTC") if parsed else run_at
    generated_at = html_lib.escape(generated_at)

    out_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<title>Sustainable AI Weekly Monitor</title>
{STYLE}
</head>
<body>
  <header class="site">
    <div>
      <h1 class="brand">Sustainable AI Weekly Monitor</h1>
      <p class="tagline">AI &amp; sustainability news, Scope 3 audits, and cloud carbon reporting &mdash; updated hourly.</p>
      <div class="header-meta">Last updated {generated_at}</div>
    </div>
    <button type="button" class="theme-toggle" id="themeToggle">Switch to dark</button>
  </header>
  <div class="layout">
    <aside class="sidebar">
      <h2>Search</h2>
      <input type="text" id="searchBox" class="side-search" placeholder="Title, source, summary...">
      <label class="side-toggle"><input type="checkbox" id="onlyNew"> Only new since last check</label>
      <label class="side-toggle"><input type="checkbox" id="showOffTopic"> Show off-topic mentions too</label>
      <h2>Filter by date</h2>
      <select id="dateFilter" class="side-select">
        <option value="">All time</option>
        <option value="{today_str}">Today</option>
        <option value="{d3_str}">Last 3 days</option>
        <option value="{d7_str}">Last 7 days</option>
      </select>
      {spike_html}
      {stats_html}
      <div class="sidebar-footer">
        Sources: Google News, Data Center Dynamics, Northeastern Global News. Screened by Gemini for
        genuine relevance, not just keywords &mdash; see the
        <a href="https://github.com/joshuam0y/sustainable-ai-weekly-monitor" target="_blank" rel="noopener">README</a>
        for details.
      </div>
    </aside>
    <main class="main">
      {HOW_TO_HTML}
      <div class="type-tabs">{type_tabs}</div>
      <div id="resultCount"></div>
      <div class="article-list">{rows_html}</div>
      {empty_state}
      <footer class="site-footer">Automatically refreshed on a schedule via GitHub Actions.</footer>
    </main>
  </div>
{SCRIPT}
</body></html>
"""

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w") as f:
        f.write(out_html)

    conn.close()
    print(f"Rendered report with {len(rows)} articles ({new_count} new)")


if __name__ == "__main__":
    render()
