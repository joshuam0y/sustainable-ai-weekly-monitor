"""Fetches the real body text of an article page, so classification and
summaries can be based on what an article actually says instead of what its
headline implies.

Deliberately does NOT try to resolve Google News redirect links. Confirmed
live: those URLs (news.google.com/rss/articles/CBMi...) don't redirect at
the HTTP level -- Google serves a ~600KB JavaScript page that resolves the
real publisher URL client-side, and the real URL appears nowhere in the
HTML under any User-Agent. Getting it would mean either running a headless
browser per article in CI or reverse-engineering Google's private
batchexecute endpoint; both are slow and brittle, so those articles keep
their headline-only classification and the direct-publisher feeds (which
give real, immediately fetchable URLs) carry the full-text path instead.
"""

import re

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; SustainableAIMonitor/1.0; "
    "+https://github.com/joshuam0y/sustainable-ai-weekly-monitor)"
)
FETCH_TIMEOUT_SECONDS = 10
# Enough body text to judge what a piece is actually about without bloating
# the Gemini prompt -- the lede and first few paragraphs carry the framing
# that a headline alone leaves ambiguous.
MAX_TEXT_CHARS = 4000
MIN_PARAGRAPH_CHARS = 40

# Real example caught live: Heatmap's article pages put "By continuing, you
# agree to the Terms of Service..." in a <p> right above the actual lede.
BOILERPLATE = re.compile(
    r"terms of service|privacy policy|cookie|newsletter|sign up|subscribe now|"
    r"all rights reserved|advertisement|share this article|related stories",
    re.IGNORECASE,
)


def is_fetchable(link):
    """Whether this link points at a real publisher page we can actually
    read (see module docstring for why Google News links can't be)."""
    return bool(link) and "news.google.com" not in link


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "figure"]):
        tag.decompose()
    # An <article> element, when the page has one, excludes the sidebar and
    # promo rails that otherwise leak into a naive all-<p> sweep.
    root = soup.find("article") or soup
    paragraphs = []
    for p in root.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) < MIN_PARAGRAPH_CHARS or BOILERPLATE.search(text):
            continue
        paragraphs.append(text)
    joined = re.sub(r"\s+", " ", " ".join(paragraphs)).strip()
    return joined[:MAX_TEXT_CHARS]


def fetch_article_text(link, timeout=FETCH_TIMEOUT_SECONDS):
    """Returns the article's body text, or "" when it couldn't be read.

    The empty string is a deliberate sentinel, not just a falsy default:
    ai_summary.py treats NULL as "never attempted" and "" as "attempted,
    nothing available" so a paywalled or blocking site doesn't get retried
    (and re-spend a Gemini call) on every single run forever.
    """
    if not is_fetchable(link):
        return ""
    try:
        resp = requests.get(link, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return ""
        return extract_text(resp.text)
    except Exception as e:
        print(f"article fetch failed for {link[:70]}: {type(e).__name__}")
        return ""
