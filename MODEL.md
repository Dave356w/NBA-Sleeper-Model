# Sleeper-Only NBA Prediction Model

Implementation specification · v1.20 · 7 November 2026

Suggested repository: `sleeper-nba-model`  
Runtime: Python 3.12  
Deployment: GitHub Actions and GitHub Pages  
Status: Build specification for a new, independent model. No existing production repository is to be changed.

## Active methodology — v1.20 residual defense update

The owner requested a residual defense correction with a **25-calendar-day half-life**, followed by a last-season iteration comparison. This section overrides conflicting v1.19 adaptive scoring/bias rules below. Raw Sleeper aggregation and the permanently fixed control remain unchanged. Previously frozen records are never rewritten.

### Selected scoring component

Fit historical team-point residuals as `actual_points - frozen_raw_Sleeper_points = global + venue + offense_control(scoring_team) + defense(opponent) + noise`. Use centered team effects and weighted ridge regression. Offensive effects are nuisance controls during estimation; they are **not** applied to projected scores. The selected family applies global, venue and opponent-defense corrections only:

```text
H_adjusted = H_raw + global + venue/2 + defense(away)
A_adjusted = A_raw + global - venue/2 + defense(home)
M_adjusted = H_adjusted - A_adjusted
T_adjusted = H_adjusted + A_adjusted
w_i = 2 ** (-(fit_cutoff_utc - historical_tip_utc).total_seconds() / 86400 / 25)
```

Recency weighting and zero-centered shrinkage are separate. Defensive ridge strength is **5 fresh team-game observations**, offensive control strength **20**, and global/venue strengths **100** each. Require 30 eligible stage-specific games. Reject nonfinite or unstable coefficients; preserve accepted coefficients on fit failure. Refit at most once per UTC day per stage and season. Age is measured from historical tip, while result verification determines eligibility. Decay is not measured from the import timestamp. Both team residuals share their game's weight.

### Selection evidence and limitations

The historical comparison held the 25-day half-life fixed. It tested three effect families and priors 0/5/10/20/40/80/160, with raw, global-only and existing adaptive baselines: 24 alternatives including controls. October–January was warm-up, February–March selected the family/prior using `0.5*(margin_MAE/14 + total_MAE/19)`, and April was a separated final check. The chosen family uses a defense prior of 5 and offense nuisance control of 20. No betting-profit search was performed.

April margin MAE was 11.430 versus raw 12.354; total MAE was 15.282 versus raw 15.553. Existing adaptive WP log loss was better (0.4185 versus the candidate's fixed-scale 0.4351). This is a point-forecast selection, not proof of a superior complete probability or betting model. The archive's pregame availability is unverified, and both seasons had already been explored. April is not pristine untouched evidence. See `docs/defense_selection_2025-26.md` and the archived selection summary.

### Historical initialization and prospective integrity

For the requested incorporation, **only first-stage team residual fitting** is initialized from the 117 audited 2026–27 legacy-first snapshots already analyzed. The normalized seed has source hashes, frozen projection times, independently retrieved ESPN final scores and their verification times, and a pinned payload hash. Game 401909904 is excluded for Indiana's 198 projected minutes. The source legacy CSV lacks original raw stat objects, as disclosed in the research report. This seed is parameter-training history, never native forward performance, and is never inserted as native forecasts, bets or grades.

The lock stage cannot use first-stage seed observations. It starts at zero team corrections and waits for 30 native lock outcomes. Stage histories, season resets and duplicate game IDs are explicit; native observations supersede duplicate legacy observations. Results must be verified before the fit cutoff, and the fitted version must predate each forecast capture.

### Confidence calibration and scoring versions

New adaptive forecasts are tagged `sleeper_only_v1_20`, store raw and adjusted H/A/M/T, component corrections, exact parameters and matchup-version IDs. WP uses the corrected margin; ATS and totals use corrected margin/total. Old v1.19 probability calibration is not reused on the changed scoring geometry.

Confidence calibration starts at a=10, ATS sigma=14 and totals sigma=19 and learns only from completed, frozen **v1.20 native** forecasts. Its existing 75-completed-game weighting and scale regularization remain unchanged; the new **team-residual component** uses 25 calendar days. Additional scalar ATS/totals biases are held at zero because global/venue mean corrections are already applied by the matchup component. Uncertainty fits use errors against the exact corrected forecasts recorded before each game; no in-sample reapplication of today's ratings to historical targets.

The fixed control always retains raw Sleeper scores and a=10/ATS14/total19. Existing first and lock records keep their original methodology and grades. Owner reporting update (9 November 2026): public pages, metrics and forecast downloads show only native v1.20 captures, with the fixed control evaluated on that same cohort. Older and unversioned histories remain internal. The archived 117-game current-season v1.20 first-snapshot replay is a separately labeled historical summary, with assumed result-availability times disclosed; it never enters native forecasts, grades, betting returns or live performance counts. The older-version selector is removed.

### Latest previews and public display

The Today page also offers a **Latest · untracked** view, using current Sleeper inputs and the latest first-stage parameters. It shows adjusted adaptive scores, raw scores and correction amounts. These previews are derived, replaceable informational output; they never register selections, receive grades or enter training. First and lock tabs continue to render immutable forecasts, with their original model version. Captures at or after tip are rejected. ESPN is still the only odds source, timestamped when fetched.

The seed and matchup-calibration records are durable and auditable. Public defense tables distinguish legacy training counts from native training and forward evaluation counts. Recovery must restore `data/calibration_seeds` along with transactions. Never silently change the seed or its pinned hash.

## Revision and scope

This edition incorporates the requested v1.18 sanity-check revisions. The supplied source PDF labels itself Version 1.0; an independently identified v1.18 source was not supplied. Version 1.19 is the revision label for this replacement specification, not evidence of an inspected v1.18 implementation.

This document supersedes the supplied PDF as build instructions. It preserves its Sleeper-only scoring architecture, full-roster aggregation, initial parameters, separate fixed controls, historical weighting, and reporting objectives. It clarifies chronology, lock timing, push handling, missing markets, calibration safeguards, schedule revisions, and reproducibility. No application has been built or live data contract verified by this document revision.

## 1. Primary instructions and deliverables

Build a new repository containing a complete, independently functioning NBA prediction system. Sleeper projected player points are the sole source of forecasted scoring. Projected minutes validate coverage; they do not rescale points. Schedule/results providers identify games and actual outcomes. Sportsbook data supplies comparison lines and payouts, never scoring features. Historical outcomes may update only the explicitly defined calibration parameters.

Produce home/away projected scores, margin, total, win probabilities, ATS and totals probabilities and selections, offered-price expected value, immutable first and lock forecasts, automatic grading, calibration history, public reporting, and internal chronological research tools.

Do not import Four Factors, efficiency ratings, historical team features, BPM, trained parameters, predictions, or betting decisions from `Dave356w/nba-matchups` or any other model. Reference source code may inform API integration only. Own the new pipeline, configuration, tests, calibration history, and performance records. Do not modify the existing production repository. Do not execute wagers.

## 2. Architecture and processing sequence

Keep ingestion, validation, prediction, calibration, market evaluation, storage, grading, research, and presentation separate.

1. Retrieve the schedule and retain its revision and UTC capture time.
2. Map games, teams, and individual Sleeper player-game projections.
3. Preserve raw responses and validate full-roster coverage.
4. Load the latest eligible calibration version for the prediction stage.
5. Calculate raw scores and fixed/adaptive outputs.
6. Capture independently validated market data where available.
7. Atomically persist eligible first or lock forecasts, with explicit market availability.
8. Verify completed results and append grading revisions.
9. Refit from eligible prior observations and publish a new calibration version.
10. Render public pages from durable records.

A rendering or odds failure must not erase an accepted score forecast. Refitting never changes a recorded prediction.

## 3. External data contracts

### Sleeper

Reference endpoints, subject to live verification:

- `https://api.sleeper.app/v1/state/nba`
- `https://api.sleeper.app/v1/players/nba`
- `https://api.sleeper.com/projections/nba/{season}/{week}?season_type=regular`

The season is its starting year: 2026 for 2026–27. Fetch adjacent weeks when needed. Filter by the correct game, matchup, and slate date; never sum an entire week's player projections into one game. Verify that the feed exposes sufficiently granular player-game forecasts before enabling live operation. Ambiguous game associations must be rejected.

Projected playing time uses the `sp` statistic in seconds; `minutes = sp / 60`. Require projected points and minutes. Preserve other supplied statistics for inactive research only. Implement schema checks, finite-number checks, timeouts, bounded retries, rate limiting, and source-availability reporting. Preserve retrieval time and provider update time when available; never fabricate a provider timestamp.

### Schedule and results

Use a verified consistent provider, initially ESPN's scoreboard:

`https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD&limit=100`

Require game ID, canonical teams, UTC tip, status, competition classification, and final scores. Include overtime in final outcomes. Primary operation covers regular-season games only. Exclude preseason, exhibitions, and All-Star games. Play-in and postseason require a separately registered extension. Classify NBA Cup games according to whether they count in regular-season standings; exclude a non-regular-season championship game from the primary set. Store the source classification and mapping decision.

### Markets

Capture book, provider event ID, both moneylines, signed home spread and opposite away line with both prices, total with Over/Under prices, retrieval time, and provider update time where available. Prefer DraftKings when reliably accessible. Other books remain separately labeled datasets. Never silently substitute books or combine sides from different books, events, lines, or incompatible timestamps.

Initial eligibility policy: the market request must finish within five minutes of its paired Sleeper capture, and available provider update times must be no older than 15 minutes at market retrieval. Reject timestamps in the future beyond a documented 60-second clock tolerance. Owner update (7 November 2026): use ESPN as the sole odds source. Timestamp each quote when its response finishes. If the provider update time is absent, the fetch timestamp establishes operational eligibility; do not infer or claim the sportsbook update time. No Odds API integration or key is required. These conservative operating defaults are versioned configuration, not claims about feed update frequency.

Opening and closing prices require genuinely observed or provider-timestamped records. Never label a first-seen quote as the true opener without evidence. Missing or stale odds do not block score forecasts.

## 4. Player processing and validation

Use canonical NBA IDs. Support aliases including CHO→CHA, BRK→BKN, PHO→PHX, GS→GSW, SA→SAS, NY→NYK, and UTH→UTA. Never match a game by player name alone.

Persist player ID/name, game ID, team/opponent IDs, snapshot times, projected points/minutes, injury designation, and raw-response reference/hash. Deduplicate by game, snapshot, team, and Sleeper player ID. Exact duplicate rows may collapse deterministically with an audit event; conflicting duplicates invalidate the affected game snapshot.

Use all valid projected players. No Top-N truncation or independent injury adjustments. Preserve legitimate zeros. Missing numbers are not zeros. Negative or nonfinite points/minutes, contradictory team assignments, and zero-minute players with positive points invalidate the affected snapshot pending a corrected source response; never silently repair them or drop their contribution.

Require both teams, valid game associations, and `200 <= summed_team_minutes <= 280` for each team. Approximately 240 minutes is the regulation target, not a scaling factor. Complete validation and capture must occur before tip. Record all rejections. Invalid coverage cannot produce a valid forecast or research bet.

## 5. Raw scoring model

```text
H = sum(home projected player points)
A = sum(away projected player points)
M = H - A
T = H + A
```

Preserve H, A, M, and T unchanged. Positive M favors home. Do not add independent home-court, pace, injury, efficiency, or other features. Public projected team scores remain H and A. Show calibrated ATS margin and calibrated total as separately labeled quantities; do not imply they are a single jointly fitted score distribution.

## 6. Win probability and adaptive WP calibration

```text
Initial a = 10.0
P_home = Phi(M / a)
P_away = 1 - P_home
```

Phi is the standard normal CDF. At M=0, publish 50/50 and no predicted-winner bet. Otherwise the selected winner follows M. The scale changes confidence, not direction.

For eligible historical game i, let M_i be its frozen raw margin and y_i indicate a home win. Fit winner outcomes, not scoring-error standard deviation:

```text
p_i(a) = Phi(M_i / a)
L(a) = -sum(w_i * [y_i*log(p_i(a)) + (1-y_i)*log(1-p_i(a))])
a_fit = argmin L(a), with 6 <= a <= 20
```

Use a deterministic optimizer over log(a), stable log-CDF/log-survival calculations, and recorded optimizer tolerances. For reported log loss only, clip probabilities to [1e-12, 1-1e-12].

Historical age is the rank among eligible completed games in the same stage, newest age zero. Order by result verification UTC, then canonical game ID to break ties. Freeze that ordering in the calibration manifest.

```text
w_i = 2 ** (-age_i / 75)
N_eff = sum(w_i)**2 / sum(w_i**2)
alpha = N_eff / (N_eff + 100)
a_target = exp((1-alpha)*log(10) + alpha*log(a_fit))
```

Do not normalize weights to sum to N or 1. These weights and prior strengths are part of the model definition. They retain a permanent prior influence: asymptotically sum(w)≈108.7, N_eff≈216.4, and alpha≈0.684. This is intentional persistent regularization.

Require 30 eligible games. Below that threshold use stage defaults. A failed subsequent fit retains that component's last accepted parameters. Reject nonconvergence, nonfinite output, flat/unidentified objectives, or a solution within 0.001 of a search endpoint; record the rejected candidate and reason. A flat objective means max-minus-min objective below 1e-8 over a recorded 101-point log-spaced diagnostic grid.

Apply a_target only after validation, then cap accepted a to [0.90*a_previous, 1.10*a_previous]. Permit at most one accepted refit per UTC day per stage and only if the eligible observation manifest changed. New versions apply only after acceptance.

## 7. ATS prediction and calibration

```text
Initial bias mu_M = 0.0
Initial sigma_M = 14.0
e_M,i = actual_home_margin_i - frozen_raw_margin_i
mu_M,target = alpha * sum(w_i*e_M,i) / sum(w_i)
```

Require 30 eligible stage observations. Bias and uncertainty use the same stage-specific eligible observations and weights as defined above, without filtering for available odds or bet qualification.

Validate a finite bias target within [-15, +15] points. Cap accepted bias movement to ±1 point per accepted daily refit, relative to the prior accepted bias. Zero is a valid starting bias. Calculate the uncertainty target around the accepted bias:

```text
sigma_M,target**2 =
  [100*(14**2) + sum(w_i*(e_M,i - mu_M,accepted)**2)]
  / [100 + sum(w_i)]
```

Require finite sigma target within [5, 35] points, then cap its daily movement to ±10% of the previous accepted sigma. Accept or reject the ATS bias/SD pair atomically; a rejected pair retains both previous values. These broad sanity bounds and movement limits are registered safeguards, not fitted hyperparameters.

```text
M_ATS = M + mu_M
S = signed home spread  # favorite by 5.5 means -5.5
ATS_edge_points = M_ATS + S
P_home_cover = Phi((M_ATS + S) / sigma_M)
P_away_cover = 1 - P_home_cover
```

The probability equations apply to half-point main lines as specified in Section 12. Positive point edge selects home; negative selects away; zero means no lean. Record all eligible directional leans, plus a separate subset with absolute edge at least 3.0 points. Require valid two-sided main-line prices, each between -135 and +115 in valid American-odds encoding. Tag these as directional research selections; positive EV is not an added qualification for this original subset.

## 8. Totals prediction and calibration

```text
Initial bias mu_T = 0.0
Initial sigma_T = 19.0
e_T,i = actual_total_i - frozen_raw_total_i
mu_T,target = alpha * sum(w_i*e_T,i) / sum(w_i)
T_adjusted = T + mu_T
```

Require 30 eligible games. Validate finite bias target within [-25, +25], cap bias movement to ±1 point per accepted daily refit, then calculate:

```text
sigma_T,target**2 =
  [100*(19**2) + sum(w_i*(e_T,i - mu_T,accepted)**2)]
  / [100 + sum(w_i)]
```

Require finite sigma target within [7, 45], then cap daily sigma movement to ±10%. Accept/reject the totals bias/SD pair atomically. Below minimum history use defaults; after failure preserve the last accepted pair. Keep ATS and totals component statuses independent.

```text
Total_edge_points = T_adjusted - L
P_over = Phi((T_adjusted - L) / sigma_T)
P_under = 1 - P_over
```

These equations apply to half-point main lines. Positive point edge means Over; negative means Under; zero means no directional lean. Record every eligible directional lean. Separately record a probability-value subset when either side exceeds its no-vig market probability by at least 0.05. Evaluate both sides; the value side can differ from the directional lean. Preserve the original threshold without adding an EV filter, but display actual offered-price EV prominently: no-vig advantage alone does not guarantee positive EV. Require the same two-sided price range and freshness rules as ATS.

## 9. Fixed controls and model coherence

The fixed control permanently uses WP a=10, ATS bias=0 and SD=14, totals bias=0 and SD=19. It never refits. The adaptive model uses accepted stage-specific parameters.

Both variants receive identical eligible projections and market snapshots. Persist and grade each separately. Compare probability quality on identical games; compare selection returns both on their own registered selections and on the common opportunity set, with denominators shown.

Separate WP and ATS calibration can yield different home probabilities at spread zero. This is an acknowledged property of independent predictive models. Do not blend them into a single probability or imply distributional consistency. Any joint distribution or alternative error model is a new registered methodology version.

## 10. Chronology, calibration eligibility, and reproducibility

For every training observation and new forecast require:

```text
historical_forecast_capture < historical_tip
historical_result_was_unknown_at_historical_forecast
historical_result_verified <= training_cutoff
training_cutoff <= calibration_created <= new_forecast_capture
```

The historical game must be final and its eligible stage forecast complete, immutable, and valid at its recorded schedule cutoff. Include each game instance once per stage calibration set, using raw frozen scoring inputs once, not one observation per model variant. Do not condition residual training on available odds, selected bets, or profitability.

Define a slate by America/New_York scheduled game date; store timestamps in UTC. Refit once that slate's non-postponed games are final and verified. Postponed/cancelled games do not indefinitely block refits. A suspended unfinished game contributes only after its final result is verified. The new version affects subsequent captures only; games already frozen retain their versions. Do not wait for a whole slate to grade individual verified finals.

Keep first and lock histories separate, with independent stage defaults and minimum sample checks. No late-pregame substitute for a missed lock. No legacy seeding in the initial native experiment. Historical research imports remain separately labeled unless a later explicit model version authorizes seeding.

Persist version ID, creation/acceptance times, stage, cutoff, code commit, configuration hash, dependency/optimizer versions, training observation IDs, forecast hashes, result revisions, verification times, age/weight order, sample count, N_eff, weight sum, half-life, priors, fit targets, accepted parameters, movement caps, diagnostics, and component statuses. Hash the full manifest. Retain prior versions. Final-result corrections create a new manifest and prospective fit, never a rewritten historical forecast.

## 11. First snapshots, five-hour locks, and schedule changes

### First snapshot

Write the earliest successfully captured and validated complete pregame Sleeper snapshot once, with raw inputs and both model outputs. It need not have odds. Use response completion time as capture time, retain request start/end, and reject captures at or after tip or when a reliable live-status source shows the game has started. A failed validation does not consume the first slot.

### Five-hour lock

Use a narrow explicit window:

```text
target = scheduled_tip_utc - 5 hours
target <= snapshot_capture_utc <= target + 15 minutes
```

Freeze the first valid capture in that window. Display actual lead time; “five-hour lock” denotes this stated tolerance. A first snapshot can also serve as the lock when it falls in the window. If no valid capture exists, append a missed-lock event after the window closes. Later updates may be displayed as unlocked but cannot become the lock or enter lock calibration.

Odds are optional for a score lock. When present, both paired captures must meet freshness rules, finish within the lock window, and precede tip. Persist either the contemporaneous market evaluation or its unavailability reason. Never attach later odds to turn an immutable unavailable lock into a tracked bet. A later quote is a separately labeled informational record.

### Schedule revisions

Preserve every observed tip revision. Before capture, use the latest verified schedule. Associate each frozen forecast with its exact schedule revision and game-instance ID. A game instance identifies a scheduled occurrence even if the provider reuses its game ID.

Any change of scheduled date, formal postponement/cancellation, or cumulative tip shift exceeding 15 minutes from the frozen schedule retires that instance for the primary experiment. Append a void reason to its betting grades and exclude it from primary forecast metrics and calibration while preserving all records. If the game is rescheduled, create a new linked instance and apply first/lock eligibility anew; no retrospective captures. Tip revisions of at most 15 minutes retain the frozen instance and original timing evidence, with the change flagged. Normal actual tip delays without a revised schedule do not create a new instance. Provider IDs and links prevent counting both instances of one played game in primary results.

## 12. Market probabilities, pushes, EV, and selection

Valid American odds are finite values <= -100 or >= +100. Preserve actual offered prices.

```text
q(+o) = 100 / (100 + o)
q(-o) = abs(o) / (100 + abs(o))
q1_no_vig = q1 / (q1 + q2)
q2_no_vig = 1 - q1_no_vig
edge = model_probability - no_vig_probability
b = o/100 for positive odds; 100/abs(o) for negative odds
EV = P(win)*b - P(loss)
```

The initial primary ATS/totals betting and probability evaluation supports half-point main lines only. This avoids an unmodeled push mass while preserving the original normal-CDF formulas. Integer and other line increments are retained as informational market records with eligibility reason `unsupported_line_increment`; do not report their normal-CDF values as calibrated win probabilities or EV. Raw score forecasts and residual calibration remain eligible regardless of line increments.

On a supported non-push market, P(loss)=1-P(win). For any future push-capable extension, explicitly model P(win), P(loss), and P(push), summing to 1; use the general EV formula above and P(win)/(1-P(push)) for metrics conditional on no push. Such an extension requires a new model version. The grading engine must still correctly handle imported research pushes: zero units, excluded from binary probability scores, separately counted.

Register a moneyline directional series for predicted winners when qualifying two-sided prices exist. Separately evaluate both home and away for the value series: require at least 0.05 no-vig advantage and positive EV at the offered price. A value selection need not be the predicted winner. Keep favorites and underdogs separate. At equal model probabilities there is no directional winner selection, but either side can qualify on value.

Use flat one-unit stakes: win earns b net units, loss loses one, push returns zero, void is recorded and excluded. No wager execution. Each series has its own unique selection IDs; overlapping subset selections must not be counted twice in a combined headline bankroll.

## 13. Grading, metrics, and statistical comparisons

Grade verified finals including overtime. Store outcome provenance and append traceable grading revisions for corrections. Preserve forecast inputs and quoted prices. Unknown or unfinished outcomes remain pending.

Report WP accuracy, counts, log loss, Brier score, reliability bins, actual versus expected wins, and margin-band reliability. Report ATS margin and totals MAE/RMSE/bias, directional accuracy, probability scores on supported markets, edge bands, home/away or Over/Under splits, units, ROI, and valid CLV. Clearly define raw-error metrics versus calibrated-error metrics and use a consistent actual-minus-predicted bias sign.

ROI equals net units divided by total settled units risked; exclude voids and include pushes in the risked-stake denominator when applicable. Report wins/losses/pushes/voids/pending separately. Keep directional and value series separate, along with first/lock stage, fixed/adaptive variant, book, native/legacy provenance, and competition class. Do not combine these into a misleading record.

Closing quote policy: use the latest qualifying same-book capture before scheduled tip, no more than 15 minutes old at tip; otherwise CLV is unavailable. Report line movement and price movement separately. For moneyline report closing no-vig probability of the selected side minus capture-time no-vig probability. For ATS/totals, price CLV requires the same selected side and line; line CLV reports improvement in the number taken, with a positive sign for a better captured number. Never infer a true close from a later or first-seen quote.

Compare fixed/adaptive mean paired loss differences on common eligible games. Use 95% confidence intervals from a reproducible bootstrap clustered by slate date, retaining all games and both variants within a cluster; report sample/slate counts and mark intervals exploratory when fewer than 30 slates exist. Include chronological, monthly, and season-phase views. Historical tuning, training scores, and forward evidence must be labeled distinctly. No setting selected using an outcome may call it untouched forward evidence.

## 14. Storage and write integrity

Use durable storage across ephemeral runners. Immutable JSON/JSONL with validated CSV exports is acceptable if transactional integrity is implemented. CSV files are derived views, not the sole audit source.

Persist schedules/revisions, raw player snapshots, normalized inputs, forecasts, market snapshots, selections, calibration manifests, result revisions, grade revisions, and quality events. Keys include game instance, stage, model variant, book/market/series where applicable, and immutable record IDs. Store payload hashes and source references.

Serialize writers across capture, grading, refitting, and reporting workflows. Commit a complete logical transaction atomically; use a transaction manifest or equivalent so readers cannot consume half-written forecast/selection sets. Retry safely without duplicating records. Once accepted, forecasts survive renderer/deployment failures. A backup and restore drill must preserve IDs, hashes, and record counts.

## 15. Repository structure

```text
sleeper-nba-model/
  README.md, MODEL.md, AGENTS.md, requirements.txt, pyproject.toml, config.yaml
  src/sleeper_nba/
    __init__.py, __main__.py, cli.py, config.py
    sleeper_api.py, nba_schedule.py, odds_api.py, team_mapping.py
    validation.py, projections.py, win_probability.py, ats.py, totals.py
    calibration.py, snapshots.py, ledger.py, grading.py
    evaluation.py, storage.py, site.py
  model/
    initial_parameters.json, active_first.json, active_lock.json
    calibration_history.jsonl, manifests/
  data/
    games.csv, predictions.csv, odds_snapshots.csv, grades.csv
    quality_events.jsonl, snapshots/, transactions/, result_revisions/
  research/
    chronological_backtest.py, calibration_comparison.py, error_analysis.py, output/
  tests/fixtures/
  site/
    index.html, results.html, performance.html, methodology.html, assets/
  .github/workflows/
    capture.yml, grade-refit.yml, tests.yml, deploy.yml
```

Maintain boundaries rather than requiring a particular file count. Document schema versions, migrations, deployment, credentials, recovery, and operational limitations.

## 16. Public website

Build a responsive interface with light/dark support and compact game cards. Today shows matchup, tip, raw projected scores, WP, winner, raw margin, adjusted ATS margin, adjusted total, eligible lines/prices, leans, probability/value differences, EV, stage, variant, timestamp, actual lead time, and availability reasons.

Results shows frozen forecasts, finals, forecast errors, market grades, and units with date/stage filters. Performance separates the registered series and includes sample sizes, returns, reliability plots, error plots, and paired fixed/adaptive comparisons. Methodology explains Sleeper-only inputs, calibration, normal approximations, independent probability models, half-point restriction, timing tolerance, and feed limitations.

Distinguish first, locked, unlocked, missed, invalid, pending, and market-unavailable states. Public records derive from persisted data. Keep parameter searches, optimizer diagnostics, retired strategies, and exploratory model variants in internal research.

## 17. Automation and resilience

Capture throughout slates and poll around each T−5h target. GitHub cron is best effort; it cannot guarantee a 15-minute lock window. Detect missed windows and record them honestly. Document an external scheduler or dedicated watcher option if reliable lock coverage is required. Never fabricate an earlier capture.

Grade verified finals, then refit eligible completed slates under Section 10. Use one shared writer concurrency policy and durable transactions. Failures in ingestion, fitting, rendering, or deployment must not replace accepted data or calibration with partial output. Store credentials in GitHub secrets, redact logs, and provide bounded retry and recovery procedures. CI runs required checks for each change.

## 18. Command-line interface

```text
python -m sleeper_nba capture
python -m sleeper_nba capture --dry-run
python -m sleeper_nba grade
python -m sleeper_nba refit
python -m sleeper_nba report
python -m sleeper_nba build-site
python -m sleeper_nba backtest
python -m sleeper_nba validate
python -m sleeper_nba import-legacy --research-only
```

Dry-run performs no durable mutation, including no accepted snapshot or active-parameter updates. Historical imports are distinct, provenance-labeled, and cannot overwrite native records. Walk-forward research uses original capture and result-availability timestamps. If availability evidence is absent, disclose that no-lookahead guarantees cannot be established for that dataset.

## 19. Historical reference and initial experiment

The supplied source reports 118 matched first-snapshot games dated October 22–November 6, 2026: 82 correct winners (69.49%), fixed-10 log loss 0.5771, Brier 0.1977, margin MAE 10.88, margin residual SD 14.05, total MAE 15.27, total residual SD 18.88, and totals overprediction 4.24 points.

These are unverified source-document claims, preserved for reproducible research only. Require original snapshots, results, timestamps, and matching definitions before using them as regression checks. They are neither acceptance thresholds nor evidence of profitability. Historical records use `legacy-first`; never reconstruct a five-hour projection from a later capture. Start the native experiment at defaults with no legacy seed. Do not select settings by retrospectively maximizing profit.

## 20. Required tests

- Numeric fixtures: H=120, A=110 gives M=10, T=230, WP≈84.13%; S=-5.5 and sigma=14 gives home cover≈62.6%; total line 225.5 and sigma=19 gives Over≈59.4%. An informational integer line of 225 gives the original continuous-CDF diagnostic≈60.4%, but is excluded from primary probability/EV evaluation.
- Projection validation: zeros versus missing, finite/nonnegative values, inconsistent zero minutes, team aliases, exact/conflicting duplicates, missing teams, team-minute boundaries, and player-game mapping across weeks.
- Chronology: reject unknown-at-fit results and at/after-tip captures; accept prior verified finals; never duplicate observations across model variants; respect stage separation, revisions, and calibration acceptance times.
- Calibration: deterministic ages/tie order, unnormalized weights, N_eff, minimum history, stable optimization, endpoint/flat-objective rejection, independent component failures, daily caps, manifest replay, and unchanged fixed controls.
- Snapshots: first/lock write-once behavior, exact window boundaries, missing odds, fetch-timestamp eligibility without provider timestamps, no later odds backfill, failed retrievals, missed locks, schedule revisions, rescheduled instances, live-start detection, and idempotent retries.
- Markets/grading: signed spreads, opposing lines, valid odds encoding, prices and payouts, no-vig probabilities, EV, half-point eligibility, freshness, mismatched books/times, both-side value selection, overtime, pushes/voids, grading corrections, subset overlap, and ROI denominators.
- Operations: interrupted transactions, concurrent writes, durable restore, no mutation in dry-run, rendering failure after capture, and end-to-end fixture capture→persist→grade→refit→report→site.

## 21. Delivery phases

1. Foundation: packaging, configuration, schemas, documentation, CI, transactional storage.
2. Sources: verified adapters, player-game mapping, validation, provenance.
3. Fixed predictions: raw scores, WP-10, ATS-14, totals-19, market eligibility.
4. Tracking: first/lock timing, immutable selections, grading and metrics.
5. Adaptive calibration: stage histories, chronology, manifests, safeguards.
6. Research: reproducible walk-forward evaluation and paired diagnostics.
7. Public site: persisted predictions, results, performance, methodology.
8. Automation: unattended capture, grading/refits, deployment, backup/recovery.
9. Verification: end-to-end tests, live contract checks, data integrity, documented limitations.

## 22. Acceptance criteria

Declare readiness only when all required tests pass, live adapters demonstrate unambiguous game-level Sleeper ingestion, and a complete fixture run reproduces every forecast and calibration from durable records. Verify correct probabilities and payouts, independent calibration, stage-specific no-lookahead constraints, immutable first/lock records, missed-lock and rejection reporting, safe result revisions, and separate fixed/adaptive reporting.

Demonstrate unattended workflows and recovery from a failed capture/render/write. Provide a working public site and complete local/production instructions. Verify no changes to the existing production repository. A live data source that fails required fields or granularity is a documented launch blocker, not permission to substitute another scoring model.

## 23. Operating principles and handoff

Sleeper points determine raw expected scoring. Full valid rosters contribute without truncation. Historical outcomes calibrate only the specified winner, margin, and total components. Fixed controls remain frozen. Every accepted record has timing evidence and provenance. Research and forward evidence remain separate. Methodology, thresholds, and safeguards are versioned prospectively; no silent edits to prior forecasts or eligibility.

Initial model tags for this specification:

```text
sleeper_only_adaptive_v1_19
sleeper_only_wp10_ats14_tot19_fixed_v1_19
```

References for integration guidance only:

- https://github.com/Dave356w/nba-matchups/blob/main/sleeper.py
- https://github.com/Dave356w/nba-matchups/blob/main/market.py
- https://github.com/Dave356w/nba-matchups/blob/main/ledger.py
- https://github.com/Dave356w/nba-matchups/blob/main/README.md

Build a functioning tested system from this specification. Preserve its statistical definitions, raw scoring inputs, controls, timing rules, and reproducibility guarantees. Make documented engineering choices where details remain unspecified; a methodology change requires a new prospective version.


## Implementation clarification — owner updates, 7 November 2026

The target repository is `Dave356w/NBA-Sleeper-Model`. ESPN odds are the sole market feed and are timestamped on HTTP response completion. Unknown sportsbook update time is disclosed but does not disqualify a freshly fetched quote. Market requests must be within five minutes of the paired Sleeper capture, and lock quotes must finish inside the lock window. Closing comparisons are observed pre-tip quotes, not certified sportsbook closing prices. No Odds API service or secret is used.

Sleeper's explicitly empty `stats: {}` rows are unprojected roster placeholders. Preserve their numerical fields as missing, retain raw rows, and exclude them from projected scoring and minutes without imputing zero. A partially populated projection, missing stats object, or inconsistent numeric projection invalidates the snapshot. Both teams must still pass the projected-minute completeness bounds.
