import os

MODEL = "claude-haiku-4-5-20251001"


def summarize_article(client, title, source):
    prompt = (
        f"Article headline: {title}\nSource: {source}\n\n"
        "Based only on this headline, write a single short sentence (max 25 words) describing what "
        "this article likely covers, for a reader tracking AI-and-sustainability news. No preamble, "
        "and don't claim certainty about details not implied by the headline."
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return None


def backfill_summaries(conn, limit=None):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set -- skipping AI summaries")
        return 0

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    query = "SELECT link, title, source FROM articles WHERE ai_summary IS NULL ORDER BY first_seen DESC"
    rows = conn.execute(query).fetchall()
    if limit:
        rows = rows[:limit]

    done = 0
    for row in rows:
        summary = summarize_article(client, row["title"], row["source"])
        if summary:
            conn.execute("UPDATE articles SET ai_summary = ? WHERE link = ?", (summary, row["link"]))
            conn.commit()
            done += 1

    print(f"Generated {done} AI summaries ({len(rows)} pending)")
    return done
