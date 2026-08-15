import os
import time

MODEL = "gemini-3.5-flash-lite"
CALL_DELAY_SECONDS = 5  # free tier is rate-limited to 15 requests/minute


def summarize_article(client, title, source):
    prompt = (
        f"Article headline: {title}\nSource: {source}\n\n"
        "Based only on this headline, write a single short sentence (max 25 words) describing what "
        "this article likely covers, for a reader tracking AI-and-sustainability news. No preamble, "
        "and don't claim certainty about details not implied by the headline."
    )
    try:
        resp = client.models.generate_content(model=MODEL, contents=prompt)
        return resp.text.strip()
    except Exception as e:
        print(f"Gemini summarize failed for '{title[:60]}': {e!r}")
        return None


def backfill_summaries(conn, limit=12):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set -- skipping AI summaries")
        return 0

    from google import genai
    client = genai.Client(api_key=api_key)

    query = "SELECT link, title, source FROM articles WHERE ai_summary IS NULL ORDER BY first_seen DESC"
    all_pending = conn.execute(query).fetchall()
    rows = all_pending[:limit] if limit else all_pending

    done = 0
    for i, row in enumerate(rows):
        summary = summarize_article(client, row["title"], row["source"])
        if summary:
            conn.execute("UPDATE articles SET ai_summary = ? WHERE link = ?", (summary, row["link"]))
            conn.commit()
            done += 1
        if i < len(rows) - 1:
            time.sleep(CALL_DELAY_SECONDS)

    print(f"Generated {done} AI summaries ({len(rows)} attempted, {len(all_pending)} pending total)")
    return done
