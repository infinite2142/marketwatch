# Market Watch

A single-page market dashboard that rebuilds itself from data each day and publishes to
GitHub Pages.

**Live page:** https://infinite2142.github.io/marketwatch/

A daily read on markets, macro drivers and crash risk, alongside a running register of
investible themes tracked from first sighting on the radar through to played-out. Global
and UK/European lens.

**Research, not a portfolio.** The page holds no positions and never writes about buying,
selling or sizing — a theme's conviction is the strength of the case, not a weight. Every
numeric claim carries a dated source, and estimates say that they are estimates.

## How it is built

`market_watch_data.json` is the data model: one key per section, and the single source of
truth for what the page says. `generator.py` renders it through `template_v28.html` into a
self-contained `index.html`. Standard library only — no build step, no framework, and
nothing for the page to fetch at runtime.

Two writers keep the JSON current, and they do not overlap:

- **The numbers** come from `fetch_data.py`, which pulls the tiles from free, keyless
  feeds — Yahoo's chart endpoint first, FRED's CSV as a second source where one exists. A
  failed feed keeps the prior value and marks the field stale rather than crashing the
  run. The source table inside `fetch_data.py` is the record of what comes from where.
- **The judgement** — the narrative, the crash-risk read, the themes, the radar, the
  signals — is written by a daily analysis that works from a ledger kept in a private
  companion repo. It runs unattended on a Mac mini, not in CI.

CI never writes to the repo. `deploy.yml` renders the page on push and publishes it
straight from the Pages artifact; on a schedule it measures how far the analysis has
fallen behind and fails loudly at three days, so the workflow-failure email is the alarm.

## What is in the repo

| File | Role |
|---|---|
| `market_watch_data.json` | The data model. One key per section. |
| `template_v28.html` | The V2.8 design — CSS, SVG, and the client-side render engine. |
| `generator.py` | template + JSON → `index.html`, via one view model that every view is drawn from. |
| `fetch_data.py` | The numeric tiles, from Yahoo and FRED. |
| `make_preview.py`, `preview.png` | The 1200×630 link-preview card. |
| `.github/workflows/deploy.yml` | Render, deploy, and the freshness alarm. |

`index.html` is **not** committed. It is rendered in CI on every push and served from the
Pages artifact, so the repo never carries a generated file that a second writer could
conflict on. `template_v27.html` is the previous design, kept for reference; nothing
reads it.

**`CLAUDE.md` is the operating manual** — write ownership, cadence, the publish path, the
editorial guardrails and the traps. This file is the short version for a visitor; where
the two disagree, `CLAUDE.md` is the one that is maintained.

## Secrets

None. The feeds are free and keyless, the workflow uses only the GitHub-provided
`GITHUB_TOKEN`, and secret-shaped filenames are excluded in `.gitignore`. Everything here
is public, the data file included.
