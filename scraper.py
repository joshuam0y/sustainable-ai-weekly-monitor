import time
from datetime import datetime, timezone
from urllib.parse import quote

import feedparser

from ai_summary import backfill_summaries
from db import get_conn

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
        "Northeastern University AI sustainability",
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
                published = entry.get("published", "")

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
