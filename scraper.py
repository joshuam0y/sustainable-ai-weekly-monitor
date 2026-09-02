import html as html_lib
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
    r"environment|scope ?3|cooling|data.?cent(?:er|re)|circular|life.?cycle|net.?zero|"
    r"renewable|greenhouse|\bghg\b|\besg\b",
    re.IGNORECASE,
)

# The environmental filter alone still isn't enough -- confirmed in
# production: 24% of articles that passed it (e.g. "The Kitchen as Climate
# Lever: How Hotel Chefs Are Driving Sustainability", or a wire-service
# stream of generic "TSRS-Aligned 2025 Sustainability Report" filler from
# unrelated companies) have zero connection to AI at all. This project is
# specifically about AI's environmental footprint (and the brief specifically
# asks for Scope 3 audits *of AI/cloud usage*, not just any company's routine
# ESG report), so both keyword groups have to be present.
AI_KEYWORDS = re.compile(
    r"\bAIs?\b|artificial intelligence|machine learning|\bLLMs?\b|generative|genai|"
    r"\bGPTs?\b|chatgpt|chatbot|data.?cent(?:er|re)|\bGPUs?\b|\bcompute\b|neural|"
    r"algorithm|gemini|copilot|\bclaude\b|\bllama\b|large language model|"
    r"openai|anthropic|deepmind|\bxai\b|mistral ai|hyperscaler",
    re.IGNORECASE,
)


def _has_environmental_and_ai_keywords(title):
    return bool(ENVIRONMENTAL_KEYWORDS.search(title) and AI_KEYWORDS.search(title))


# Confirmed live: requiring the exact phrase "northeastern university" left
# this category at 0 real articles across the whole database, even though
# genuinely on-topic Northeastern stories were reaching this filter --
# "Aoun declares 'human centrality' as focus of Northeastern's new academic
# plan" and an algae-biofuel piece from Northeastern Global News itself both
# got dropped for saying "Northeastern's" or nothing at all, not the exact
# phrase. Loosened to just "northeastern" appearing anywhere, with an
# exclusion list for the actual false-positive pattern this was originally
# guarding against (the compass direction, e.g. "northeastern India").
NORTHEASTERN_COMPASS_DIRECTION = re.compile(
    r"northeastern\s+(india|china|u\.?s\.?a?\b|united states|region|asia|europe|africa|"
    r"brazil|nigeria|thailand|syria|australia)",
    re.IGNORECASE,
)


def is_relevant(title, category, source=""):
    if category == "northeastern":
        # Same OR-gate reasoning as fetch_general_feeds(): confirmed live,
        # the two real NU-relevant headlines this path actually surfaced
        # ("Aoun declares 'human centrality'...", an algae-biofuel piece)
        # each had only an AI keyword or neither, never both -- the AND
        # gate alone accounts for why this category sat at 0 articles.
        # These queries already anchor on "Northeastern University" plus a
        # topic term, so the title itself doesn't have to carry both
        # keyword classes on top of that.
        if not (ENVIRONMENTAL_KEYWORDS.search(title) or AI_KEYWORDS.search(title)):
            return False
        # A story bylined by a Northeastern-affiliated outlet (its own
        # newsroom, the student paper) is already about Northeastern by
        # construction, the same reasoning fetch_general_feeds() uses --
        # confirmed live, the algae-biofuel piece never says "Northeastern"
        # in its own headline despite running on Northeastern Global News.
        # A third-party outlet that merely mentions NU still needs the
        # title to actually say so, to keep out the compass-direction
        # false positive this check was originally built to catch.
        is_nu_source = "northeastern" in (source or "").lower()
        if not is_nu_source:
            lowered = title.lower()
            if "northeastern" not in lowered:
                return False
            if NORTHEASTERN_COMPASS_DIRECTION.search(title):
                return False
        return True
    return _has_environmental_and_ai_keywords(title)


# Real feedback (from the person this project reports to): the keyword gate
# above proves an environmental term and an AI term both appear somewhere in
# the title, but not that either is the article's actual MAIN POINT. Confirmed
# example that passed the gate above: "ERCOT Hits Pause on Texas Data Center
# Queue. How Worried Should AI Infrastructure Investors Be?" -- has "data
# center" and "AI", but is an investor-sentiment story, not a Scope 3
# emissions one. STRONG_ENVIRONMENTAL_KEYWORDS below is the subset of
# ENVIRONMENTAL_KEYWORDS that's actually specific to emissions/sustainability
# substance (as opposed to "grid" or "data center", which show up in plain
# business/infrastructure stories too) -- a title with investor/market
# framing AND none of these strong terms is cheap, free evidence of exactly
# the false-positive pattern flagged. This doesn't replace the Gemini
# classification in ai_summary.py (which still runs on everything else and
# catches subtler cases) -- it just catches the obvious ones without
# spending an API call, and per the same "never hard-delete" principle used
# elsewhere in this project, it soft-hides (is_core_topic = 0) rather than
# skipping insertion entirely, so it stays reviewable via the "Show
# off-topic mentions too" toggle instead of silently vanishing.
STRONG_ENVIRONMENTAL_KEYWORDS = re.compile(
    r"carbon|emission|footprint|scope ?3|\bghg\b|net.?zero|sustainab|greenhouse|renewable",
    re.IGNORECASE,
)

FINANCIAL_ANGLE_KEYWORDS = re.compile(
    r"investor|invest(?:ing|ment)?s?\b|\bstocks?\b|\bshares\b|earnings|market cap|valuation|"
    r"how worried should|\bqueue\b|buildout|capacity crunch|\bipo\b|hedge fund|"
    r"should .* be\b",
    re.IGNORECASE,
)

INVESTOR_ANGLE_SUMMARY = (
    "Pre-filtered: matches the environmental+AI keyword gate, but reads as investor/market "
    "framing with no emissions-specific term in the headline."
)


def _is_investor_angle_without_substance(title):
    return bool(FINANCIAL_ANGLE_KEYWORDS.search(title) and not STRONG_ENVIRONMENTAL_KEYWORDS.search(title))


def precheck_core_topic(title):
    """Returns (ai_summary, is_core_topic, relevance_score) to pre-populate
    at insert time, or (None, None, None) to leave all three NULL for the
    normal Gemini backfill pass. relevance_score is pinned to 1 (the floor)
    rather than left NULL so this row is never re-queued for a Gemini call
    it doesn't need -- ai_summary.py's pending query checks relevance_score,
    not is_core_topic."""
    if _is_investor_angle_without_substance(title):
        return INVESTOR_ANGLE_SUMMARY, 0, 1
    return None, None, None


HTML_TAG = re.compile(r"<[^>]+>")


def _clean_excerpt(raw_summary, max_len=280):
    """Strips HTML from an RSS entry's own summary/description field.
    Confirmed live: Google News RSS's summary is just an <a> tag repeating
    the title (worthless), but direct-site feeds like Northeastern's and
    Data Center Dynamics' include a real sentence -- e.g. "There's more to
    a simple 'thank you' than meets the ear..." -- worth passing to Gemini
    as extra context. Returns None (not empty string) when there's nothing
    usable, so callers can tell "no excerpt" apart from "empty excerpt"."""
    if not raw_summary:
        return None
    text = html_lib.unescape(HTML_TAG.sub(" ", raw_summary))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*The post .* appeared first on .*$", "", text)  # WordPress feed boilerplate
    if not text:
        return None
    return text[:max_len]


def _normalize_title(title):
    """Lowercased, whitespace-collapsed, truncated to the first 50 chars --
    the same real story, reprinted by a different outlet or given a
    different length headline by whichever aggregator wrote it, keeps that
    much verbatim even when trailing clauses/subtitles differ. Confirmed
    live: "AI Adoption Reaches 37% in Supplier Risk and Sustainability, but
    Widespread Integration Remains Limited: Achilles Survey" and "AI
    adoption reaches 37% in supplier risk and sustainability" are the same
    underlying story, but the exact-title (COLLATE NOCASE) dedup this
    replaced didn't catch it -- confirmed shipped as a real visible
    duplicate on the live report before this fix."""
    return re.sub(r"\s+", " ", title.strip().lower())[:50]


def load_seen_title_prefixes(conn):
    return {_normalize_title(r["title"]) for r in conn.execute("SELECT title FROM articles").fetchall()}


def dedupe_existing(conn):
    """One-time (well, every-run, but a no-op once caught up) cleanup for
    rows already stored before _normalize_title()'s prefix-based dedup
    existed -- that check only prevents NEW duplicates, it doesn't remove
    ones already sitting in monitor.db from when this compared full exact
    titles instead. Keeps the earliest (by first_seen) row in each
    normalized-prefix group, drops the rest."""
    rows = conn.execute("SELECT link, title, first_seen FROM articles ORDER BY first_seen ASC").fetchall()
    seen = {}
    to_delete = []
    for row in rows:
        norm = _normalize_title(row["title"])
        if norm in seen:
            to_delete.append(row["link"])
        else:
            seen[norm] = row["link"]
    if to_delete:
        conn.executemany("DELETE FROM articles WHERE link = ?", [(link,) for link in to_delete])
        conn.commit()
        print(f"dedupe_existing: removed {len(to_delete)} already-stored near-duplicate article(s).")


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

# Each site's own real RSS feed -- read directly, not searched. Confirmed
# live: all are plain, working feeds. Assigned straight to a category
# rather than run through QUERIES, since there's no keyword query to
# construct for "just give me your latest items."
#
# The three tag-specific Northeastern feeds (confirmed live, all real and
# distinct from the main feed and from each other -- "Breakthrough research
# uses machine learning to better predict New England floods" only showed
# up in the climate tag, not the main feed) exist because the plain
# newsroom feed alone left "northeastern" sitting at 0 real articles in
# production: a general campus newsroom rarely publishes one headline that
# happens to be topical AND about NU specifically in the same 25-item
# window. These are already filtered to Northeastern's own sustainability/
# climate/AI coverage, so genuinely relevant volume is far higher per fetch.
GENERAL_FEEDS = [
    ("https://www.datacenterdynamics.com/en/rss/", "scope3_cloud", "Data Center Dynamics"),
    ("https://news.northeastern.edu/feed/", "northeastern", "Northeastern Global News"),
    ("https://news.northeastern.edu/tag/sustainability/feed/", "northeastern", "Northeastern Global News"),
    ("https://news.northeastern.edu/tag/climate/feed/", "northeastern", "Northeastern Global News"),
    ("https://news.northeastern.edu/tag/artificial-intelligence/feed/", "northeastern", "Northeastern Global News"),
]


def fetch_query(query):
    url = FEED_URL.format(q=quote(query))
    feed = feedparser.parse(url)
    return feed.entries


MAX_GENERAL_FEED_AGE_DAYS = 90


def _is_stale(published, max_age_days=MAX_GENERAL_FEED_AGE_DAYS):
    """The Google News query path is already recency-scoped (FEED_URL's
    when:7d), but a plain site RSS feed isn't -- confirmed live, Northeastern's
    sustainability/climate tag feeds return their all-time best-matching
    content, not just recent posts (real published dates found: 2013, 2019,
    2021). 40% of everything the Northeastern feeds surfaced in one fetch was
    over a year old, which isn't "news" for something called a WEEKLY
    monitor. Unparseable dates fail open (kept, not stale) since a feed
    entry with no date at all is far more likely a formatting quirk than
    confirmed old content."""
    if not published:
        return False
    try:
        dt = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt) > timedelta(days=max_age_days)


def fetch_general_feeds(conn, now, seen_prefixes):
    """
    Unlike QUERIES above (Google News searches, filtered by is_relevant()'s
    full keyword+category gate), these feeds are each site's own real RSS
    output -- the northeastern-specific "title must literally say
    'Northeastern University'" check in is_relevant() doesn't apply here:
    Northeastern's own newsroom feed is inherently about Northeastern by
    construction, without needing the headline to say so (confirmed live,
    most of its real headlines don't -- e.g. "Northeastern students find AI
    isn't a cure all for drug discovery"). This was the exact gap noted in
    is_relevant()'s own comment history: 100% of "northeastern"-category
    articles from Google News search had zero real Northeastern connection
    (matching "northeastern India" as a compass direction); going straight
    to Northeastern's own feed instead of searching for it sidesteps that
    false-positive problem entirely.

    The Northeastern feed uses an OR gate (environmental OR AI keyword), not
    the AND gate everything else uses -- confirmed live, a general campus
    newsroom almost never produces one headline matching both keyword
    classes at once (0 of 25 real current items passed the AND gate; the
    "northeastern" category sat at zero articles in the whole database as a
    result). It's still a curated single feed already about Northeastern by
    construction, so the volume risk from loosening this one gate is capped
    at ~25 items/fetch, unlike an open Google News search.
    """
    new_count = 0
    for url, category, source_label in GENERAL_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"general feed {url} failed ({type(e).__name__}: {e}), skipping.")
            continue
        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            title = entry.get("title", "").strip()
            has_env = ENVIRONMENTAL_KEYWORDS.search(title)
            has_ai = AI_KEYWORDS.search(title)
            if category == "northeastern":
                if not (has_env or has_ai):
                    continue
            elif not (has_env and has_ai):
                continue
            published = entry.get("published", "")
            if _is_stale(published):
                continue
            norm = _normalize_title(title)
            if norm in seen_prefixes:
                continue
            excerpt = _clean_excerpt(entry.get("summary", ""))
            ai_summary, is_core_topic, relevance_score = precheck_core_topic(title)
            cur = conn.execute(
                "INSERT OR IGNORE INTO articles "
                "(link, title, source, published, category, summary, first_seen, ai_summary, is_core_topic, "
                "relevance_score, excerpt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (link, title, source_label, published, category, "", now, ai_summary, is_core_topic,
                 relevance_score, excerpt),
            )
            if cur.rowcount:
                seen_prefixes.add(norm)
                new_count += 1
        time.sleep(1)  # polite delay between feeds
    return new_count


def run():
    conn = get_conn()
    dedupe_existing(conn)
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    # Shared across every query AND every general feed, updated as new
    # rows are added within this same run -- catches a duplicate the
    # instant it shows up a second time in this run, not just against
    # history from previous runs.
    seen_prefixes = load_seen_title_prefixes(conn)

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
                if not is_relevant(title, category, source):
                    continue
                published = entry.get("published", "")

                # The same article can reach us twice under different Google
                # News tracking URLs (once per matching query), and wire
                # syndication means the same press release often runs
                # verbatim (or with a different trailing subtitle -- see
                # _normalize_title()'s own docstring for a real confirmed
                # case) across multiple *different* outlets -- confirmed in
                # production: 105 near-duplicate title pairs, including the
                # identical headline picked up by three separate energy
                # trade sites. Dedup on a normalized title prefix (not
                # source, not the full exact string), so a wire story
                # doesn't show up once per outlet that reprinted or
                # re-headlined it.
                norm = _normalize_title(title)
                if norm in seen_prefixes:
                    continue

                ai_summary, is_core_topic, relevance_score = precheck_core_topic(title)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO articles "
                    "(link, title, source, published, category, summary, first_seen, ai_summary, is_core_topic, relevance_score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (link, title, source, published, category, "", now, ai_summary, is_core_topic, relevance_score),
                )
                if cur.rowcount:
                    seen_prefixes.add(norm)
                    new_count += 1
            time.sleep(1)  # be polite to Google News

    new_count += fetch_general_feeds(conn, now, seen_prefixes)

    conn.execute("INSERT OR REPLACE INTO runs (run_at, new_articles) VALUES (?, ?)", (now, new_count))
    conn.commit()
    print(f"Run at {now}: {new_count} new articles")

    backfill_summaries(conn)
    conn.close()


if __name__ == "__main__":
    run()
