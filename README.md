# NBA Sleeper Model

[![Tests](https://github.com/Dave356w/NBA-Sleeper-Model/actions/workflows/ci.yml/badge.svg)](https://github.com/Dave356w/NBA-Sleeper-Model/actions/workflows/ci.yml)
[![Capture and publish](https://github.com/Dave356w/NBA-Sleeper-Model/actions/workflows/model.yml/badge.svg)](https://github.com/Dave356w/NBA-Sleeper-Model/actions/workflows/model.yml)

**[Open the dashboard](https://dave356w.github.io/NBA-Sleeper-Model/)** · [Build specification](MODEL.md) · [Actions](https://github.com/Dave356w/NBA-Sleeper-Model/actions)

An independent NBA forecasting system. Raw home/away scores are the sums of every valid Sleeper projected player's points. Version 1.20 adds a learned opponent-defense correction to adaptive scoring. No Four Factors, efficiency, BPM, production-model outputs, or independent injury adjustments are used.

## What runs

- Sleeper game-specific player points and seconds, validated against 200–280 projected team minutes.
- ESPN regular-season schedule, UTC tips, statuses and overtime-inclusive finals.
- **ESPN DraftKings odds only, timestamped when fetched. No Odds API or paid-feed secret.** The sportsbook's own update time is unknown; fetch time establishes quote eligibility by the owner's registered policy.
- Immutable first snapshots and T−5h locks with a 15-minute capture tolerance.
- A fixed WP-10 / ATS-14 / totals-19 control and versioned adaptive scoring.
- **25-calendar-day residual-defense decay**, separate zero-centered ridge priors: defense 5 games, offense nuisance control 20, global/venue 100. Offensive coefficients help estimate defense but are not applied to scores.
- Raw and adjusted scores, component corrections, and an untracked Latest view alongside immutable first/lock tabs.
- Winner probabilities, margin/total corrections, ATS/totals probabilities, no-vig edges, offered-price EV, research selections, grades and public results.
- Stage-specific weighted calibration with 75-game half-life, 30-game minimum, shrinkage, daily caps, rejection safeguards and complete training manifests.
- An atomic, hash-verified transaction ledger; CSV and public JSON are derived exports.

Version 1.19 started without historical seeding. For v1.20, the first-stage team correction is explicitly initialized from 117 audited current-season legacy first snapshots, stored with a pinned hash in `data/calibration_seeds`. They are training history, never native forward results. Lock-stage corrections and new-version confidence calibration start at defaults. Historical reference claims are not imported as forward performance. No actual wagers are executed.

## Local setup

Use Python 3.12 on Linux/macOS (file locking uses `fcntl`). From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python -m sleeper_nba capture --dry-run
python -m sleeper_nba run
python -m http.server 8000 --directory site
```

Open `http://localhost:8000`. Each command also supports a global `--root /path/to/repository` before its subcommand.

```bash
python -m sleeper_nba capture
python -m sleeper_nba grade
python -m sleeper_nba refit
python -m sleeper_nba report
python -m sleeper_nba build-site
python -m sleeper_nba validate
python -m sleeper_nba backtest
python -m sleeper_nba import-legacy path/to/input.json --research-only
```

`run` grades existing forecasts, refits completed slates, captures new first/lock forecasts, verifies the ledger and renders the website. `capture --dry-run` performs reads and calculations without creating records or advancing calibration. `backtest` is an explicitly labeled internal chronological replay, not a substitute for the native experiment. Legacy imports are isolated in `research/output` and cannot enter native calibration.

## GitHub Actions and Pages

1. In **Settings → Pages**, select **GitHub Actions** as the source.
2. In **Settings → Actions → General**, enable Actions and allow the model workflow's declared contents-write permissions. No external credentials are needed.
3. Run **Capture, grade, calibrate and publish** manually once, then leave its scheduled runs enabled.

`model.yml` runs at minutes 03/13/23/33/43/53 of every hour. A shared non-cancelling concurrency group serializes model writers; an OS lock serializes local commands. Accepted transactions are committed even if reporting fails. Each run uploads a 30-day audit recovery artifact. The site is deployed with the official Pages artifact/deploy actions. `deploy.yml` rebuilds after application changes; `ci.yml` lints, tests, validates and builds without live network dependencies.

GitHub schedules are best effort and may run late or skip intervals. A capture outside the defined lock window is never called a lock. Missed locks are recorded. Reliable T−5h coverage requires an external scheduler or continuously running watcher; neither is silently fabricated by this implementation. Public repositories generally have no standard Actions-minute charge, but account policy and limits still apply.

## Market policy

Quotes come from ESPN's DraftKings main-line fields; the feed's field name `close` is treated as its current captured quote, not proof of a genuine closing price. Two-sided prices must come from the same captured event/book/line. Fetch completion must be within five minutes of the paired Sleeper response and before tip. Lock quotes must also be captured inside the lock window. Missing odds never block a score forecast and cannot be backfilled into its betting record later.

ATS/totals primary betting and probability scores use half-point main lines, with both prices between -135 and +115. Integer lines remain informational because the normal model does not estimate push mass. The grading engine supports imported pushes (zero units). Moneyline value requires ≥5 percentage points of no-vig advantage and positive offered-price EV, evaluating both sides. ATS's ≥3-point subset and totals' ≥5-percentage-point subset remain separately labeled research series; a no-vig edge alone does not guarantee positive EV.

Near-tip quote observations support **observed closing** comparisons only when a same-book quote was actually captured within 15 minutes before tip. No unavailable opening/closing price is inferred. Captured odds can still reflect a stale upstream sportsbook feed; fetch time is not the sportsbook update time.

## Data integrity and operations

`data/transactions/*.json` and the pinned `data/calibration_seeds` input are authoritative. Every transaction contains complete immutable records and a SHA-256 payload checksum. Forecast records contain normalized player rows, raw source rows, capture provenance, exact parameters, configuration hash and stage. Calibration versions retain code/dependency versions and the exact observation/result revisions and weights used. Both variants are stored in one stage forecast to prevent double-counting training games.

An empty Sleeper `stats: {}` row is a preserved **unprojected** roster placeholder, not an imputed zero. Missing or contradictory fields in a populated projection invalidate that game snapshot. All teams must independently pass minute coverage. Exact duplicates collapse with a count; conflicting duplicates invalidate the snapshot.

Major tip changes (>15 minutes), date changes and postponements retire the original game instance and void its selections. A rescheduled instance must earn new pregame captures. Ordinary actual-tip delays without a schedule revision do not alter a frozen forecast. Final-score corrections append result and grade revisions; historical forecasts remain unchanged.

### Recovery

- An API outage is recorded in quality events; already accepted forecasts survive. Retry the workflow after the source recovers.
- Restore the complete `data/transactions` and `data/calibration_seeds` directories from Git history or an audit artifact into an isolated checkout. Run `validate`, then `build-site`; compare IDs/checksums/counts before replacing a production copy. Never edit an existing transaction to repair a result.
- Export corruption is repairable by rebuilding from transactions. A transaction checksum failure stops processing and must be investigated/restored from backup.
- For a failed git push, preserve runner artifacts and retry against the current branch. Do not force-push or overwrite competing records. Workflows pull with rebase before pushing their new immutable transaction files.
- For a failed Pages deployment, rerun **Publish reporting site**. Forecast persistence is independent of the deployment step.

## Verification

```bash
ruff check src tests
ruff format --check src tests
pytest -q
python -m sleeper_nba validate
python -m sleeper_nba build-site
```

Tests cover numeric equations, aliases, zero/missing handling, incomplete coverage, clock/window boundaries, stale/mismatched prices, both-side value selection, payouts, pushes, chronology, weighting and shrinkage, component fit failures, immutable/idempotent storage, full fixture capture→grade→correct→refit→report, outage retries, restoration and schedule changes.

For an optional browser smoke test, install `pip install -e '.[browser]'`, then `python -m playwright install chromium`, serve `site`, and run `python tests/browser_smoke.py`. It checks all pages, stage/model controls, desktop/mobile layout and JavaScript errors. Browser testing uses a real generated site; no fabricated performance sample is published.

See [MODEL.md](MODEL.md) for registered methodology and implementation clarifications. The original `Dave356w/nba-matchups` repository is unchanged.

## v1.20 calibration transition

The selected residual-defense component uses 25 **calendar days**, with `w=2**(-age_days/25)`. The ridge prior is separate from decay: 5 fresh-game prior strength for defense and 20 for the offensive estimation control. Global and venue corrections are learned separately. Full selection evidence and caveats are in [docs/defense_selection_2025-26.md](docs/defense_selection_2025-26.md). Selection used point errors, not betting profit; the candidate did not outperform the old adaptive WP log loss.

First-stage team ratings can learn from the audited current-season legacy-first seed and prior native first snapshots. Locks use lock history only. Probability calibration uses **only new v1.20 frozen native forecasts**, with its original 75-game scale weighting and defaults until 30 eligible outcomes. Extra scalar margin/total biases remain zero to avoid double-counting the new global correction.

Existing frozen v1.19 forecasts are not rewritten. The Today page’s Latest view shows untracked v1.20 previews with first-stage parameters; it never creates historical bets or grades.

### Public v1.20 reporting

Today, Results, Performance and the public forecast CSV show only native v1.20 captures. The fixed control is compared on that same v1.20 cohort. Older and unversioned forecasts, grades and returns stay in the internal ledger and full `data/predictions.csv`; they are excluded from public metrics and downloads. A missing v1.20 sample remains empty rather than falling back to older results.

Results and Performance also show the archived 117-game current-season v1.20 first-snapshot replay as a separate historical summary: 86 correct and 31 incorrect winners. Its assumed result-availability times and previously inspected sample are disclosed. Probability metrics use the archived prior-only confidence-calibration replay. No historical forecast, bet or grade is added to the native ledger, and the replay is never pooled with live performance or reconstructed as five-hour locks. The public summary reads only the selected v1.20 candidate from the archived research output; alternative iterations remain internal.

### Internal research notes

[v1.20 versus market and the value-underdog favorite hypothesis](docs/v1_20_market_analysis_2026-11-09.md) records the 9 November 2026 findings, matched-price comparisons, selection returns, favorite moneyline/ATS reversals, source hashes and limitations. The favorite filter is an exploratory proposal, not an active selection rule. Reproduce the archived study offline with `python research/v1_20_market_analysis.py --root .`; the input snapshot and full results are retained under `research/inputs` and `research/output`.
