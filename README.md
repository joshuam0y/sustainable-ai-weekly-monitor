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
2. Also pulls two sites' own real RSS feeds directly (not searched) —
   [Data Center Dynamics](https://www.datacenterdynamics.com/) (Scope 3 Cloud
   Emissions) and [Northeastern Global News](https://news.northeastern.edu/)
   (Northeastern Mentions) — filtered through the same environmental+AI
   keyword gate as everything else. Going straight to Northeastern's own
   newsroom instead of searching for "Northeastern" fixed a real, confirmed
   bug: Google News search for that category had a 100% false-positive rate
   (mostly matching "northeastern India" as a compass direction, not the
   university).
3. Stores every article it finds in a local SQLite database (`monitor.db`),
   deduplicated on a normalized title prefix (not just an exact match or
   URL), so the same story reprinted with a different trailing subtitle by
   a different outlet doesn't show up twice.
4. Generates a one-sentence, headline-based AI summary for each new article
   (via the free Gemini API) so the report is skimmable without opening every link.
5. Renders a static report (`docs/index.html`) — a sidebar (search, date
   filter, spike-detection, at-a-glance stats) plus a single filterable
   article list with simple category tabs above it, not a boxed panel per
   topic — everything filters together instead of needing to leave one tab
   to search across all of them.
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
| `ai_summary.py` | Headline-based AI summaries via Gemini |
| `render_report.py` | Builds the static HTML report in `docs/` |
| `.github/workflows/hourly.yml` | Scheduled scrape + render + publish |
