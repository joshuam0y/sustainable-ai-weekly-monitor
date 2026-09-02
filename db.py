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
    return conn
