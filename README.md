# Sustainable AI Weekly Monitor

Part II of the Sustainable AI project: an automated program that performs the
same kind of research done manually in Part I, on a recurring basis, so the
findings stay current without anyone re-running the research by hand.

**Live report:** published via GitHub Pages once enabled (Settings → Pages →
source: `main` / `docs`).

## What it does

Every hour, a GitHub Actions workflow:

1. Searches Google News RSS for a set of keyword queries across four topics:
   - **Northeastern Mentions** — AI sustainability coverage involving NU specifically
   - **Scope 3 AI Audits** — companies/universities auditing Scope 3 emissions of their AI usage
   - **Scope 3 Cloud Emissions** — broader Scope 3 disclosures for cloud computing
   - **General Conversation** — the wider AI-and-sustainability discourse
2. Also pulls five sites' own real RSS feeds directly (not searched) —
   [Data Center Dynamics](https://www.datacenterdynamics.com/) (Scope 3
   Cloud Emissions) and four from
   [Northeastern Global News](https://news.northeastern.edu/) (Northeastern
   Mentions): the main feed plus its sustainability, climate, and
   artificial-intelligence tag feeds specifically. Going straight to
   Northeastern's own newsroom instead of searching for "Northeastern" fixed
   a real, confirmed bug: Google News search for that category had a 100%
   false-positive rate (mostly matching "northeastern India" as a compass
   direction, not the university). The Northeastern feeds use an OR gate
   (environmental keyword OR AI keyword, not both) since they're already
   about the university by construction — confirmed live, requiring both
   left this category sitting at exactly 0 articles in production despite
   the feeds working fine, because a general campus newsroom rarely
   publishes one headline matching both keyword classes at once. Adding the
   three tag-specific feeds on top of the main one turned that into 42 real
   candidates in a single fetch.
3. Stores every article it finds in a local SQLite database (`monitor.db`),
   deduplicated on a normalized title prefix (not just an exact match or
   URL), so the same story reprinted with a different trailing subtitle by
   a different outlet doesn't show up twice.
4. Sends each new article's headline to the free Gemini API for **five**
   things in one call: which of the four buckets it actually belongs in,
   an on-topic judgment, a 1-10 relevance score, a finer-grained topic tag,
   and a one-sentence summary.
   - *On-topic* catches articles that only pass the keyword gate in passing —
     the keyword gate above only proves an environmental term and an AI term
     both show up in the title; it can't tell "Scope 3 emissions audit of AI
     data centers" (on topic) from "ERCOT Hits Pause on Texas Data Center
     Queue. How Worried Should AI Infrastructure Investors Be?" (investor
     sentiment that happens to mention "data center" and "AI"). A cheap, free
     keyword pre-filter (`scraper.py`'s `precheck_core_topic`) also catches
     the most obvious investor/market-framing cases before spending a Gemini
     call.
   - *Category* is re-decided from the headline's actual content instead of
     trusting whichever search query originally surfaced it — real feedback
     flagged articles sitting in a bucket that didn't actually fit.
   - *Relevance* (1-10) ranks how strongly a headline exemplifies its
     bucket's specific theme, not just whether it's on-topic at all. The
     report surfaces each bucket's top 5 by this score as a "Top 5 picks per
     category only" filter.
   - *Topic tag* is a second dimension layered on top of the 4 categories,
     not replacing them: grid & energy demand, water & cooling, renewable
     sourcing & policy, emissions disclosure & audit, hardware & efficiency,
     community & political response, or corporate strategy & reporting.
     Two articles in the same category can be about entirely different
     things — this is what shows up as the row of pills below the category
     tabs, and as a small chip on each article card.

   Articles judged off-topic are never deleted, and category/relevance/topic
   tag never overwrite anything silently unreviewable — off-topic ones are
   hidden from the default view but visible via a "Show off-topic mentions
   too" toggle, same principle as job listings in the companion
   `conservation-climate-jobs` project never being hard-deleted by a new
   filter rule.
5. Renders a static report (`docs/index.html`) — a sidebar (search, date
   filter, spike-detection, at-a-glance stats, toggles) plus a single
   filterable article list with category tabs and a second row of AI-tagged
   topic pills above it, not a boxed panel per topic — everything filters
   together instead of needing to leave one tab to search across all of them.
6. Commits the updated database and report, and republishes the page via
   GitHub Pages.

## Running it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scraper.py         # pulls new articles into monitor.db
.venv/bin/python render_report.py   # rebuilds docs/index.html
```

Set `GEMINI_API_KEY` in the environment to enable AI summaries; without it,
the scraper still runs, it just skips the summary step.

## One-time setup

- Get a free Gemini API key at https://aistudio.google.com/apikey (no credit
  card required) and add it as a repo secret named `GEMINI_API_KEY`
  (Settings → Secrets and variables → Actions).
- Enable GitHub Pages (Settings → Pages → Source: GitHub Actions).

## Files

| File | Purpose |
|---|---|
| `db.py` | SQLite schema + connection helper |
| `scraper.py` | Pulls Google News RSS results, dedupes, categorizes |
| `ai_summary.py` | Headline-based AI summaries + on-topic classification via Gemini |
| `render_report.py` | Builds the static HTML report in `docs/` |
| `.github/workflows/hourly.yml` | Scheduled scrape + render + publish |
