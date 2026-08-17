# MarketWatch

A single-page market dashboard that rebuilds from data each day and publishes to GitHub Pages
at https://infinite2142.github.io/MarketWatch/

## Two repos, one system

| Repo | Visibility | Holds |
|---|---|---|
| `~/marketwatch` (this one) | **public** | the page, its data model, the fetch + render scripts, the deploy workflow |
| `~/marketwatch-core` | **private** | the ledger, memory, task spec, daily prompt, run reports, the wrapper script |

`marketwatch-core` is the state and the instructions; this repo is the artifact. Read
`../marketwatch-core/daily-task.md` before changing anything about how the daily update behaves —
it is the spec, and this file only summarises it.

Everything in this repo is public. Nothing sensitive belongs in `market_watch_data.json`.

## Two machines

Both repos are cloned on two machines, with different jobs. Check `whoami` / `hostname` before
assuming which one you are on.

| | The mini | The laptop |
|---|---|---|
| role | always on; runs the daily update unattended | occasional edits to the algo and the page |
| writes | the analysis, the ledger, the numeric tiles | the template, the Python, the specs, the ledger |
| schedule | launchd → `daily-update.sh` at 07:00 local | nothing scheduled — correct, leave it that way |

**Do not run `daily-update.sh` or `fetch_data.py` from the laptop.** They are the mini's job, and a
second writer of the same generated state is how a day's publish gets lost. Editing and pushing
from the laptop is fine and is the intended workflow: the mini's next run opens with
`git pull --rebase --autostash`, so anything pushed lands there automatically.

A template or workflow change pushed from the laptop deploys in ~2 minutes through Actions without
involving the mini at all. An algo change (`fetch_data.py`, `generator.py`, `daily-prompt.md`,
`daily-task.md`) takes effect on the mini's next run.

`fetch_data.py` and `generator.py` use only the standard library, so they do not care which machine
or which `python3` runs them. Keep it that way — a third-party import is what broke the fetch over
12-17 Aug 2026.

## Files

| File | Role |
|---|---|
| `template_v27.html` | The locked V2.7 design — CSS, SVG, client-side render engine. Hand-edited only. |
| `market_watch_data.json` | The data model, one key per section. Single source of truth for page content. |
| `generator.py` | template + JSON → `index.html`. The only thing that writes `index.html`. |
| `fetch_data.py` | Pulls numeric metrics from FRED CSV + Stooq/Yahoo into the JSON. |
| `.github/workflows/deploy.yml` | Renders and deploys on push; runs a staleness check on a schedule. |

`index.html` is **gitignored**. It is rendered in CI and published straight from the Pages
artifact. Never commit it — tracking it causes push conflicts.

## Publish path

Edit the JSON or the template → commit → push to `main` → Actions renders `index.html` and
deploys. The workflow does **not** write to the repo — only the two machines do, and only the mini
writes generated state.

Concurrent pushes are safe in both directions: `deploy.yml` uses `concurrency: pages` with
`cancel-in-progress: true` so the newest deploy wins, and `daily-update.sh`'s `publish()` rebases
onto origin and retries if a laptop push landed while the analysis step was running.

`python3 generator.py` renders locally for preview. Not required before pushing.

## Write ownership — the thing to get right

Three writers share `market_watch_data.json`. Editing outside your lane gets silently reverted.

- **`fetch_data.py`** owns tile `val`/`chg`/`dir`, every per-metric `as_of` and stale flag, and
  `meta.last_fetch`.
- **The daily analysis task** owns `state_of_play.narrative`, crash-risk assessments, `sectors`,
  `drivers`, `investible_themes` (incl. `access` routes), `radar`, `faded`, `signals`, and
  `meta.report_date` / `report_date_long` / `refresh_label`. It rebuilds these **from the ledger**
  on every run.
- **Hand edits from an interactive session** are durable for the template and for structural
  changes, but **any analytical field edited by hand is overwritten by the next daily run.** To
  make an analytical change stick, put it in `../marketwatch-core/theme-ledger.md` or `memory.md`,
  which is what the daily reads from.

`chartRead` strings are an unresolved ownership edge case — treated as editorial prose, not as
fetch-owned tile data. See the 2026-08-17 note in `memory.md`.

## Traps

- **Uncommitted work gets swept up.** `daily-update.sh` does `git pull --rebase --autostash`, then
  `git add -A` and commits everything dirty under "Daily update YYYY-MM-DD". Commit your own work
  before the mini's next run or it lands in someone else's commit. (This is how a
  `template_v27.html` design tweak got absorbed on 2026-08-17.)
- **Setting `meta.report_date` is a claim.** It means the coverage sweep genuinely completed. If it
  didn't, leave the date stale — the page's chip then reads "Data X · analysis Y" and the
  `staleness` job fails at a 3-day gap, which is the intended alarm. A stale page that says it is
  stale beats a page that lies about its freshness.
- **Validate the JSON before finishing.** `python3 -c "import json; json.load(open('market_watch_data.json'))"`.
  The wrapper's `--- validate ---` step fails the whole run on invalid JSON.
- **Verify writes by re-reading.** On 2026-08-17 a run reported a successful JSON write that was
  not on disk. A report of a write is not proof of one.

## Where the daily runs

Locally on the always-on Mac mini via launchd (`com.marketwatch.daily.plist` →
`daily-update.sh`), because a cloud-scheduled session has no device bridge and cannot push or read
the private repo. The wrapper syncs both repos, runs `fetch_data.py`, invokes
`claude -p "$(cat daily-prompt.md)" --add-dir ~/marketwatch-core`, validates, renders, then commits
and pushes both repos.

## Editorial guardrails

These bind the page's content, not just the daily task:

- **Research, not a portfolio.** No holdings, no positions. Never write about holding, buying,
  selling, trimming, exiting, sizing or allocating. `conv` is the strength of the case, not a
  weight. "Late-stage / fading" means the opportunity is largely priced, not that anything was
  sold. `access` routes are ways a reader *could* get exposure — no sizing, no entry levels.
- **Every numeric claim carries a dated source.** Estimates and forecasts are flagged as such.
- **A lone source contradicting an established anchor is logged, not adopted — and the rejection
  carries a dated re-check obligation.** A rejection that is never revisited hardens into an
  assumption. See the gold `~$5,600` case in `memory.md`: the refusal was right, never scheduling
  the re-check was not.
- **"No result" from a sweep is a statement about the sweep, not the world.** The retirement rule
  keys off *observed* absence of evidence, so a false silence can retire a live theme. Re-verify a
  quiet domain independently before carrying the silence forward.
- Corrections are logged in `memory.md`, never silently fixed.
- Global and UK/European lens, not US-only.
