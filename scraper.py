import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser

from ai_summary import backfill_summaries
from db import get_conn

# Google News RSS does loose/fuzzy matching, not a strict AND of query terms --
# a query like "Scope 3 audit artificial intelligence" still surfaces plain
# financial/compliance-audit stories, and "Northeastern University AI
# sustainability" surfaces "northeastern India" as a compass direction, not
# the university. Confirmed in production: 28% of "conversation" articles,
# 51% of "scope3_ai_audit", and literally 100% of "northeastern" had zero
# environmental keyword in the title. This is the actual relevance gate the
# query strings can't enforce on their own.
ENVIRONMENTAL_KEYWORDS = re.compile(
    r"energy|carbon|emission|water|footprint|electricit|grid|climate|sustainab|"
    r"environment|scope ?3|cooling|data cent|circular|life.?cycle|net.?zero|"
    r"renewable|greenhouse|\bghg\b|\besg\b",
    re.IGNORECASE,
)


def is_relevant(title, category):
    if not ENVIRONMENTAL_KEYWORDS.search(title):
        return False
    if category == "northeastern" and "northeastern university" not in title.lower():
        return False
    return True


def strip_source_suffix(title, source):
    """Google News RSS always appends ' - Source Name' to the title itself,
    on top of giving it to us separately -- confirmed 100% consistent across
    309 real production rows. Without this, every card shows the source
    twice: once baked into the headline, once in the byline underneath."""
    suffix = f" - {source}"
    if source and title.endswith(suffix):
        return title[: -len(suffix)]
    return title


QUERIES = {
    "conversation": [
        "AI sustainability",
        "artificial intelligence environmental impact",
        "AI energy consumption",
        "generative AI carbon footprint",
    ],
    "scope3_ai_audit": [
        "Scope 3 emissions AI",
        "Scope 3 audit artificial intelligence",
        "AI carbon footprint audit university",
    ],
    "scope3_cloud": [
        "Scope 3 emissions cloud computing",
        "data center Scope 3 emissions report",
        "cloud computing carbon footprint disclosure",
    ],
    "northeastern": [
        '"Northeastern University" AI sustainability',
        '"Northeastern University" carbon footprint',
        '"Northeastern University" sustainability report',
    ],
}

FEED_URL = "https://news.google.com/rss/search?q={q}+when:7d&hl=en-US&gl=US&ceid=US:en"


def fetch_query(query):
    url = FEED_URL.format(q=quote(query))
    feed = feedparser.parse(url)
    return feed.entries


def run():
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0

    for category, queries in QUERIES.items():
        for query in queries:
            entries = fetch_query(query)
            for entry in entries:
                link = entry.get("link")
                if not link:
                    continue
                title = entry.get("title", "").strip()
                source = entry.get("source", {}).get("title", "") if entry.get("source") else ""
                title = strip_source_suffix(title, source)
                if not is_relevant(title, category):
                    continue
                published = entry.get("published", "")

                # The same article can reach us twice under different Google
                # News tracking URLs (once per matching query), and wire
                # syndication means the same press release often runs
                # verbatim across multiple *different* outlets -- confirmed
                # in production: 105 near-duplicate title pairs, including
                # the identical headline picked up by three separate energy
                # trade sites. Dedup on title alone (not source), so a wire
                # story doesn't show up once per outlet that reprinted it.
                existing = conn.execute(
                    "SELECT link FROM articles WHERE title = ? COLLATE NOCASE", (title,)
                ).fetchone()
                if existing:
                    continue

                cur = conn.execute(
                    "INSERT OR IGNORE INTO articles (link, title, source, published, category, summary, first_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (link, title, source, published, category, "", now),
                )
                if cur.rowcount:
                    new_count += 1
            time.sleep(1)  # be polite to Google News

    conn.execute("INSERT OR REPLACE INTO runs (run_at, new_articles) VALUES (?, ?)", (now, new_count))
    conn.commit()
    print(f"Run at {now}: {new_count} new articles")

    backfill_summaries(conn)
    conn.close()


if __name__ == "__main__":
    run()
