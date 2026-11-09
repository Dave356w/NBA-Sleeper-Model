# v1.20 versus market and the value-underdog favorite hypothesis

Analysis date: **9 November 2026**. Historical sample: **22 October–6 November 2026**. Stage: **legacy first snapshots**.

**Status: retrospective exploratory research.** The favorite-reversal hypothesis was identified after inspecting this sample. It has not been registered as a prospective selection series or enabled in production.

## Findings

The v1.20 replay picks slightly more winners than the market favorite on the common directional sample. Market moneylines have better probability scores, and market spreads and totals have lower point-error estimates. The existing moneyline value filter loses money, with market underdogs accounting for the losses.

Reversing value-underdog selections is promising in one small subset: when v1.20 still gives the underdog less than 50% win probability. The opposing favorites went **15–2 moneyline (+22.7% ROI)** and **10–7 ATS (+12.7%)**. This is a hypothesis for future testing, with only **17 games across eight dates**.

## Sources and frozen inputs

| Input | Location and provenance |
| --- | --- |
| Model definition | [`eab43b2`](https://github.com/Dave356w/NBA-Sleeper-Model/tree/eab43b23a40bec0eee6d05887fbbcb4b603c3d53), v1.20 residual-defense scoring and confidence calibration |
| Historical Sleeper scores and verified finals | [`data/calibration_seeds/legacy_first_2026-27.json`](../data/calibration_seeds/legacy_first_2026-27.json), 117 audited legacy-first observations; pinned payload SHA-256 `9d3a8e56998a4a7e738020614b3546e1afbc658d97370abe7cffe53714348705` |
| Original market ledger | [`Dave356w/nba-matchups/data/nba_ledger.csv`](https://github.com/Dave356w/nba-matchups/blob/8ac68a718013ce78855a66a5a1a2b7543d3450be/data/nba_ledger.csv), commit `8ac68a7`, Git blob `9b9148f3ef226674e3acf6ad4c3feffcd4fa6d2e` |
| Retained market input | [`research/inputs/v1_20_market_quotes_2026-11-09.json`](../research/inputs/v1_20_market_quotes_2026-11-09.json): selected quote, identity and final-score fields for the same 117 games, with original source hashes and a payload checksum |
| Earlier point-model selection | [`docs/defense_selection_2025-26.md`](defense_selection_2025-26.md) and [`research/output/defense_selection_2025-26.json`](../research/output/defense_selection_2025-26.json) |
| Reproducible calculations | [`research/v1_20_market_analysis.py`](../research/v1_20_market_analysis.py) |
| Complete results and game audit | [`research/output/v1_20_market_analysis_2026-11-09.json`](../research/output/v1_20_market_analysis_2026-11-09.json), including all 26 favorite-reversal games with IDs, dates, probabilities, prices and individual returns |

The retained external-ledger fields contain market quotes and result cross-checks. No predictions, fitted parameters or scoring features from the other model are used.

The seed excludes game `401909904` because Indiana had only 198 projected minutes. The original legacy data does not retain complete raw Sleeper stat objects. Both seasons and current-season outcomes had been inspected before this study.

## Replay and market matching

1. Reconstruct raw scores from the frozen seed totals. Replay v1.20's global, venue and opponent-defense corrections with 25-calendar-day decay, defensive prior 5, offensive estimation-control prior 20 and global/venue priors 100. Apply no offense coefficient to scores.
2. For this historical experiment, assume each outcome becomes available at **12:00 UTC on the day after its Eastern tip date**. Use the latest noon UTC at or before each archived projection as the fitting cutoff. This substitutes an explicitly assumed research clock for the seed's actual November 7 result-verification times.
3. Order forecasts by capture time. Fit confidence only from earlier simulated, frozen corrected forecasts whose assumed outcomes are available at the cutoff. Use the existing 75-completed-game weighting, 30-game minimum, scale regularization and daily movement caps. Extra scalar ATS/totals biases remain zero.
4. Join markets by game ID and verify canonical home/away identities and final scores against the seed. Use `first_*` moneyline/spread quotes and `first_ou_*` total quotes. Require recorded market and Sleeper timestamps within five minutes, both before tip, with DraftKings prices on both sides. Apply v1.20's half-point and price-range rules to ATS/totals.
5. Use the registered directional/value definitions and actual offered-price payouts. Risk **one unit per selection**. Include overtime; retain pushes separately. Series and subsets overlap and must never be added into a combined bankroll.

**Quote timestamp limitation:** the legacy timestamps are recorded first-snapshot times. Original HTTP response-completion timestamps and sportsbook update times are unavailable. “Matched” below means eligible under these replay assumptions; it does not certify native v1.20 capture provenance or upstream freshness. Later odds are not attached to an earlier forecast.

| Market | Matched games | Missing quotes | Outside five-minute pairing | Eastern slate dates |
| --- | ---: | ---: | ---: | ---: |
| Moneyline | 115 | 1 | 1 | 14 |
| ATS | 115 | 1 | 1 | 14 |
| Totals | 60 | 54 | 3 | 7 |

For moneyline, `401909093` has no first quote and `401909094` has a later, unpaired first quote. Both were correct v1.20 winner picks. That explains **86–31 across all 117 replay games** versus **84–31 across the 115 matched moneylines**.

The complete replay reproduces margin MAE **10.826236**, total MAE **14.716602**, log loss **0.584844** and Brier **0.199833**. The script checks these against the earlier archived candidate within `1e-8`; tiny optimizer differences can occur across numerical-library versions.

## v1.20 versus the recorded market

Market probabilities are proportional no-vig probabilities from the two recorded American prices. Market point forecasts are the negative signed home spread and the quoted game total.

| Measure | Common games | v1.20 | Market |
| --- | ---: | ---: | ---: |
| Winner accuracy | 112 | **82–30, 73.2%** | 80–32, 71.4% |
| Moneyline log loss | 115 | 0.586128 | **0.561028** |
| Moneyline Brier | 115 | 0.200465 | **0.190497** |
| Margin MAE | 115 | 10.890084 | **10.386957** |
| Total MAE | 60 | 13.907363 | **13.566667** |
| ATS probability log loss | 115 | 0.711804 | **0.695763** |
| Totals probability log loss | 60 | 0.712501 | **0.694817** |

Winner accuracy excludes three 50/50 market pick'ems from both sides. The market winner is its favorite. All other measures retain their stated common samples; lower scores/errors are better.

Backing v1.20 winners on those same 112 non-pick'em games returns **+9.4946 units, +8.48% ROI**, versus **+2.6748 units, +2.39%** backing market favorites. The paired ROI difference is **+6.09 percentage points**, with an exploratory 95% slate-bootstrap interval **[−5.59, +18.13] pp**. The advantage is not established.

The paired v1.20-minus-market log-loss difference is **+0.02510**, interval **[−0.01111, +0.05878]**. Margin MAE difference is **+0.50313 points**, interval **[−0.02170, +1.08288]**. These intervals also cross zero. The point estimates favor the market, with limited statistical resolution.

## Returns from the existing registered selection definitions

| Replay series | Bets | W–L | Net units | ROI |
| --- | ---: | ---: | ---: | ---: |
| Moneyline directional: every predicted winner | 115 | 84–31 | **+10.3128** | **+8.97%** |
| Moneyline value: ≥5 pp no-vig advantage and positive offered-price EV | 64 | 35–29 | −4.6514 | −7.27% |
| All ATS directional leans | 115 | 57–58 | −6.2380 | −5.42% |
| ATS absolute point edge ≥3 | 46 | 22–24 | −4.1162 | −8.95% |
| All totals directional leans | 60 | 29–31 | −4.7201 | −7.87% |
| Totals value: ≥5 pp no-vig advantage | 19 | 8–11 | −3.7446 | −19.71% |

There are no pushes in these supported matched markets. A no-vig advantage alone does not guarantee positive offered-price EV; totals retain the existing policy without an additional EV gate.

Moneyline value selections split as follows. Groups use market no-vig probability above/below 50%; equal 50% is a pick'em.

| Selected side's market role | Bets | W–L | Net units | ROI |
| --- | ---: | ---: | ---: | ---: |
| Favorite | 36 | 26–10 | +1.2381 | +3.44% |
| Underdog | 26 | **7–19** | **−7.7076** | **−29.64%** |
| Pick'em | 2 | 2–0 | +1.8182 | +90.91% |

Within the 26 value underdogs:

- Model probability **below 50%**: **2–15**, **−10.5900 units**, **−62.29% ROI**. Mean predicted win probability was **38.48%**, versus **11.76%** realized.
- Model probability **at least 50%**: **5–4**, **+2.8824 units**, **+32.03% ROI**.

The large ATS-edge subset also overstates confidence in this sample: mean predicted cover probability **63.46%**, observed cover rate **47.83%**. These are descriptive calibration failures in the inspected sample, not estimates of permanent model bias.

## Favorite reversal hypothesis

Reverse each qualifying moneyline value-underdog selection to the opposing market favorite. Use that favorite's own recorded moneyline price. For ATS, use its own same-snapshot eligible spread price and line; the trigger remains the original moneyline value-underdog condition.

| Original value-underdog condition | Games | Favorite ML W–L | ML units | ML ROI | Favorite ATS W–L | ATS units | ATS ROI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All value underdogs | 26 | 19–7 | +1.8544 | +7.13% | 14–12 | +0.8576 | +3.30% |
| **Model dog probability <50%** | **17** | **15–2** | **+3.8659** | **+22.74%** | **10–7** | **+2.1509** | **+12.65%** |
| Model dog probability ≥50% | 9 | 4–5 | −2.0115 | −22.35% | 4–5 | −1.2933 | −14.37% |

For the 17-game subset, both model direction and market direction favor the favorite. The disagreement concerns the underdog's price, not the predicted winner. The model's mean favorite probability was **61.52%**, versus the market's **69.77%** and an observed **88.24%** favorite win rate. Market probabilities implied **11.86 expected favorite wins**, versus 15 observed. The median favorite quote was **−265**. Profits above already account for the smaller favorite payouts.

The mean raw price-implied favorite probability was **72.71%**. That mean is a descriptive quote statistic; each game's actual price determines its payout and breakeven probability.

The subset's favorite moneyline ROI interval is **[+3.69%, +39.55%]**; its ATS ROI interval is **[−24.64%, +63.74%]**. All-value-underdog favorite ML ROI has interval **[−25.05%, +31.66%]**. These are exploratory, unadjusted intervals from very few date clusters. The positive interval in the selected 17-game slice is not confirmatory evidence after choosing the slice from these outcomes.

### Proposed future test, not yet registered

Keep the existing v1.20 forecasts and value criteria fixed. A possible first-stage test would require:

1. A complete, frozen native v1.20 first forecast with qualifying contemporaneous two-sided DraftKings moneylines.
2. A market underdog `q_dog < 0.50` with `p_dog - q_dog >= 0.05` and positive offered-price EV under the existing value definition.
3. The additional condition `p_dog < 0.50`, so the model still predicts the market favorite to win.
4. Separate one-unit favorite moneyline and favorite ATS series. ATS additionally needs an eligible contemporaneous half-point line and two-sided prices in the registered range. Missing ATS must not remove an otherwise eligible ML observation or be filled from later prices.

A prospective registration would need an exact start timestamp, series IDs, code/configuration hashes, evaluation horizon and comparison plan before any qualifying forecast is captured. Already frozen forecasts and this historical sample stay outside the new forward denominator. Lock-stage tests require separate registration and records. Preserve pick'ems and the `p_dog >= 0.50` group as distinct research comparisons.

This would be a contrarian selection rule. Under v1.20's own probability estimates, the opposite favorite need not have positive EV. The new series therefore needs explicit prospective documentation rather than being presented as an existing v1.20 value selection.

## Reproduce and extend

From the repository revision containing this study, install the pinned requirements, then run:

```bash
python research/v1_20_market_analysis.py --root .
```

The command runs offline, rebuilds the 117 chronological score/probability forecasts, evaluates the retained market inputs, checks the archived reference metrics and writes only `research/output/v1_20_market_analysis_2026-11-09.json`. It does not require the temporary analysis files used during the original investigation.

The input manifest pins the market source commit/blob/full-file hash, selected-input checksum, seed checksum, configuration hash and implementation-file hashes. The output adds script and numerical-library versions. Drift fails explicitly; use the study revision to reproduce it after future model changes. The source snapshot includes all 117 records so missing and unpaired quotes remain auditable.

Bootstrap sampling uses entire Eastern slate-date clusters, RNG seed 120, 10,000 draws for market comparisons/returns and 20,000 for favorite-reversal intervals. Game-level favorite results are stored in `favorite_reversal.games`; the 17-game primary candidate is identified by `model_dog_below_50`.

Future analysis should prioritize a new forward sample, confidence reliability for value underdogs, and comparisons against favorites at similar prices. Keep dates, model versions, snapshot stages and opportunity sets aligned. Record any additional subgroup search as exploratory.

## Interpretation limits and production separation

- This study has **14 matched moneyline/ATS dates**, **seven totals dates** and **eight dates in the 17-game favorite subset**. Results have limited statistical resolution and correlated games within slates.
- Historical result availability is assumed. Legacy pregame-as-of integrity, raw HTTP timing and sportsbook update freshness cannot be fully established from retained objects.
- The model family and these seasons had already been explored. The reversal hypothesis and probability split were identified after observing losses. Multiple model/strategy comparisons increase selection bias.
- Returns assume flat one-unit risk at recorded quotes, with no evidence about execution, limits or slippage. High moneyline win rates alone do not establish profitability at favorite prices.
- At model-definition commit `eab43b2`, the separate native v1.20 ledger had **one settled lock forecast and no settled first forecasts**. None of the 117 replay games is native forward evidence.
- This note and its command do not modify production scoring, thresholds, calibration, native forecasts, grades or public headline results. The favorite series remains a documented proposal for future prospective registration.
