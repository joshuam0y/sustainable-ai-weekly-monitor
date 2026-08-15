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
    return conn
