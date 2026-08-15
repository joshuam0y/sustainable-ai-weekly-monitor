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
2. Stores every article it finds in a local SQLite database (`monitor.db`),
   deduplicated by URL, so nothing already seen is lost or reprocessed.
3. Generates a one-sentence, headline-based AI summary for each new article
   (via the Claude API) so the report is skimmable without opening every link.
4. Renders a static report (`docs/index.html`) with a "New Since Last Check"
   tab plus one tab per topic, each independently scrollable so the page
   itself never turns into an endless scroll.
5. Commits the updated database and report, and republishes the page via
   GitHub Pages.

## Running it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scraper.py         # pulls new articles into monitor.db
.venv/bin/python render_report.py   # rebuilds docs/index.html
```

Set `ANTHROPIC_API_KEY` in the environment to enable AI summaries; without it,
the scraper still runs, it just skips the summary step.

## One-time setup

- Add `ANTHROPIC_API_KEY` as a repo secret (Settings → Secrets and variables →
  Actions) so the hourly workflow can generate summaries.
- Enable GitHub Pages (Settings → Pages → Source: GitHub Actions).

## Files

| File | Purpose |
|---|---|
| `db.py` | SQLite schema + connection helper |
| `scraper.py` | Pulls Google News RSS results, dedupes, categorizes |
| `ai_summary.py` | Headline-based AI summaries via Claude |
| `render_report.py` | Builds the static HTML report in `docs/` |
| `.github/workflows/hourly.yml` | Scheduled scrape + render + publish |
