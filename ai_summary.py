import os
import time

MODEL = "gemini-3.5-flash-lite"
CALL_DELAY_SECONDS = 5  # free tier is rate-limited to 15 requests/minute

VALID_CATEGORIES = {"northeastern", "scope3_ai_audit", "scope3_cloud", "conversation"}

# A second, finer-grained dimension layered ON TOP of the 4 main categories
# above -- those 4 stay as the primary structure (the boss specifically
# praised having them), this is an additional lens for "what specific theme
# is this article actually about," built from the recurring patterns really
# seen in this feed's own headlines (grid strain, water/cooling, renewable
# mandates, formal emissions audits, chip/model efficiency, local political
# pushback, corporate PR). Only assigned to on-topic articles -- an
# off-topic one doesn't need a theme, it's already hidden by default.
VALID_TOPIC_TAGS = {
    "grid_energy",
    "water_cooling",
    "renewable_policy",
    "emissions_disclosure",
    "hardware_efficiency",
    "community_political",
    "corporate_strategy",
}


def evaluate_article(client, title, source, excerpt=None):
    """One call does five jobs -- category, on-topic judgment, a relevance
    score, a topic tag, and the summary -- so none of this costs anything
    extra against the free-tier rate limit versus the summary-only version
    this replaced.

    CATEGORY exists because buckets were being assigned by which search
    query happened to surface an article, not by what it's actually about --
    real feedback flagged articles sitting in the wrong bucket entirely.
    Reclassifying from the headline's actual content, the same way
    is_core_topic already corrects for keyword-only false positives (see
    that field's docstring in db.py for the ERCOT example), fixes this at
    the source instead of just narrowing the search queries, which
    wouldn't fix anything already mis-bucketed.

    RELEVANCE is a second, finer signal on top of is_core_topic: on-topic
    isn't the same as a *strong* example of its bucket. It lets
    render_report.py surface each bucket's top 5 as a fast "just show me
    the best ones" filter instead of everything on-topic being lumped
    together with equal weight. Real feedback: top picks need to actually
    be relevant, and a bare headline is sometimes too thin to judge that
    well -- excerpt (a real sentence from the source's own RSS entry, see
    scraper.py's _clean_excerpt) gives this call more to work with when
    one exists, without the cost/fragility of fetching and parsing each
    article's actual page across dozens of unpredictable third-party sites.

    TOPIC_TAG is a THIRD dimension, orthogonal to category: two articles in
    the same category bucket (say, scope3_cloud) can be about completely
    different things -- one about a water-cooling retrofit, another about a
    formal emissions audit. This is the "new buckets" layer requested on
    top of the 4 existing ones, not a replacement for them.
    """
    excerpt_line = f"Article excerpt: {excerpt}\n" if excerpt else ""
    prompt = (
        f"Article headline: {title}\nSource: {source}\n{excerpt_line}\n"
        "You're organizing AI-and-sustainability news for a university sustainability team into "
        "four buckets. Read the headline and decide which bucket it ACTUALLY belongs in based on "
        "content, not on what search term might have surfaced it, and not on the Source field above "
        "-- Northeastern's own newsroom also publishes general AI/tech commentary and analysis that "
        "isn't actually about the university, so 'Source: Northeastern Global News' on its own is "
        "NOT evidence for the northeastern bucket:\n"
        "- northeastern: the article's actual SUBJECT is Northeastern University itself -- its "
        "students, faculty, or researchers as the story's protagonists, its campus, or its programs. "
        "Being published by Northeastern's newsroom does not qualify on its own: 'Inside the growing "
        "US effort to block Chinese AI hardware' or 'Why Anthropic, OpenAI and SpaceX are racing to "
        "go public' both ran on Northeastern's site but are general AI-industry stories, not "
        "northeastern (they're conversation). 'Scientists put algae to work making fuel. AI keeps "
        "watch.' or 'Iron powder could soon become renewable energy resource, Northeastern "
        "researchers say' ARE northeastern -- specific NU people/research are the subject.\n"
        "- scope3_ai_audit: a Scope 3 emissions audit, disclosure, or methodology specifically tied "
        "to AI/ML usage\n"
        "- scope3_cloud: Scope 3 or broader emissions disclosures for cloud computing or data "
        "centers generally (not AI-specific)\n"
        "- conversation: general AI-and-sustainability discourse that doesn't fit the buckets above\n\n"
        "Their overall priorities: Scope 3 emissions audits of AI/cloud computing, the environmental "
        "footprint of data centers and AI more broadly (energy/power demand, grid strain, renewable "
        "energy sourcing or policy, water use, cooling), and Northeastern's own sustainability/AI "
        "work.\n\n"
        "ON-TOPIC even without the words \"carbon\" or \"emissions\": a data center or AI company's "
        "energy/power consumption, strain it puts on the electric grid, renewable energy commitments "
        "or mandates affecting it, or its water/cooling use -- these ARE the environmental footprint, "
        "not just an adjacent business detail.\n"
        "OFF-TOPIC even though it mentions AI, data centers, or energy: stock/investor sentiment, "
        "market-sizing or valuation reports, routine facility openings/expansions/deals with no "
        "energy or environmental angle, or unrelated corporate PR/award announcements that merely "
        "use ESG or sustainability as a buzzword.\n\n"
        "This team would rather have a smaller set of genuinely relevant articles than a larger set "
        "padded with loosely-related ones -- if you're genuinely unsure whether something is "
        "on-topic or which bucket it belongs in, decide OFF_TOPIC / conversation rather than forcing "
        "a more specific fit.\n\n"
        "If (and only if) it's on-topic, ALSO pick the one specific theme that best describes it:\n"
        "- grid_energy: power/energy demand, grid capacity or strain\n"
        "- water_cooling: water use or cooling systems specifically\n"
        "- renewable_policy: renewable energy sourcing, mandates, or policy\n"
        "- emissions_disclosure: a formal Scope 3 / carbon audit, report, or disclosure\n"
        "- hardware_efficiency: chips, GPUs, model design, or infrastructure built to use less energy\n"
        "- community_political: local/political opposition, regulation debate, or community impact\n"
        "- corporate_strategy: a company's sustainability strategy, commitments, or PR\n"
        "Leave it blank only if truly none of these fit.\n\n"
        "Based on the headline (and the excerpt above, if one was given), reply with exactly five "
        "lines, nothing else:\n"
        "CATEGORY: one of northeastern, scope3_ai_audit, scope3_cloud, conversation -- whichever "
        "actually fits best.\n"
        "ON_TOPIC: yes or no -- per the distinction above.\n"
        "RELEVANCE: a number 1-10 for how strongly this headline exemplifies its chosen bucket's "
        "specific theme (10 = a textbook example, 1 = barely related even though it's on-topic).\n"
        "TOPIC_TAG: one of grid_energy, water_cooling, renewable_policy, emissions_disclosure, "
        "hardware_efficiency, community_political, corporate_strategy, or blank.\n"
        "SUMMARY: one short sentence (max 25 words) describing what this article likely covers. "
        "Don't claim certainty about details not implied by the headline."
    )
    try:
        resp = client.models.generate_content(model=MODEL, contents=prompt)
        text = (resp.text or "").strip()
    except Exception as e:
        print(f"Gemini evaluate failed for '{title[:60]}': {e!r}")
        return None, None, None, None, None

    category = None
    on_topic = None
    relevance = None
    topic_tag = None
    summary = None
    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("CATEGORY:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value in VALID_CATEGORIES:
                category = value
        elif upper.startswith("ON_TOPIC:"):
            value = stripped.split(":", 1)[1].strip().lower()
            on_topic = value.startswith("y")
        elif upper.startswith("RELEVANCE:"):
            value = stripped.split(":", 1)[1].strip()
            digits = "".join(c for c in value if c.isdigit())
            if digits:
                relevance = max(1, min(10, int(digits)))
        elif upper.startswith("TOPIC_TAG:"):
            value = stripped.split(":", 1)[1].strip().lower()
            if value in VALID_TOPIC_TAGS:
                topic_tag = value
        elif upper.startswith("SUMMARY:"):
            summary = stripped.split(":", 1)[1].strip()

    # Fail open on every field independently: an unparseable line leaves
    # that field None (category/relevance/tag untouched, on_topic treated
    # as "not yet evaluated" i.e. still shown) rather than silently
    # discarding or miscategorizing something real.
    return category, on_topic, relevance, topic_tag, summary or (text[:200] if text else None)


def backfill_summaries(conn, limit=40):
    # Bumped from 24 -- the real throughput ceiling here is run FREQUENCY
    # (hourly), not the per-minute rate limit: CALL_DELAY_SECONDS=5 already
    # caps this at 12 calls/minute even at a much higher batch size, safely
    # under the free tier's 15 RPM. 40 calls * 5s is ~3.3 minutes of Gemini
    # calls per run, still comfortably inside one hourly job. Backlog grew
    # further after adding 3 more Northeastern feeds in this same change,
    # so catch-up speed matters more now, not less.
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set -- skipping AI summaries")
        return 0

    from google import genai
    client = genai.Client(api_key=api_key)

    # relevance_score, not is_core_topic, marks "already evaluated" now --
    # it's the newest signal, so this one query naturally catches both
    # brand-new articles and everything scored before relevance_score
    # existed (which otherwise would never get a category/relevance pass).
    # Northeastern rows go first: real feedback specifically wants that
    # category's already-mis-bucketed items corrected fast, not stuck
    # behind hundreds of older pending rows in a plain recency queue --
    # it's a small category (dozens, not hundreds), so this clears within
    # 1-2 runs instead of waiting on the general backlog.
    query = (
        "SELECT link, title, source, excerpt FROM articles WHERE relevance_score IS NULL "
        "ORDER BY (category = 'northeastern') DESC, first_seen DESC"
    )
    all_pending = conn.execute(query).fetchall()
    rows = all_pending[:limit] if limit else all_pending

    done = 0
    off_topic = 0
    recategorized = 0
    tagged = 0
    for i, row in enumerate(rows):
        category, on_topic, relevance, topic_tag, summary = evaluate_article(
            client, row["title"], row["source"], row["excerpt"]
        )
        if summary:
            is_core = None if on_topic is None else (1 if on_topic else 0)
            if category:
                cur_cat = conn.execute("SELECT category FROM articles WHERE link = ?", (row["link"],)).fetchone()
                if cur_cat and cur_cat["category"] != category:
                    recategorized += 1
                conn.execute("UPDATE articles SET category = ? WHERE link = ?", (category, row["link"]))
            if topic_tag:
                tagged += 1
            conn.execute(
                "UPDATE articles SET ai_summary = ?, is_core_topic = ?, relevance_score = ?, topic_tag = ? "
                "WHERE link = ?",
                (summary, is_core, relevance, topic_tag, row["link"]),
            )
            conn.commit()
            done += 1
            if is_core == 0:
                off_topic += 1
        if i < len(rows) - 1:
            time.sleep(CALL_DELAY_SECONDS)

    print(
        f"Generated {done} AI summaries ({off_topic} flagged off-topic, {recategorized} moved to a "
        f"different bucket, {tagged} given a topic tag, {len(rows)} attempted, {len(all_pending)} pending total)"
    )
    return done
