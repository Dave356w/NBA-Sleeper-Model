# Sleeper-Only NBA Prediction Model

Implementation specification · v1.33 · 20 November 2026

Suggested repository: `sleeper-nba-model`  
Runtime: Python 3.12  
Deployment: GitHub Actions and GitHub Pages  
Status: Build specification for a new, independent model. No existing production repository is to be changed.

## Current model at a glance (v1.33, as of 20 November 2026)

Documentation only: this section restates the rules in force for new forecasts tagged `sleeper_only_v1_33`, in processing order, with the live `config.yaml` values. It changes nothing. Where it and a dated section below differ, the dated section governs and this summary is corrected. The sections below are the append-only history: each version's section overrides earlier ones, and the numbered base specification (§1–23) applies where nothing later overrides it.

1. **Capture.** Sleeper's game-specific player projections (company `rotowire`), the ESPN schedule and ESPN's DraftKings odds, each timestamped at fetch (§3; capture policy v1.21.1). The `watch.yml` job captures on its own schedule and `watchdog.yml` restarts it; no other writer.
2. **Raw scores.** `H` and `A` are the sums of the projected points of every valid projected player; each team's projected minutes must be 200–280 (§4–5). `M = H − A`, `T = H + A`. Raw scores are never changed and never include home-court, pace or injury terms. The fixed control scores them with a = 10, ATS SD 14 and totals SD 19 (§9).
   **Minutes cap** (v1.32, adaptive scoring only). A team whose projected minutes exceed 240 has its points scaled by 240 / team minutes before the team terms (`matchup.team_minutes_cap: 240`); a team at or below 240 is never scaled up. The team fit and the season replay train and score on the same capped points; seed and backfill minutes come from the pinned `matchup.seed_minutes` file. `H`, `A` and the fixed control stay as Sleeper projected them.
3. **Team adjustment** (v1.20, timing from v1.23, offense and priors from v1.25). After each fully graded slate, a weighted ridge regression fits `actual − raw team points = global + venue + offense_control(team) + defense(opponent)`, with weights halving every 25 calendar days from tip. The zero-centered priors are defense 20 games, offense 20, global 100 and venue 100; coefficients are bounded at ±30. The fit starts from the first graded game (`minimum_games: 1`). Its history is the pinned 117-game legacy-first seed, the pinned 17 November outage backfill (from its creation) and graded native first snapshots, a native game superseding a seeded one; a game with first snapshots under several tags uses its latest one (v1.28), and from v1.31 (`history_snapshot: game_day_latest`) only snapshots captured on the game's Eastern date qualify: a game-day native lock stands in for an earlier-day first snapshot, and a game with no game-day capture is left out. Applied to scores:
   ```text
   H_adj = H + global + venue/2 + offense(home) + defense(away)
   A_adj = A + global − venue/2 + offense(away) + defense(home)
   ```
   From v1.25 (`apply_offense: true`) each team's offense term is added to its own score; through v1.24 it was an estimation control only. `venue` is the model's home-court term, learned from results and shrunk toward zero. Locks use this same first-stage fit.
4. **Win probability.** `P(home) = Φ(M_adj / a)`; the predicted winner is the sign of `M_adj`, with no winner at zero (§6). Spread and total biases are held at 0 (`learn_scalar_biases: false`) because `global` and `venue` already correct the mean.
5. **Confidence calibration** (v1.24). The scale `a`, ATS SD and totals SD are refit at most once per UTC day, only from fully resolved slates. The history is the season's earlier games replayed walk-forward under the current method (`season.calibration_observations`; game-day snapshots only from v1.30), followed by native games of this tag. Weights halve every 75 games, at least 30 games are needed, the fit for `a` is bounded to 6–20 and shrunk toward 13.3 with prior strength 100 (v1.33; 10 through v1.32), `a` starts at 13.3 before the first accepted fit, and each refit moves at most 10% from the last accepted value. Locks use the first-stage calibration.
6. **Picks** (v1.22; value series retired in v1.27). Only the moneyline creates selections, and from v1.27 only the directional series (`selection_series: [directional]`): the predicted winner at the saved price. The value series (either side at least 0.05 above the no-vig probability with positive expected value, §12) no longer registers; edges and expected values stay on each market record as context. Quotes must be DraftKings, paired with the capture within 5 minutes and no more than 15 minutes old. Spread and total quotes are kept as context only.
7. **Timing** (§11; first snapshot from v1.27). The first snapshot is the earliest valid pregame capture on the game's Eastern calendar date that carries a qualifying DraftKings moneyline; if none has by the lock target (T−5h), it freezes unpriced at the first capture from then (`first_policy: game_day_priced_et_v1`). The lock is the first valid capture in the 15 minutes after T−5h; if that window passes with no valid capture, the first valid capture until T−2h is a late lock, labelled `lock_timing: late` with its real lead time (`five_hours_late_v3`, v1.29). A game with no valid capture by T−2h is recorded as missed and never backfilled. Nothing is captured at or after tip.
8. **Grading** (§13). Graded against ESPN finals including overtime. Score corrections are re-checked for 7 days after tip and graded as new revisions. Units are one per pick: payout on a win, −1 on a loss.
9. **Public record** (v1.22, 11 November reporting update). Results and Performance show one pooled first-snapshot record: live v1.33 forecasts plus the season's earlier games replayed under v1.33 from results known before each game. From v1.30 (`replay_snapshot: game_day_et_v1`) every replayed snapshot was captured on the game's Eastern date: an earlier-version first snapshot from an earlier day is replaced by that game's game-day native lock, and a game with no game-day capture is left out and listed in `season.replay_excluded`. Where a game's frozen snapshot has no price, its moneyline, spread and total are the DraftKings close from the pinned ESPN file (`closing_backfill`, v1.27); `price_basis` records which. The replay writes nothing to the ledger. Pages show the model-pick moneyline series and a rolling ROI chart; the value series and all parts of the record stay in `data.json` and the ledger (17 November update).
10. **Integrity.** The ledger in `data/transactions` is append-only and hash-verified. Accepted forecasts, grades and fits are never rewritten, and every methodology change is prospective and versioned (`AGENTS.md`).

## Active methodology — v1.33 win-probability scale centred at 13.3

Owner update, 20 November 2026. One change; everything else is unchanged from v1.32. New forecasts are tagged `sleeper_only_v1_33`; frozen records of every earlier version, and their grades, fits and calibrations, are never rewritten.

**What changed.** This section overrides §6's `Initial a = 10.0` and the `log(10)` in `a_target`, for the adaptive variant only. The adaptive scale starts at `wp_initial: 13.3` and is shrunk toward `wp_prior_centre: 13.3`:

```text
a_target = exp((1-alpha)*log(13.3) + alpha*log(a_fit))
```

The weights, prior strength 100, 30-game minimum, bounds 6–20, daily 10% movement cap, rejection rules and refit timing are unchanged. A configuration without `wp_prior_centre` keeps 10, so every earlier calibration and forecast re-validates unchanged. The fixed control keeps a = 10. The scale never changes the predicted winner, so picks, margins and totals are unaffected.

**Why.** Opening week (22–28 October, 54 games) rewarded a sharp scale (best fit 6.7), while games since 29 October support a softer one (12.4; 13.1 on the 127 also forecast by NBA Matchups). Anchored at 10, the v1.32 replay used a scale of 8.1–11.0 into November, and stated win chances ran too confident: calibration slope 0.86 ± 0.16 over the season (1 SE; 1.0 is calibrated), 0.68 ± 0.17 on those 127 games. 13.3 is the fitted margin sigma of the separate NBA Matchups model (`model/logit.json`, 11,687 games of earlier seasons), a number set before this season rather than chosen on these games.

**Evidence (retrospective, previously inspected games).** The pooled season record under v1.33 is the same 189 games with the same picks: 69.8% winners, margin error 11.48, model-pick ROI +3.1%. Log loss is 0.5998 against 0.5986 under v1.32 (paired +0.0012 ± 0.0053); Brier 0.2059 against 0.2053. Calibration slope is 1.01 ± 0.19 against 0.86 ± 0.16. By period: opening week +0.019 ± 0.010, from 29 October −0.006 ± 0.006. The replayed scale runs 9.2–13.3, ending at 12.8. The change is adopted for calibration, not for an expected gain in log loss, which the replay does not show.

## v1.32 methodology — 240-minute cap (scale down only, still current)

Owner update, 19 November 2026. One change; everything else is unchanged from v1.31. New forecasts are tagged `sleeper_only_v1_32`; frozen records of every earlier version, and their grades, fits and calibrations, are never rewritten.

**What changed.** This section overrides the base rule that projected minutes "do not rescale points" (§4–5), for the adaptive variant only. When a team's Sleeper projected minutes sum to more than 240, the adaptive scoring scales that team's projected points by 240 / team minutes before the team terms are added (`matchup.team_minutes_cap: 240`). A team at or below 240 is never scaled up. The forecast records the factor (`home_minutes_scale`, `away_minutes_scale` in the matchup adjustment). Raw `H` and `A`, the 200–280 minute validity check and the fixed control are unchanged.

The team fit trains on the same capped points, so its residuals match what it corrects, and so do the season replay and the confidence history built from it. Native games take their minutes from the frozen forecast's `team_minutes`. The legacy seed and outage backfill keep only team totals, so their minutes come from `data/calibration_seeds/seed_team_minutes_2026-27.json`, pinned by SHA-256 under `matchup.seed_minutes` (`{sha}`). `research/build_seed_minutes.py` builds that file from the same nba-matchups snapshots the seed was built from, and it refuses to write unless every team's player points reproduce the pinned seed score. A seed game without pinned minutes fails integrity rather than passing uncapped. Configurations frozen without the key keep uncapped scoring and re-validate unchanged.

**Why.** A team above 240 minutes is a counting error: Sleeper is crediting more than a game's minutes, usually a player who will not play still carrying his projection alongside a full rotation. A team below 240 is usually genuinely short-handed, so scaling it up adds points that do not exist. In the full-game scaling review, a team projected at 215 minutes had its error grow from 16 to 29 points when scaled up. About 12% of live team snapshots are more than five minutes off 240, against 4% in the seed's archive.

**Evidence.** The scale-down-only rule touches 7 of the 185 game-day games in the season replay, one team in each: 5 seed or backfill games and 2 native ones. On those teams the average team-points error fell from 8.7 to 6.8, with 5 of 7 closer to the final score. In the walk-forward replay (team fit and confidence scale refitted on capped points, paired against v1.31, 95% slate-bootstrap intervals):

- margin error −0.06 (−0.18 to +0.04);
- one more winner;
- log loss −0.007 (−0.020 to +0.003).

Every measure points the right way, but none is distinguishable from zero on seven games. The rule is adopted as a low-risk safeguard: it only ever lowers an overcounted projection. The pooled season record under v1.32 is 185 games: 69.7% winners, log loss 0.6014, margin error 11.51 and model-pick ROI +3.3%, against 69.2%, 0.6081, 11.57 and +0.9% under v1.31. The analysis is retrospective on previously inspected data, and the rule's direction was chosen after seeing the two up-scaled teams.

## v1.31 methodology — game-day snapshots in the team fit (still current)

Owner update, 19 November 2026. One change; everything else is unchanged from v1.30. New forecasts are tagged `sleeper_only_v1_31`; frozen records of every earlier version, and their grades, fits and calibrations, are never rewritten.

**What changed.** The live team fit (`matchup.eligible_observations`), which scores first snapshots and locks, now follows the v1.30 replay rule (`matchup.history_snapshot: game_day_latest`). For each game:

1. the latest game-day native first snapshot of any tag;
2. otherwise the latest game-day native lock, as a first-stage observation marked `snapshot_stage: lock`;
3. otherwise the game's seed or backfill snapshot, which must also be on game day;
4. otherwise the game is left out.

Fits recorded under `latest` (v1.28–v1.30) or without the key keep their histories and re-validate unchanged. The season replay already fitted its own team terms from game-day snapshots in v1.30, so the public record and the confidence history do not change. Only live forecasts' team terms change.

**Why.** It is consistent with v1.26 (first snapshots on game day) and v1.30 (replay on game day). Live forecasts and the public record now use team terms fitted from the same games.

**Effect.** On the ledger at 22:00 UTC on 19 November the fit goes from 207 to 185 games: 117 seed, 8 backfill, 28 game-day native first snapshots and 32 game-day locks, matching the replay. The 22 games of 8, 9 and 15 November with no game-day capture drop out. The global term moves from −0.91 to −0.83 and venue from 0.00 to −0.07. Team terms move by 0.28 points on average, the largest being LAL offense (−1.09), WAS defense (−0.81) and SAS defense (+0.77). On that night's four games the adjusted margin moved by 0.1–0.5 points. This is a consistency change, not a demonstrated gain.

**Review of the dropped games (owner request, 19 November; `research/team_fit_exclusion_replay.py`).** The season was replayed walk-forward twice on the same 185 game-day games, with the team fit trained either on those games only or also on the 22 day-before snapshots. Everything else followed the replay, and the extra games trained the fit only. Adding them changed 61 forecasts. Paired differences (with the 22 minus without, 95% slate-bootstrap intervals over 25 slates):

- margin error: +0.02 points (−0.04 to +0.09);
- winners: +0.5 points, one more game correct (0 to +1.9);
- log loss: +0.0014 (−0.004 to +0.006);
- log loss with the scale held at 10: +0.0015 (−0.003 to +0.006).

On the 61 changed games alone the differences were +0.06 points, one more winner and +0.0043. Leaving the games out has no measurable accuracy cost: the margin and probability estimates lean very slightly towards dropping them, and the winner count leans towards keeping them, all well inside the noise. The analysis is retrospective on previously inspected data, and no threshold was searched.

**Implementation fix, 19 November (package 1.31.1; no methodology change, tag unchanged).** §11 retires an instance when its schedule is revised: a date change, a postponement or cancellation, or a cumulative tip shift over 15 minutes. It also says normal start delays without a revised schedule keep the instance. The code applied the tip and date test at any status, so a start time ESPN rewrote after a game had started or finished would have retired a played game and voided its grades. A tip or date shift now retires the instance only while the game is still scheduled (`status: pre`). After the start, the instance and its result are kept, and the change is recorded once per reported tip and status as a `tip_revision_after_start` quality event. Postponements and cancellations still retire at any status. Nothing in the ledger was affected: no instance has been retired this season, and across 112 games and 360 schedule revisions ESPN has not moved a tip time.

**Full-game minutes scaling review (owner request, 19 November; research only, no methodology change; `research/minutes_reallocation_replay.py`).** The question was whether each team's Sleeper player sum should be scaled so its projected minutes make a complete 240-minute game. A team below 240 had the missing minutes handed to its projected players in proportion to each one's minutes, scaling each player's points by the same ratio. Added minutes were capped at 38 per player, with the excess passed to teammates still under the cap, and a player already above 38 kept his projection. A team above 240 was scaled down by 240 / team minutes. Player rows came from the frozen native forecasts and, for the seed and backfill games, from nba-matchups' `data/nba_sleeper.csv`, whose rows reproduce every pinned seed and backfill team total exactly (SHA-256 recorded in the output). Each variant re-ran the season replay on the same 185 game-day games, with the team fit and confidence scale refitted walk-forward. Paired differences are variant minus current, with 95% slate-bootstrap intervals over 25 slates.

- **Scaling to 240 minutes:** Sleeper already projects 361 of the 370 team-games at exactly 240 minutes, so only 9 team-games change (39 minutes added, 59 removed). Margin error −0.02 (−0.15 to +0.10), total error +0.12 (−0.08 to +0.41), one more winner, log loss −0.002 (−0.012 to +0.008). There is no measurable effect, and too few teams are off 240 for the rule to matter.
- **Also run (an earlier reading of the request): unavailable players dropped and their minutes handed to teammates by the same capped rule.**
  - Dropping Out, IR and suspended players changes 17 team-games, because Sleeper projects nearly every Out player at 0 minutes. Log loss +0.004 (−0.003 to +0.012), with no measurable effect.
  - Also dropping day-to-day and game-time-decision players changes 180 team-games. Margin error +0.88 points (+0.38 to +1.52), winners −5.4 points (−10.7 to −0.6), log loss +0.074 (+0.015 to +0.161). This is clearly worse: about half of those players play, so removing all of them throws away real information.

Sleeper's projections already allocate a full game's minutes for nearly every team, so neither two-way scaling nor reallocation is adopted. A scale-down-only rule was adopted separately as v1.32. The analysis is retrospective on previously inspected data; the owner set the full-game rule and the 38-minute cap before the run, and no threshold was searched.

## v1.30 methodology — game-day snapshots in the season replay (still current)

Owner update, 19 November 2026. One change; everything else is unchanged from v1.29. New forecasts are tagged `sleeper_only_v1_30`; frozen records of every earlier version, and their grades, fits and calibrations, are never rewritten.

**What changed.** The season replay (`season.py`), which builds the public season record and, from v1.24, starts the first-stage confidence history, now uses only snapshots captured on the game's Eastern calendar date (`replay_snapshot: game_day_et_v1`). For each earlier-version native game:

1. the latest game-day native first snapshot of an earlier tag, if one exists (as before);
2. otherwise the latest game-day native lock of an earlier tag, re-scored from its frozen raw Sleeper scores with its own contemporaneous quotes (`snapshot_stage: lock`);
3. otherwise the game's seed or backfill snapshot, if it has one, which must also be on game day;
4. otherwise the game is excluded from the replay and listed in `season.replay_excluded` with reason `no_game_day_capture`.

Seed and backfill snapshots are held to the same rule; all 125 in this season's record were captured on game day, so none is affected. Rows carry `snapshot_stage` in `data.json`. Configurations frozen without the key keep the latest earlier-version first snapshot whenever it was captured, so v1.24–v1.29 calibrations re-validate unchanged. In v1.30 the live team fit was not changed: it still trained on each game's latest native first snapshot (v1.28) until v1.31. Replay still writes no forecast, selection, grade or ledger record.

**Why.** From v1.26 a first snapshot must be taken on game day, but the v1.25–v1.29 replay used earlier tags' first snapshots as frozen, and those were often captured the day before. In the 207-game v1.29 record, 54 of the 82 native replay games (8, 9, 12, 13, 14, 15 and 18 November) came from snapshots 25–46 hours before tip, including all 12 games of 18 November, captured at 22:34 UTC on 17 November when the watcher restarted. 32 of the 54 have a game-day lock; 22 (8, 9 and 15 November) have no game-day capture.

**Effect.** The pooled record goes from 207 to 185 games (125 seed, 28 game-day first snapshots, 32 game-day locks). On the 32 games where both exist, the game-day lock picked 21 winners against 18 and its raw margin error was 13.35 against 13.65 points (paired difference 0.30, standard error 0.47). That is consistent with fresher projections but not distinguishable from zero. On its own games the pooled record moves from 71.0% winners, log loss 0.5934 and +3.8% model-pick ROI (207 games) to 69.2%, 0.6081 and +0.9% (185 games). The sets differ, so this is not a like-for-like comparison: the excluded games happened to favour the model. Confidence calibration starts a new v1.30 history from the revised replay.

**Display fix (no methodology effect).** When a slate's locks include late locks, the Today page's lock card reads "Locks" and gives the on-time and late counts. It no longer says every lock was captured near T−5h.

## v1.29 methodology — late-lock fallback (still current)

Owner update, 19 November 2026. This section overrides the v1.21.1 rule that a lock missed in its 15-minute window is never captured. Everything else is unchanged from v1.28. New forecasts are tagged `sleeper_only_v1_29`; frozen records of every earlier version, and their grades, are never rewritten.

**What changed.** `lock_policy: five_hours_late_v3` keeps the on-time lock, the first valid capture in the 15 minutes after T−5h. If that window passes with no valid capture, the first valid capture until `lock_late_hours` (2) before tip becomes the lock, labelled `lock_timing: late` and carrying its real lead time; an on-time v1.29 lock is labelled `on_time`. Validation checks the label against the capture time. Late locks are tracked, priced and graded like on-time locks; operational coverage counts them as locked and reports them separately (`late` per slate, and "late lock" on the game card). The watcher keeps its 60-second retries inside the on-time window and uses its ordinary tick in the late window. Locks frozen under `five_hours_15min_v2` or the v1.20.1 through-tip policy keep their windows.

**Evidence.** Of the 48 missed locks this season, 34 fall within the watcher's commit history. 33 were missed because the watcher was not running in the window (8–9 November, and the 14–17 November outage); one (GSW @ MEM, 19 November) because Sleeper's projection failed the 200–280 team-minute check for the whole window despite 13 attempts. In 8 of the 34 the watcher was running again by T−3h and in 11 by T−2h, so the fallback would have recovered about a third of these misses. Every v2 lock that did freeze was captured 4.93–5.00 hours before tip, so the on-time window itself is reliable.

**Operational changes (no methodology effect).** The watcher's pre-capture test run no longer blocks capture: on 14 November an aged test fixture failed it and every restart failed the same way for about 72 hours. Tests still gate merges in CI; in the watcher a failure is reported and the job ends failed after the watch. The watchdog now fails visibly when no capture has been committed for more than three hours or the last two watcher runs failed (normal gaps since self-chaining began have stayed under three hours). Rejected projections journal their per-team minutes, and the internal performance summary no longer depends on ledger file order.

## v1.28 methodology — latest native snapshot in the team-fit history (still current)

Owner update, 19 November 2026. One change; everything else is unchanged from v1.27. New forecasts are tagged `sleeper_only_v1_28`; frozen records of every earlier version, and their grades and fits, are never rewritten.

**What changed.** When a game has native first snapshots under several methodology tags, the team fit (`matchup.eligible_observations`) now trains on the latest of them (`matchup.history_snapshot: latest`; ordered by capture time, then record id). All first snapshots are pregame and game-day snapshots are always captured after earlier-day ones, so this is the snapshot nearest tip, and the game-day one whenever it exists. Until now the fit kept whichever snapshot came last in the ledger's hash-named file order, which is effectively arbitrary, while the season replay already used the latest snapshot (v1.25). Configurations frozen without the key keep their ledger-order choice; recorded fits keep their stored manifests.

**Effect at adoption.** In the team fit, 14 of 207 games change snapshot, from a median 27.6 hours before tip to 7.0 hours; snapshots under 24 hours rise from 143 to 153 games, the same set the season replay uses. Team terms move by at most 0.15 points (global −0.97 to −0.91) and tonight's margins by at most 0.2. The season record is unchanged (147–60, log loss 0.5934, +7.78 units), because the replay already used these snapshots. The remaining 54 games with only an earlier-day snapshot are kept; dropping them was considered and rejected because it would remove about 30% of the history.

## v1.27 methodology — priced first snapshot, closing-price backfill, value series retired (still current)

Owner update, 19 November 2026. Three changes, all prospective; everything else is unchanged from v1.26, including scoring (applied offense, defense prior 20), the T−5h lock policy, calibration and moneyline-only selections. New forecasts are tagged `sleeper_only_v1_27`; frozen records of every earlier version, and their grades, are never rewritten.

### What changed

1. **The first snapshot waits for a price** (`first_policy: game_day_priced_et_v1`). On the game's Eastern date, a capture freezes the first snapshot only if its own fixed-variant evaluation has a qualifying DraftKings moneyline (paired, fresh, valid two-sided prices). If no capture qualifies by the lock target (T−5h), the first capture from then freezes it unpriced, so every game still has a first snapshot. Validation applies the same rule to each frozen record (`common.first_window_open`, `first_is_priced`); configurations frozen under `game_day_et_v1` or without `first_policy` keep their rule. nba-matchups' game-day first snapshots this season (12–24 hours before tip) had a DraftKings moneyline in all 211 games, so the wait is expected to be short.
2. **Closing-price backfill for the season record** (`closing_backfill`, reporting only). `research/build_closing_backfill.py` writes `data/market_backfill/espn_draftkings_close_2026-27.json`, hash-pinned in `config.yaml`: for every finished season game whose ESPN home and away teams match the schedule, ESPN's DraftKings close (both moneylines, home spread with prices, total with prices) and open. `season.with_closing_prices` uses a close only where the game's frozen snapshot left that market unpriced; a snapshot price is never replaced. Each public row carries `price_basis` (`snapshot` or `closing`, for the moneyline), spread and total carry `line_basis`, and `data.json` reports the record split by basis. Nothing is written to the ledger, and closing prices never enter scoring, calibration, forecasts, selections or grades.
3. **The value series is retired** (`selection_series: [directional]`). New forecasts register only the model pick; no value selection is created, so none is graded or counted. Each moneyline market record still carries the model and no-vig probabilities, edges and expected values. Selections frozen under earlier versions, including their value selections and grades, are unchanged, and configurations frozen without `selection_series` keep every series. The season record's `value` figures in `data.json` remain a replay diagnostic of the old rule; the pages already showed only model picks.

### Evidence and its limits

- At adoption 57 of the 207 season games had no snapshot price: 55 earlier-version native first snapshots captured about 40 hours before tip, before odds were posted, plus 2 seed games. ESPN closes cover all 207 games. On the 47 games where this ledger also recorded the watcher's own DraftKings quote in the last 15 minutes before tip, ESPN's close matched the moneyline exactly in 35, and the no-vig home probability differed by +0.0001 on average (largest 0.036); spreads matched in 41.
- With the backfill, the pooled record has 207 priced games: model picks 147–60, +7.78 units (+3.8%); on the 57 closing-priced games 42–15, +5.66 units; on the 150 snapshot-priced games unchanged (105–45, +2.12 units). Value picks −13.54 units (63–55). Model log loss 0.5934 against the market's 0.5735.
- A close is a later, sharper price than was available when a 40-hour snapshot was frozen. It is a stricter benchmark for probability comparisons, but returns at the close are not returns that could have been taken at snapshot time; the split by `price_basis` stays in `data.json`.
- Value series at retirement, on the pooled record: 49–37, −9.61 units on 86 snapshot-priced bets, and −13.54 units on 118 bets with closing prices included. Larger edges did worse (10%+ edges 13–13, −5.2 units on snapshot prices). It never showed a positive return beyond noise in any version.
- The file is fixed at its creation. Later games are priced by their own snapshots; a game left unpriced after the file was built stays unpriced until the file is rebuilt and re-pinned.

## v1.26 methodology — game-day first snapshot (superseded by v1.27 for new first snapshots)

Owner update, 19 November 2026. This section overrides the §11 rule that the first snapshot is "the earliest valid pregame capture". Everything else is unchanged from v1.25, including scoring (applied offense, defense prior 20), the T−5h lock policy, calibration, moneyline-only selections and the fixed control. New forecasts are tagged `sleeper_only_v1_26`; frozen records of every earlier version, and their grades, are never rewritten.

### What changed

1. **The first snapshot waits for the game day** (`first_policy: game_day_et_v1`). It is the earliest valid pregame capture whose Eastern (America/New_York) calendar date is the game's slate date, so for a 7 p.m. ET tip it is frozen from about 19 hours before tip instead of about 40. Captures before that date still produce the untracked Latest preview.
2. **Validation checks the policy each forecast was frozen with.** A first snapshot frozen under `game_day_et_v1` before its game day is rejected. Configurations frozen without `first_policy` keep the earliest valid capture, and an unknown policy is an error.
3. **Switchover.** On the first capture under v1.26, games already on their game day freeze their v1.26 first snapshot at that capture (tonight's slate, about 8 hours before tip); their v1.25 first snapshots stay in the ledger and, as earlier-version captures, are not used by the season record once a v1.26 forecast exists.

### Evidence and its limits

`python research/snapshot_timing.py --root . --sleeper-csv <nba-matchups>/data/nba_sleeper.csv` writes `research/output/snapshot_timing_2026-27.json`. Team scores are sums of raw Sleeper points against the pinned ESPN box scores.

- On the 52 games with all four snapshots, against this ledger's first snapshots (median 39.7 hours before tip): nba-matchups' game-day first snapshots (18.8 hours) have total MAE 12.56 vs 14.12 (−1.56, 95% interval −2.47 to −0.62) and margin MAE 12.87 vs 13.26 (−0.39, −0.96 to +0.17); T−5h locks have total MAE −1.06 (−1.94 to −0.13) and margin MAE −0.42 (−1.11 to +0.23). Projected points of players who did not play fall from 25.7 to 18.1 per game.
- On 208 games, the 18-hour and 1-hour snapshots are indistinguishable (margin +0.04, total −0.02), so the gain comes between about 40 and 18 hours, when day-before injury reports and rotation news arrive; moving the lock closer to tip is not supported.
- The total gain is significant on one sample of 52 games; the margin gain is directional only. These are raw sums: the team fit and confidence calibration are refitted on the corrected scores and may absorb part of the difference. The legacy seed already came from nba-matchups' game-day first snapshots, so from v1.26 seeded and native first snapshots have similar lead times.
- A pregame player-prop rule (scaling Sleeper projections of players without a points prop to 0.34 or 0.66) was tested on the 12 games with props and rejected: totals were biased by +12 to +29 points and margins did not improve. Props arrive about 20 minutes before tip and are market data, so they are not a scoring input.

## v1.25 methodology — team offense applied, defense prior 20 (still current)

Owner update, 19 November 2026. After a walk-forward review of team-specific terms the owner selected the best-scoring variant and asked for it to be implemented and the season replayed under it. This section overrides conflicting text below, including the v1.20 and v1.21 rules that offensive effects "are nuisance controls during estimation" and "are not applied to projected scores", and the v1.21 defensive prior of 40. Everything not named here is unchanged from v1.24, including the fit itself (same features, 25-calendar-day decay, offense prior 20, global/venue priors 100, ±30 bound, slate refits, first-stage fit on locks), confidence calibration from the season history, moneyline-only selections and the fixed control. New forecasts are tagged `sleeper_only_v1_25`; frozen records of every earlier version, and their grades, are never rewritten.

### What changed

1. **Offense is applied** (`matchup.apply_offense: true`). The fitted `offense_control(team)` coefficient is added to that team's own corrected score:
   ```text
   H_adj = H + global + venue/2 + offense(home) + defense(away)
   A_adj = A + global − venue/2 + offense(away) + defense(home)
   ```
   Offense and defense terms are each centered at zero across teams, so the change moves individual games, not the league mean. Forecasts record `home_offense` and `away_offense` in their adjustment.
2. **Defensive prior 40 → 20** fresh team-game observations (`matchup.defense_prior: 20`).
3. **Frozen settings decide.** `matchup.apply` reads `apply_offense` from the fit record's own frozen settings. Every fit recorded under v1.20–v1.24 lacks the key and reproduces with defense only; a replay fit, which carries no settings, uses the current configuration.
4. **Deterministic replay snapshot.** When a game has first snapshots under several earlier tags, the season replay uses the latest of them (ordered by capture time, then record id). Before, the choice followed the ledger's hash-named file order. This is presentation and replay only: no record changes, and it is not used in validation.

The season record on the public pages is the pooled v1.25 record: every earlier game replayed under v1.25 from results known before it (seed and backfill games, and the native first snapshots of v1.19–v1.24, whose frozen raw scores are re-scored), plus live v1.25 forecasts as they are graded. Confidence calibration starts its v1.25 history from that replay at the first capture under the new tag.

### Evidence and its limits

- Walk-forward replay on this season's 207 games (27 slates), production fit code, each game scored from a fit built at the noon-UTC cutoff before its snapshot, win probability at fixed a = 10: `python research/team_terms_replay.py --root .` writes `research/output/team_terms_replay_2026-27.json`. Eighty variants: defense prior 5–160 or none × offense prior 5–160 × offense applied or control only, with raw and global/venue controls.
- Selected `def 20 / off 20 applied` against the v1.24 setting (`def 40 / off 20 control`): margin MAE 11.490 vs 11.542 (−0.052, slate-bootstrap 95% interval −0.210 to +0.097), total MAE 14.325 vs 14.410, win-probability log loss 0.5957 vs 0.5989 (−0.0033, −0.0153 to +0.0077). **Neither interval excludes zero.** Applying offense helped slightly at every defense prior tested; a defense prior of 5 was clearly worst.
- The best of 80 variants on one previously inspected season is chosen with optimism. Last season's archive (`docs/defense_selection_2025-26.md`, 1,230 games) pointed the other way: applied offense ("joint", prior 40) had full-season margin MAE 11.092 vs 11.076 and log loss 0.5761 vs 0.5729.
- Pooled public record replayed under v1.25 at adoption (207 games, values recomputed at each build): 147–60 winners (71.0%) against 144–63 for the published v1.24 record, log loss 0.5934 against 0.5998, margin MAE 11.49 against 11.53, total MAE 14.33 against 14.40. Model-pick moneyline series 105–45, +2.12 units (v1.24: 105–45, +2.90); value series 49–37, −9.61 units (v1.24: 36–28, −5.65). Against the market on the 150 priced games log loss is 0.5854 vs the market's 0.5714. Context only: ATS 70–80, totals 45–51 at −110. The v1.24 figures mixed frozen live forecasts with replay, so the comparison is not exactly like for like.
- `research/output/defense_prior_replay_2026-27.json` remains a defense-only prior review of the pinned seed; it was regenerated under this configuration and now names prior 20 as selected, but it does not apply offense.
- No betting-profit search was performed. The owner adopted the change knowingly on point-error evidence that is not distinguishable from zero.
- **Player projection review (research only, no methodology change).** `python research/player_projection_error.py --root . --sleeper-csv <nba-matchups>/data/nba_sleeper.csv` compares 4,717 projected player-games in 208 games (416 team-games) with the pinned ESPN box-score extract `research/inputs/espn_box_2026-27_2026-11-19.csv`; output in `research/output/player_projection_error_2026-27.json`. Player errors persist (walk-forward, a player's next error rises 0.36 per point of his mean past error, 95% interval 0.27–0.45) and Sleeper projects day-to-day players at full value although half of them do not play (projected 11.0, actual 5.9). Neither reaches team scores: the projected points of injury-flagged players do not predict team error (slope +0.05, −0.05 to +0.15), and adding walk-forward player trends worsens team MAE (9.69 → 9.93) and margin MAE (11.59 → 11.83), because teammates absorb the minutes and shots. This supports keeping corrections at team level (offense and defense terms) and adding no player or injury adjustment. A first draft of this analysis, outside the repository, dropped Brooklyn, Charlotte and Phoenix team-games from the nba-matchups snapshots because their Basketball-Reference codes (BRK, CHO, PHO) were not normalized; the committed script maps every team code through `common.team`. The model, the legacy seed and the outage backfill already used `common.team` and were not affected.
- **Out-player minutes adjustment (research only, owner request 22 November 2026; no methodology change).** `python research/out_minutes_adjustment_replay.py --root . --box research/inputs/espn_box_2026-27_2026-11-22.csv` subtracts from each team's projected points the recent minutes of its players listed Out, IR or suspended, divided by k = 10, 8 or 5, and re-scores the v1.33 season replay walk-forward on 73 native games (11 slates; the 125 seed and backfill games have no player rows here and stay unadjusted). Recent minutes are either the player's average over the team's last 5 games (absences count 0) or over his own last 5 games played. Every variant was worse or indistinguishable: margin MAE +0.16 to +1.03 points, totals MAE +0.23 to +4.56, log loss +0.007 to +0.062; the best (last 5 played, k = 10) changed margin MAE by +0.16 (95% slate interval −0.52 to +0.91) and won one more game. The walk-forward residual implies about −0.03 points per Out minute (team-last-5: −0.035, −0.14 to +0.08; own-last-5: −0.027, −0.08 to +0.03), i.e. k ≈ 30 and not distinguishable from 0: Sleeper already projects nearly every Out player at 0 minutes and hands his minutes to teammates, and the team terms absorb long absences. Output in `research/output/out_minutes_adjustment_replay_2026-27.json`. The box file is every completed regular-season game's ESPN summary box score to 21 November (it reproduces the pinned 19 November extract exactly).
- **Low-minute redistribution (research only, owner request 22 November 2026; no methodology change).** `python research/low_minutes_redistribution_replay.py --root . --box research/inputs/espn_box_2026-27_2026-11-22.csv` drops players projected above 0 and below n minutes (n = 3, 5, 8, 10, 12, 15) and hands their minutes and points to teammates projected at n or more, in proportion to minutes (38-minute cap, then the v1.32 240-minute cap), re-scoring the v1.33 season replay walk-forward on 73 native games (seed and backfill unadjusted). No threshold helped: below 3 and 5 changed almost nothing (margin MAE +0.01, log loss +0.000 and +0.005); below 8, 10 and 12 worsened log loss (+0.013, +0.017, +0.032, intervals above zero) and margin MAE by +0.15 to +0.34, with totals within ±0.13. Against ESPN box scores, players projected under 5 minutes miss about 45% of games but the rest play more than projected (average 2.3–4.7 projected against 5.7–6.7 actual, zeros included), while players projected above 20 minutes play 1.6–2.2 minutes less than projected. Sleeper already slightly over-projects the rotation, so moving more minutes to it adds error. Output in `research/output/low_minutes_redistribution_replay_2026-27.json`.
- **Rolling minutes-band correction (research only, owner request 22 November 2026; no methodology change).** `python research/minutes_band_bias_replay.py --root . --box research/inputs/espn_box_2026-27_2026-11-22.csv` corrects each player's projected minutes by the walk-forward mean error (actual − projected) of his projected-minutes band (0–15, 15–24, 24–30, 30+), league-wide (shrunk to 0 with 50 pseudo-observations) or for his team (shrunk to the league value with 20, 5 for `team_loose`, or not at all for `team_raw`), then rescales the team to Sleeper's projected minutes; points follow minutes at each player's projected rate and the 240-minute cap applies. History is the 101 native games with ESPN box scores (2,276 player rows); the v1.33 replay is re-scored on its 73 native games (seed and backfill unadjusted). League-wide bands error +1.4 (0–15), −0.3, −2.2 and −3.0 minutes (30+). The league correction did not help (margin MAE −0.09, −0.32 to +0.16; totals +0.42, +0.15 to +0.69). The team corrections did: `team` margin MAE −0.31 (−0.68 to +0.04), log loss −0.021 (−0.043 to +0.002); `team_loose` margin MAE −0.51 (−0.98 to −0.08), log loss −0.036 (−0.065 to −0.009), two more winners, totals +0.31 (−0.21 to +0.78). The gain is not margin shrinkage (the margin change is uncorrelated with the margin, and a matching shrink does nothing), and none of 50 placebo runs with team histories relabelled across teams did as well. It comes almost entirely from 15–21 November (30 games: margin −1.18, log loss −0.070) and is about zero to 14 November (43 games, little team history). A per-band persistence check does not show team band errors carrying over game to game (correlations −0.09 to +0.14, all intervals include 0), so the mechanism is not established. Bands and shrinkage were set before the first run, but weaker shrinkage was then seen to do better (k = 2 best of 2–50), so `team_loose` is chosen with some optimism. A fourth variant added afterwards at the owner's request, `team_raw` (the team's own band mean with no shrinkage, the league value where the team has no history in a band), did best: margin MAE −0.80 (−1.40 to −0.27), log loss −0.056 (−0.088 to −0.022), three more winners, totals +0.26 (−0.56 to +1.03), raw team-points MAE 9.32 → 9.20; −0.21 margin to 14 November and −1.64 from 15 November; one of 50 relabelled placebo runs matched its log loss and none its margin. It is the strongest of four settings tried on the same games, so it carries the most selection optimism. Promising enough to track prospectively; not adopted. Output in `research/output/minutes_band_bias_replay_2026-27.json`. **Interaction with the team terms** (`research/minutes_band_interaction_replay.py`, owner request): the same 73 games re-scored with the team fit as configured, with offense and defense off (global and venue kept) and with no team fit. The `team_raw` band effect is about the same in each (margin MAE −0.80, −0.74, −0.73; log loss −0.056, −0.062, −0.062), so it neither replaces nor depends on the team terms. On these 73 games the team offense/defense terms themselves did not help the margin (switching them off: margin MAE −0.29, −0.61 to −0.01; log loss +0.002) and helped totals (switching them off: +0.26). That window is short and later than the 207-game evidence the v1.25 terms were adopted on; it is not grounds to remove them. **Full-season confirmation and decision** (`research/minutes_band_full_season_replay.py --sleeper-csv <nba-matchups>/data/nba_sleeper.csv`, owner request: the result decides a version update). The decision rule was fixed before the first run: adopt `team_raw` on the v1.33 model only if log loss over all scored games improves with its 95% slate interval below 0, margin MAE is not worse, and the 125 seed and backfill games (never used to choose the variant) point the same way. Seed and backfill player rows come from nba-matchups' snapshots, each team total checked against the replay score; history is 226 games (4,953 player rows). Over all 198 games (27 slates): margin MAE −0.09 (−0.33 to +0.12), log loss −0.008 (−0.026 to +0.007), winners 147 against 139 (+0.040, +0.010 to +0.073), totals −0.02. Seed and backfill (125): margin +0.03, log loss +0.004, totals −0.24, five more winners. Native (73), now with the earlier season in the history: margin −0.30 (−0.77 to +0.20), log loss −0.030 (−0.060 to −0.002). Placebo relabelling matched the margin gain in 3 of 50 runs and the log-loss gain in 2. The rule fails on two counts (log-loss interval includes 0; the confirmation games do not improve), so **the correction is not adopted and no version is created**. The earlier native-only gain was largely an artefact of the short history window; the extra winners are not matched by better probabilities or margins.

## v1.24 methodology — confidence calibration from the season history (still current)

Owner update, 11 November 2026. The owner's stated intention is a dynamic model: the win-probability scale should move with results instead of restarting at 10 with every methodology tag. This section overrides conflicting text below, including the v1.20–v1.23 rules that confidence calibration "learns only from completed native forecasts" of the current tag, "never uses the seed" and is stage-specific for locks. Everything not named here is unchanged from v1.23, including scoring, team residuals, moneyline-only selections, the fixed control and the calibration equations of Sections 6–8. New forecasts are tagged `sleeper_only_v1_24`; frozen records of every earlier version, and their grades, are never rewritten.

### What changed

1. **The confidence history starts from the season replay** (`calibration.history: season`). First-stage calibration observations are the season's earlier games replayed walk-forward under the current methodology (`season.calibration_observations`: the pinned 117-game seed and resolved first snapshots of earlier tags, each margin scored from team fits that used earlier results only), followed by completed native forecasts of the current tag on fully resolved slates. A native game supersedes its replay. Replay results are usable at their recorded verification time, or at 12:00 UTC on the day after the Eastern tip date for seed games. The replay is computed at refit time and writes no forecast, selection or grade.
2. **Locks use the first-stage calibration** (`calibration.stage_source: {lock: first}`), as v1.23 did for the team fit. No lock-only confidence version is created.
3. **Unchanged safeguards.** The 30-game minimum, 75-game half-life, shrinkage toward 10 with prior strength 100, bounds, at most one accepted refit per UTC day, and the ±10% daily cap all apply. The v1.24 history therefore starts at 10 and moves at most 10% per accepted refit; a refit needs a changed observation manifest.

A configuration frozen without these keys keeps the earlier behavior on replay and validation.

### Public pages (presentation only)

- **Live scores.** While the Today page is open, the reader's browser reads ESPN's public scoreboard for the selected slate and shows each game's live or final score and clock on its card, every 30 seconds while a game is in progress and at the next tip before that; a hidden tab waits. Live games are listed first, then upcoming, then final. This is display only: it is never written to the ledger and never changes a forecast, selection, grade or training observation. Projected scores stay labeled as projected.
- **Layout.** The four pages use the same visual system as the NBA Composite public pages (top navigation, shared color tokens and type, light and dark themes following the reader's system setting unless they choose one). No content, record or number changed.

### One public record and live win chance (owner update, 11 November 2026, presentation only)

This overrides the v1.21 and v1.22 reporting rules that small samples are labeled exploratory, that the pooled record's parts are listed beside it, and that live-only tables and the fixed control appear on the public pages.

- **One record.** Results and Performance show only the pooled v1.24 first-snapshot record (`season.py`): live v1.24 forecasts as frozen, plus the season's earlier games replayed under v1.24. Results lists it game by game for a chosen slate; Performance gives its totals, its comparison with the saved market prices and the two moneyline series. Sample-size labels, replay warnings, the live-only tables, the fixed-control comparison and the stage and model selectors are removed from those pages. The pages no longer say how many games were forecast live and how many replayed (owner decision); How it works describes the replay, and the counts stay in `data.json` (`season.segments`).
- Nothing is deleted: per-part metrics, live-only metrics, the fixed control, closing-line values and lock-stage scores remain in `data.json`, the ledger and `research/output/performance.json`. Scoring, selections, grades and the replay's computation are unchanged.
- **Live win chance.** While a game is in progress its Today card shows an in-game home win probability computed in the reader's browser from ESPN's live score and clock and the DraftKings home spread saved with the game's latest forecast or preview: P = Φ(a(τ)·lead/√τ + b(τ)·μ·√τ), μ = −spread, τ = share of regulation left (in overtime, the period's time left / 48 minutes). The coefficients (`web/live_wp.json`) are copied from `Dave356w/nba-matchups` `model/live_wp.json`, fitted there on ESPN play-by-play of 2015-16 to 2025-26 and chosen by held-out log loss (spline 0.4421 against 0.4492 for a plain Brownian margin on 4,901 held-out games). This is an owner-authorized exception to Section 1's rule against importing trained parameters from that repository, limited to this display: it is never a scoring feature, forecast, selection, grade or training input, and is never written to the ledger. It uses the market spread, not the Sleeper margin, because that is the input it was fitted on; the spread is the one saved with the forecast, which can be hours before tip. It is absent without a saved spread, before tip and after the final.

### Performance page returns (owner update, 17 November 2026, presentation only)

- The value moneyline series is no longer shown on the public pages. It is still computed and kept in `data.json` (`season.pooled.betting.value` and each segment), the ledger and `research/output/performance.json`; its rule and the selections are unchanged.
- The Performance page adds a rolling return chart for the model-pick series: ROI after each bet in tip order, season to date (from bet 20) and over the latest 50 bets. It is computed in the reader's browser from the published season rows with the same one-unit rule as the table, so its final value always equals the table's ROI. Display only.

### Capture-outage backfill (owner update, 17 November 2026)

From about 22:00 UTC on 14 November 2026 the watcher captured nothing: a test fixture aged past the 7-day correction window, the pre-capture test run failed and each restart exited. The owner authorized backfilling the missed games from `Dave356w/nba-matchups`' archived early-day Sleeper snapshots (`data/nba_sleeper.csv`, kind `first`), without the earlier restriction that only the legacy seed and native captures enter the history.

- `research/build_gap_backfill.py` builds `data/calibration_seeds/gap_backfill_2026-11-17.json` in the legacy seed's schema (provenance `gap-backfill-first`), pinned by SHA-256 under `matchup.backfill_files`. It takes only games that have no native first-stage forecast here, are not in the legacy seed, are final on ESPN and whose snapshot precedes tip, with the legacy seed's 200-minute team check. Projected scores are the snapshot's summed player points; results are ESPN finals with the response hash; quotes are nba-matchups' archived first moneyline, spread and total.
- At creation it holds the eight 16 November (ET) games. The outage slates of 14 and 15 November already had native first snapshots, which are graded live once capture resumes. Game 401909904 stays excluded as in the legacy seed.
- Use: the first-stage team fit (from the file's `created_at` on, never earlier), the season replay as archived first snapshots, and through the replay the v1.24 confidence history. It is never a forecast, selection, grade or ledger record, and no live lock exists for these games. A native forecast always supersedes a backfilled game.
- Limits: the snapshot is taken early in the day, not in the T−5h window, and its quotes are paired under the replay's timing rule only. A later outage needs a new pinned file and owner authorization.

### Evidence and its limits

- The margins are compressed: on 145 season games the actual margin is 1.46 times the projected margin (standard error 0.23), and the in-sample best scale is 7.0 against the default 10.
- Before v1.24 no confidence version had ever been accepted: five tags in three weeks each restarted the 30-game count.
- Sequential test, set before it was run: the registered procedure fitted from earlier games only, against fixed a=10, on the 139 replayed games of this season (18 slates). Log loss 0.5618 against 0.5637, difference −0.0019, slate-bootstrap 95% interval −0.0057 to +0.0025. The criterion was an interval excluding zero; **it was not met**. The 2025–26 replay in the v1.22 section points the same way (−0.0043, interval −0.0093 to +0.0004) and also includes zero.
- The owner adopted the change knowingly as a design choice, not on a demonstrated gain. Learned sequentially the scale reached 8.8, not 7.0, because of the shrinkage and the daily cap.
- At adoption the first refit on 145 observations fits 7.01, targets 8.20 and is capped at 9.0.
- Using first-snapshot calibration on locks is not tested; only ten graded locks exist. ATS and totals sigmas are refit from the same history and remain context only.
- The seed sample was inspected before this choice, and its result-availability times are assumed.

## v1.23 methodology — residual defense from game 1, on locks, refit each slate (still current)

Owner update, 10 November 2026. The owner's stated intention is that the residual-defense correction applies from the first game of a season, to every forecast, and is updated after each slate. v1.22 did not do that in three places. This section overrides conflicting text below, including the v1.20 rules that the lock stage "cannot use first-stage seed observations" and "waits for 30 native lock outcomes", the 30-game requirement for team residuals, and one refit per UTC day for team residuals. Everything not named here is unchanged from v1.22, including moneyline-only selections, the priors, the 25-calendar-day decay and the scoring equations. New forecasts are tagged `sleeper_only_v1_23`; frozen records of every earlier version, and their grades, are never rewritten.

### What changed

1. **Locks use the first-stage team fit** (`matchup.stage_source: {lock: first}`). Under v1.22 the lock stage had its own history, started at zero corrections and needed 30 graded native locks; it had 4, so every lock to date carries a zero correction. From v1.23 a lock is scored with the same team-residual version as a first snapshot captured at that moment. That version is trained on first-snapshot residuals, including the pinned 117-game seed. No lock-only team fit is created. Lock forecasts and their outcomes do not train the team fit.
2. **No 30-game wait** (`matchup.minimum_games: 1`). The fit runs from the first graded game of a season. The zero-centered priors (defense 40, offense control 20, global and venue 100) do the shrinking: one game moves a team's defensive term by a fraction of a point. With no graded game the correction is zero.
3. **Refit after each slate** (`matchup.refit: slate`). Under v1.22 the team fit was refreshed at most once per UTC day, at the first pass after 00:00 UTC. That is mid-evening in the Eastern slate, so games finishing later waited a day: the first snapshots captured at 10:35 UTC on 10 November used a fit missing five games of the previous night. From v1.23 a new team version is created when the eligible observation manifest has changed and no started slate still has an ungraded forecast game. A slate has started once one of its forecast games has tipped; a game still ungraded 24 hours after its tip stops holding the refit back, and a retired instance never does. The first version of a methodology is fitted at once. A day without new results creates no version.

Confidence calibration (the win-probability scale and the context sigmas) is unchanged in v1.23 (v1.24 changes its history, above): stage-specific, native forecasts of the current methodology only, 30-game minimum, at most one accepted refit per UTC day. The v1.23 tag starts that history at defaults, as every tag does.

A configuration frozen without these keys keeps the earlier behavior on replay and validation: a 30-game minimum, stage-specific team fits and one refit per UTC day.

### Evidence and its limits

- Seed replay, 117 first snapshots, prior 40 (`research/defense_prior_replay.py`, regenerated): with a one-game minimum 103 games receive a correction (75 with the 30-game minimum). Log loss 0.5735 against 0.5743 with the 30-game minimum and 0.5742 for raw Sleeper; margin MAE 10.843 against 10.883 raw. Against raw the paired log-loss difference is −0.0007 (95% slate-bootstrap interval −0.0067 to +0.0041). Starting earlier is neutral on this sample; it is neither a demonstrated gain nor a harm.
- The pooled season record under v1.23 is 101–38 with log loss 0.5618 (v1.22: 101–38, 0.5625). The value series changes from 43 to 46 bets.
- Using the first-stage fit on locks is not tested: only four graded locks exist. Sleeper's projections at T−5h include later lineup news than at the first snapshot, so first-snapshot residuals may overstate the lock-stage residual. The correction is small (team defensive terms currently between −1.1 and +1.5 points, global −1.4) and the fixed control remains the comparison.
- The seed sample was inspected before these choices. This is a change to match the owner's intention, not a selection on results.

## v1.22 methodology — moneyline-only picks and pooled season record (still current)

Owner update, 10 November 2026. This section overrides conflicting text below, including Sections 7, 8, 12 and 13 where they register spread and totals selections, and the 9 November reporting rule that the replay is never shown with live results. Scoring is unchanged from v1.21. New forecasts are tagged `sleeper_only_v1_22`; frozen records of every earlier version, and their grades, are never rewritten.

### Selections

`selection_markets: [moneyline]`. Only the moneyline creates tracked selections: the predicted-winner series and the value series (at least 0.05 above the no-vig probability and positive expected value at the offered price, either side). Spread and totals selections are retired for new forecasts. A configuration frozen without this key registered all three markets and keeps doing so on replay or validation; an unknown market name is rejected.

Spread and total quotes are still captured and stored with the model's margin and total, flagged `selects: false`. They are context and the basis of the forecast-versus-market comparison, not picks.

Reasons, from the 115 priced first-snapshot games of 22 October–6 November (v1.21 scoring, default scales):

- Spread leans were on the underdog in 97 of 115 games because the model's margins are compressed (83% lie inside the spread). They returned −13.6%; betting every underdog blind returned −13.7%.
- Totals leans went 30–30. Market total error was 13.57 against the model's 13.76.
- Regressing the actual margin on the spread and on the model-minus-spread difference gives a coefficient of −1.26 on the difference (95% slate-bootstrap interval −2.58 to +0.11). It must be positive for a spread bet to have an edge.
- A fixed margin slope fitted on 2025–26 (1.26; by period 1.05, 1.31, 1.45) reduced the underdog share to 66% but did not produce a positive return, and the improvement vanished when the slope was learned sequentially. No slope is registered.

For the moneyline, a slope on the margin is the same adjustment as the win-probability scale `a`, which already refits from completed games (Section 6). On a 2025–26 replay of v1.21 the adaptive scale changed log loss by −0.0043 against fixed a=10 (interval −0.0093 to +0.0004). No additional parameter is added.

None of this shows a moneyline edge. On the same games the market's log loss was 0.5610 against the model's 0.5754, and the value series returned +0.7% on 37 bets (interval −29% to +30%).

### Pooled season record

The public pages show one season-to-date first-snapshot record under the current methodology, built at render time by `season.py` from three parts that stay individually visible:

1. **Replay · archived first snapshots**: the pinned 117-game seed, with moneyline, spread and total quotes from the retained `research/inputs/v1_20_market_quotes_2026-11-09.json` (checksum verified; paired within five minutes of the snapshot and before tip).
2. **Replay · earlier-version captures**: resolved native first snapshots frozen under earlier methodology tags, re-scored from their frozen raw Sleeper scores, with the contemporaneous quotes stored in each forecast.
3. **Live**: graded native first snapshots of the current methodology, exactly as frozen.

Replay chronology: a result is usable at its recorded verification time, or at 12:00 UTC on the day after the Eastern tip date for seed games, whose original verification times were not retained. Each replay forecast uses the latest noon-UTC fit at or before its capture. Team residuals use the registered settings; the win-probability scale starts at 10 and refits from earlier usable replay games once 30 exist, with the registered weighting, shrinkage and daily cap. A game appears once; a live row supersedes a replay row for the same game.

Pooled and per-part outputs: games, winner record, accuracy, log loss, Brier, margin and total MAE, the two moneyline series at recorded prices, and model-versus-market errors on games with a recorded line.

This is a reporting change only. Replay creates no forecast, selection, grade or calibration record; it does not seed live confidence calibration; locks are not replayed; and the native ledger, forecast download and live tables are unchanged. The live-only tables remain on the same pages.

### Limitations

- The pooled line mixes retrospective and prospective evidence. The seed sample was inspected before the v1.21 prior and the v1.22 selection policy were chosen, so its part is in-sample for those choices and will flatter the pooled record relative to future live results.
- Seed availability times are assumed. Seed quotes are legacy first-snapshot records without original response-completion times.
- Predicted-winner returns largely track how favorites performed; in the seed period favorites won 71% against a market-implied 66%.
- Each methodology tag restarts live confidence calibration at defaults. v1.22 therefore begins at a=10 while its replay part uses a replayed adaptive scale.

## v1.21 methodology — defense shrinkage and reporting update (scoring still current)

Owner-requested review, 10 November 2026. This section overrides conflicting v1.20 text below. Everything not named here is unchanged from v1.20. New forecasts are tagged `sleeper_only_v1_21`; frozen v1.19 and v1.20 records, grades and calibration versions are never rewritten.

### What changed

1. **Defensive ridge prior: 5 → 40 fresh team-game observations.** Family, 25-calendar-day half-life, offense control (20), global/venue priors (100), the 30-game minimum and the scoring equations are unchanged.
2. **New version histories.** Team-residual versions and confidence calibration are keyed by methodology, so v1.21 fits its own first-stage team version (117-game pinned seed plus native first snapshots) and starts confidence calibration at a=10 / ATS 14 / totals 19 with zero native games. The lock stage still starts at zero corrections and waits for 30 native lock outcomes.
3. **Reporting.** Public pages, metrics and the forecast download show native v1.21 captures only, following the 9 November policy. Winner accuracy and probability scores are labeled exploratory below 100 graded games; returns below 250 settled bets per series. Performance reports scores by capture lead time and lock coverage by slate. The export adds `tip_utc`, `lead_hours`, team minutes, `model_tag`, per-team corrections and the team-fit training count, rounds floats to six decimals and orders rows by tip.

### Why the prior changed

The v1.20 prior was chosen on February–March 2026 and checked on April. October–January was warm-up and was never scored as a selection period, yet that thin-data regime is where v1.20 went live. The archived comparison (`research/output/defense_selection_2025-26.json`) already contains the relevant numbers for the same family:

| 2025–26 period | Model | N | Margin MAE | Total MAE | WP log loss (a=10) |
|---|---|---:|---:|---:|---:|
| Oct–Jan | raw Sleeper | 729 | 10.855 | 15.134 | 0.6201 |
| Oct–Jan | prior 5 (v1.20) | 729 | 10.980 | 15.288 | 0.6386 |
| Oct–Jan | prior 40 (v1.21) | 729 | 10.863 | 15.131 | 0.6216 |
| Full season | raw Sleeper | 1230 | 11.161 | 14.880 | 0.5752 |
| Full season | prior 5 (v1.20) | 1230 | 11.065 | 14.907 | 0.5834 |
| Full season | prior 20 | 1230 | 11.052 | 14.838 | 0.5742 |
| Full season | prior 40 (v1.21) | 1230 | 11.076 | 14.840 | 0.5729 |

Prior 5 was worse than raw Sleeper on every measure through January and worse on full-season log loss. Prior 40 is level with raw early, keeps most of the late-season point-error gain and has the best full-season log loss of the family. Priors 20 and 40 are not distinguishable; 40 was taken as the more conservative of the two.

On the pinned 117-game 2026–27 seed (`research/defense_prior_replay.py`, output in `research/output/defense_prior_replay_2026-27.json`), 75 games receive a correction. The standard deviation of the margin correction falls from 3.62 points (prior 5) to 0.79 (prior 40), against a raw-margin spread of roughly 4–5 points. Log loss at a=10 is 0.5838 for prior 5, 0.5743 for prior 40 and 0.5742 for raw Sleeper. The prior 40 − prior 5 difference is −0.0095 with a slate-bootstrap interval of [−0.030, +0.016], and prior 40 − raw is +0.0001 [−0.005, +0.004].

### Limitations

- This is a retrospective re-selection on two previously inspected samples, using periods the v1.20 selection treated as warm-up. It is not untouched forward evidence. It moves toward more shrinkage, which limits but does not remove selection risk.
- Neither sample shows v1.21 beating raw Sleeper on win probability. The change removes a likely early-season harm; it does not establish a gain.
- The 2025–26 archive has unverified pregame-as-of timestamps, and its April log loss (≈0.42–0.45 for every candidate including raw) is too low to treat as representative.
- Because corrected margins are now close to raw margins, the default a=10 is retained for the new confidence history. No replay calibration is reused.
- Lock lead time is not controlled. Under the v1.20.1 policy a lock is the first capture between T−5h and tip, and scheduled runs have arrived 3.5–10.5 hours apart, so most games get no lock and the rest mix lead times from five hours to minutes. Lock-stage scores must be read by lead-time bucket. The v1.21.1 capture policy at the end of this document restores the 15-minute window and adds a watcher for new captures.
- Team minutes are exported for audit. Observed first-snapshot team minutes sit at 233–249, so no minutes rescaling is proposed.

## v1.20 methodology — residual defense update (superseded where v1.21 differs)

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


## Prospective capture policy v1.20.1

New captures with `lock_policy: five_hours_to_tip_v1` accept the first valid
lock from five hours before tip until strictly before tip. Once accepted it
remains immutable. Historical forecasts retain their own configuration and
original 15-minute validation window. The scoring methodology remains v1.20.

The default Picks view prefers an accepted lock, otherwise the latest valid
saved pregame preview, otherwise the first snapshot. Preview refreshes retain
previous valid snapshots across failed captures and after tip. Fallbacks display
capture time and actual lead time, remain untracked, and never create bets, grades
or training observations. Post-tip feed data never supplies a pregame fallback.
Preview recommendations use the existing market eligibility rules and only
positive offered-price model EV is displayed; historical records are unchanged.
The scheduled ten-minute workflow is best effort and cannot guarantee execution
or profitable outcomes.


## Prospective capture policy v1.21.1

Owner update, 10 November 2026. New captures use `lock_policy: five_hours_15min_v2`:
the lock is the first valid capture with
`tip − 5h <= capture <= tip − 5h + 15 minutes`, as in Section 11. This supersedes
the v1.20.1 through-tip window for new captures only. Forecasts frozen under
`five_hours_to_tip_v1` keep that configuration and remain valid locks; unlabeled
historical forecasts keep their original 15-minute window. An unrecognized policy
name is rejected. The scoring methodology remains v1.21.

The Picks view, previews and fallbacks are unchanged: a game without a lock
shows the latest saved pregame snapshot, labeled and untracked.

### Watcher

Section 17's "dedicated watcher option" is now implemented. The ten-minute cron
is retired because GitHub delivered it 3.5–10.5 hours apart.

- `watch.yml` runs one job for about 320 minutes. Each pass updates from `main`,
  runs grade → refit → capture → validate → site build, commits accepted records,
  starts the Pages deploy, then sleeps until the earliest of: five seconds past
  the next T−5h target, 60 seconds while a lock window is open without a lock,
  or a schedule-aware tick: ten minutes from eight hours before a tip until that
  game has a verified result (bounded at six hours after tip), and one hour when
  no game is near. `python -m sleeper_nba next-wake` computes the sleep from the
  ledger and creates no records.
- Before exiting, the job dispatches its successor. The shared writer concurrency
  group makes the successor wait until the current run has finished, so there is
  one writer at a time. The hand-off gap is roughly one minute, inside the
  15-minute tolerance.
- No hand-off happens after a manual cancel or when setup or tests fail before
  the watch starts. `watchdog.yml` (cron, twice an hour, best effort) starts a
  watcher when none is queued or running.
- To stop capture, disable both workflows in the Actions tab, then cancel the
  running watcher.

Limitations: a runner loss or GitHub outage that spans a whole 15-minute window
still misses that lock, and the miss is recorded. Capture times are runner clock
times. The watcher keeps the code it started with for workflow steps, but each
pass runs the Python package as updated from `main`; dependency changes take
effect at the next hand-off.


## Prospective capture across methodology transitions — 10 November 2026

First snapshots and locks are write-once per game instance, stage and methodology.
An older methodology's frozen forecast does not block a fresh pregame capture
under the current methodology. When an earlier record occupies the original ID,
the new ID appends the methodology tag. Earlier forecasts remain unchanged.
New captures use their actual retrieval time and current parameters; saved
untracked previews are never promoted or backdated. Locks still require the
registered window. The pooled record counts each game once, with a graded live
current-methodology forecast superseding its retrospective replay.

## Public reporting clarification — 11 November 2026

This is a reporting and asset update, with no methodology change. Live market comparisons use the latest nonvoid grade and the exact frozen forecast and paired capture-time quotes, separately for each first/lock stage and adaptive/fixed variant. Each measure retains its own eligible-game count. Margin and total comparisons use the frozen calibrated predictions. Live controls do not change the separately labeled pooled season overview, which remains the current method's first snapshots with every replay and live part listed.

A read-only market-favorite benchmark compares one-unit model-winner and favorite returns on the same priced games and quotes. The favorite is the side with the higher two-sided no-vig probability, including when both offered prices are negative. Equal-probability pick'em quotes and tied model forecasts are excluded from both benchmark rows. The benchmark reports selection agreement and never creates a forecast, selection, bet, grade or training observation. Positive returns and sample-count thresholds alone do not establish an edge.

The public Results page puts date/stage/model controls before its live scorecards and shows picked teams, probabilities, eligible saved moneylines, market favorites, finals and settled directional moneyline units. Phone layouts use game cards. Calibration details and historical market comparisons remain available in labeled disclosures. Research subsets remain in the ledger.

Brand assets and 30 teams' light/dark logos are served locally. The source manifest records ESPN metadata URLs and source-image hashes; New Orleans uses ESPN's `no` asset. SVG navigation icons replace font-dependent symbols. Asset failure falls back to a local basketball placeholder while the team code remains readable. Favicons, touch icons, a manifest and static social metadata are included; the share image deliberately contains no changing performance claim.

### Coverage and offline verification

Lock coverage counts each scheduled regular-season game instance once its lock window has elapsed after watcher launch. A window without a native lock counts as missed even if the watcher was offline and wrote no quality event. Retired, postponed and cancelled instances are excluded. New schedule records retain the window end. Older records use the recorded miss deadline or the game's frozen forecast policy, falling back to the configured window when neither was retained.

The offline `validate` command reconstructs fit histories at their cutoffs, refits team and confidence parameters, resolves the active versions at capture, and evaluates both variants from the saved projections and ESPN quote inputs. Settlement uses the result or retirement revision available at grading time. New captures journal valid candidates while a stage is eligible, including those deferred for lack of a price. New result revisions reference their ESPN source events. The validator reports how many older forecasts lack candidate history and how many older results lack the original event. Historical replay can verify those frozen inputs and grades, but cannot establish that an earlier qualifying capture never existed.

A final becomes eligible for settlement when both competitors have explicit nonnegative integer scores, the final is untied, and the two team identities match the frozen matchup. The ledger stores the verified scores in frozen home/away order, including when the source reverses its labels. An incomplete or mismatched final stays pending for a subsequent verification. Schedule retrieval failures identify the affected dates and make the watcher run exit unsuccessfully after saving available work.

A rejected projection's quality event now records the per-team projected minutes, projected-player counts and bounds (`invalid_projection` with `team_minutes`, `projected_players`, `bounds`), so a missed stage can be diagnosed; identical repeated rejections still deduplicate. The internal live-performance summary orders games frozen in the same capture by tip time and record id, and paired-comparison slates by date, so its cumulative series and bootstrap intervals no longer depend on ledger file order (reporting only).

Capture events, the lock card and aggregate coverage use the same per-instance operational outcome across methodology versions. Historical quality events remain in the ledger. Public missed-lock rows carry the derived outcome when the watcher was offline.
