import os
import time

MODEL = "gemini-3.5-flash-lite"
CALL_DELAY_SECONDS = 5  # free tier is rate-limited to 15 requests/minute


def evaluate_article(client, title, source):
    """One call does two jobs -- the summary this already did, plus a
    genuine on-topic judgment -- so this costs nothing extra against the
    free-tier rate limit versus the summary-only version it replaces.

    The keyword filter in scraper.py only proves two keyword CLASSES both
    appear somewhere in the title; it can't tell a story ABOUT the
    environmental impact of AI/cloud computing from one that just mentions
    a data center in passing. Confirmed real example: "ERCOT Hits Pause on
    Texas Data Center Queue. How Worried Should AI Infrastructure Investors
    Be?" passes the keyword gate (has "data center" and "AI") but is an
    investor-sentiment story, not a Scope 3 emissions one -- exactly the
    kind of false positive raised in real feedback on this project.
    """
    prompt = (
        f"Article headline: {title}\nSource: {source}\n\n"
        "You're screening AI-and-sustainability news for a university sustainability team. Their "
        "actual priorities are: Scope 3 emissions audits of AI/cloud computing, the environmental "
        "footprint of data centers and AI more broadly (including energy/power demand, grid strain, "
        "renewable energy sourcing or policy, water use, and cooling), and Northeastern University's "
        "own sustainability/AI work.\n\n"
        "ON-TOPIC even without the words \"carbon\" or \"emissions\": a data center or AI company's "
        "energy/power consumption, strain it puts on the electric grid, renewable energy commitments "
        "or mandates affecting it, or its water/cooling use -- these ARE the environmental footprint, "
        "not just an adjacent business detail.\n"
        "OFF-TOPIC even though it mentions AI, data centers, or energy: stock/investor sentiment, "
        "market-sizing or valuation reports, routine facility openings/expansions/deals with no "
        "energy or environmental angle, or unrelated corporate PR/award announcements that merely "
        "use ESG or sustainability as a buzzword.\n\n"
        "Based only on this headline, reply with exactly two lines, nothing else:\n"
        "ON_TOPIC: yes or no -- per the distinction above.\n"
        "SUMMARY: one short sentence (max 25 words) describing what this article likely covers. "
        "Don't claim certainty about details not implied by the headline."
    )
    try:
        resp = client.models.generate_content(model=MODEL, contents=prompt)
        text = (resp.text or "").strip()
    except Exception as e:
        print(f"Gemini evaluate failed for '{title[:60]}': {e!r}")
        return None, None

    on_topic = None
    summary = None
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("ON_TOPIC:"):
            value = stripped.split(":", 1)[1].strip().lower()
            on_topic = value.startswith("y")
        elif upper.startswith("SUMMARY:"):
            summary = stripped.split(":", 1)[1].strip()

    # Fail open: an unparseable response leaves on_topic as None (treated
    # as "not yet evaluated," i.e. still shown by default -- see
    # render_report.py) rather than silently hiding something real. A
    # research tool missing genuine content is a worse failure than
    # showing one extra borderline article.
    return on_topic, summary or (text[:200] if text else None)


def backfill_summaries(conn, limit=24):
    # Bumped from 12 -- switching the "pending" query above to is_core_topic
    # means every article summarized before this file existed (532 of them
    # at the time this changed) is pending again for one-time reclassification.
    # 24 calls/run * 5s delay is ~2 minutes of runtime, still nowhere near the
    # free-tier per-minute limit, and clears that backlog in about a day of
    # hourly runs instead of two.
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set -- skipping AI summaries")
        return 0

    from google import genai
    client = genai.Client(api_key=api_key)

    # is_core_topic, not ai_summary, is what marks "already evaluated" --
    # articles summarized before this file gained on-topic classification
    # have an ai_summary but a NULL is_core_topic, and need a real Gemini
    # call to get retroactively classified rather than being skipped forever
    # just because some text already sits in ai_summary. Confirmed real
    # case: the ERCOT article the on-topic check was built for already had
    # an old-style summary and would otherwise never get reclassified.
    query = "SELECT link, title, source FROM articles WHERE is_core_topic IS NULL ORDER BY first_seen DESC"
    all_pending = conn.execute(query).fetchall()
    rows = all_pending[:limit] if limit else all_pending

    done = 0
    off_topic = 0
    for i, row in enumerate(rows):
        on_topic, summary = evaluate_article(client, row["title"], row["source"])
        if summary:
            is_core = None if on_topic is None else (1 if on_topic else 0)
            conn.execute(
                "UPDATE articles SET ai_summary = ?, is_core_topic = ? WHERE link = ?",
                (summary, is_core, row["link"]),
            )
            conn.commit()
            done += 1
            if is_core == 0:
                off_topic += 1
        if i < len(rows) - 1:
            time.sleep(CALL_DELAY_SECONDS)

    print(
        f"Generated {done} AI summaries ({off_topic} flagged off-topic, "
        f"{len(rows)} attempted, {len(all_pending)} pending total)"
    )
    return done
