import sqlite3

DB_PATH = "monitor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    link TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT,
    published TEXT,
    category TEXT NOT NULL,
    summary TEXT,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_at TEXT PRIMARY KEY,
    new_articles INTEGER NOT NULL
);
"""


def get_conn(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(articles)")}
    if "ai_summary" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN ai_summary TEXT")
        conn.commit()
    # NULL = not yet evaluated (e.g. GEMINI_API_KEY unset, or still queued
    # behind the per-run backfill limit), 1 = the environmental/AI angle is
    # the article's actual main point, 0 = it just mentions a keyword in
    # passing (a real, confirmed example: "ERCOT Hits Pause on Texas Data
    # Center Queue. How Worried Should AI Infrastructure Investors Be?" is
    # an investor-sentiment story, not a Scope 3 emissions one, despite
    # matching both keyword gates). Never deletes anything the keyword
    # filter already accepted -- see render_report.py's "Show off-topic
    # mentions too" toggle -- so a wrong LLM call is reviewable, not lost.
    if "is_core_topic" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN is_core_topic INTEGER")
        conn.commit()
    # 1-10, how strongly the headline exemplifies ITS bucket's specific theme
    # (not just "is it on topic at all" -- is_core_topic already covers
    # that). NULL = not yet scored. Real feedback: buckets were assigned by
    # which search query surfaced an article, not by what it's actually
    # about, so some articles sat in the wrong bucket entirely -- category
    # itself gets corrected by the same Gemini call that sets this score
    # (see ai_summary.py), and render_report.py uses the score to surface
    # each bucket's top 5 as an easy "just show me the best ones" filter.
    if "relevance_score" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN relevance_score INTEGER")
        conn.commit()
    # A finer-grained AI-assigned theme (grid/energy, water/cooling,
    # renewable policy, emissions disclosure, hardware efficiency,
    # community/political response, corporate strategy) layered on top of
    # the 4 main categories, not replacing them -- those 4 stay as the
    # primary structure the report already uses. NULL = not yet tagged, or
    # genuinely didn't fit any of the 7 well (see ai_summary.py's
    # VALID_TOPIC_TAGS). See render_report.py's TOPIC_TAG_LABELS for the
    # human-readable names and the second filter row it renders from this.
    if "topic_tag" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN topic_tag TEXT")
        conn.commit()
    # A real excerpt sentence from the source's own RSS entry, when one
    # exists -- confirmed live, Google News RSS's "summary" field is
    # useless (just an <a> tag repeating the title), but direct-site feeds
    # (Northeastern's own, Data Center Dynamics) include a genuine sentence
    # of content. Passing this to ai_summary.py's Gemini call gives it more
    # to judge relevance from than a bare headline, without the cost/
    # fragility of fetching and parsing each article's actual page across
    # dozens of unpredictable third-party sites. NULL when no real excerpt
    # was available (most Google-News-sourced rows) -- the prompt just
    # falls back to headline-only, same as before this column existed.
    if "excerpt" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN excerpt TEXT")
        conn.commit()
    return conn
