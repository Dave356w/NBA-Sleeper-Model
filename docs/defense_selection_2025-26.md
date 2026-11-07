# Residual defense iteration selection — 2025–26

The owner fixed team-residual half-life at **25 calendar days** and requested a last-season shrinkage comparison. This is retrospective point-forecast model selection; no betting-profit search was performed.

- Warm-up: October 2025–January 2026 (729 games).
- Selection: February–March 2026 (405 games).
- Final chronological check: April 1–12, 2026 (96 games).
- Twenty-one candidate combinations: three effect families × ridge strengths 0, 5, 10, 20, 40, 80, 160. Three additional controls: raw Sleeper, global/venue-only and the existing adaptive replay.
- Primary selection loss: `0.5*(margin_MAE/14 + total_MAE/19)`. All coefficients are refitted from prior games only, with an assumed next-day-noon UTC outcome availability schedule.
- The archive is mutable and lacks verified pregame-as-of timestamps. Both seasons had already been explored, so April is a separated check for this comparison, not pristine untouched evidence.

## Selected point-forecast candidate: `defense_offense_control_prior5`

Fit centered offense and opponent-defense residuals jointly. Apply only global, venue and opponent-defense terms to scores. The offensive coefficient is a nuisance control. Defensive prior strength is 5 fresh team-game observations; offense control prior is 20; global and venue priors are 100 each. Zero prior was tested as an unshrunk minimum-norm diagnostic, not selected.

| Period | Model | N | Margin MAE | Total MAE | WP log loss |
|---|---|---:|---:|---:|---:|
| selection | defense_offense_control_prior5 | 405 | 11.131 | 14.134 | 0.5192 |
| selection | raw_sleeper | 405 | 11.429 | 14.265 | 0.5248 |
| selection | global_venue | 405 | 11.433 | 14.308 | 0.5249 |
| selection | registered_adaptive | 405 | 11.426 | 14.309 | 0.5175 |
| final_check | defense_offense_control_prior5 | 96 | 11.430 | 15.282 | 0.4351 |
| final_check | raw_sleeper | 96 | 12.354 | 15.553 | 0.4467 |
| final_check | global_venue | 96 | 12.312 | 15.532 | 0.4435 |
| final_check | registered_adaptive | 96 | 12.327 | 15.514 | 0.4185 |
| full_season | defense_offense_control_prior5 | 1230 | 11.065 | 14.907 | 0.5834 |
| full_season | raw_sleeper | 1230 | 11.161 | 14.880 | 0.5752 |
| full_season | global_venue | 1230 | 11.164 | 14.884 | 0.5750 |
| full_season | registered_adaptive | 1230 | 11.171 | 14.861 | 0.5693 |

The April margin-MAE difference versus raw is −0.924 points, with an exploratory 95% slate-bootstrap interval of [−1.482, −0.448]. The totals difference is −0.270, interval [−0.920, +0.572]; totals improvement is not established in that small check.

**Important tradeoff:** existing adaptive WP log loss was better than this candidate at fixed a=10 (April 0.4185 versus 0.4351; full season 0.5693 versus 0.5834). This is the best candidate by the declared point-error selection objective, not a claim of best overall probability calibration or profitability. The production integration gives corrected forecasts a new confidence-calibration history rather than reusing calibration learned from raw margins.

## Selection ranking

| Candidate | Selection loss | Selection margin MAE | Selection total MAE | April check loss |
|---|---:|---:|---:|---:|
| defense_offense_control_prior5 | 0.769472 | 11.131 | 14.134 | 0.810374 |
| defense_only_prior5 | 0.769761 | 11.127 | 14.150 | 0.810511 |
| defense_offense_control_prior10 | 0.770483 | 11.157 | 14.136 | 0.813882 |
| defense_only_prior10 | 0.770791 | 11.155 | 14.151 | 0.813962 |
| defense_offense_control_prior0 | 0.770933 | 11.147 | 14.167 | 0.811867 |
| defense_only_prior0 | 0.771308 | 11.142 | 14.189 | 0.811694 |
| defense_offense_control_prior20 | 0.773009 | 11.213 | 14.157 | 0.819792 |
| joint_prior10 | 0.773074 | 11.267 | 14.087 | 0.818036 |
| defense_only_prior20 | 0.773240 | 11.211 | 14.168 | 0.819906 |
| joint_prior5 | 0.773253 | 11.276 | 14.081 | 0.817725 |
| joint_prior20 | 0.773909 | 11.269 | 14.114 | 0.821131 |
| joint_prior40 | 0.775967 | 11.292 | 14.163 | 0.827343 |
| defense_offense_control_prior40 | 0.776261 | 11.273 | 14.199 | 0.827353 |
| defense_only_prior40 | 0.776403 | 11.272 | 14.206 | 0.827407 |
| joint_prior0 | 0.777818 | 11.344 | 14.161 | 0.819609 |
| joint_prior80 | 0.778787 | 11.331 | 14.216 | 0.835057 |
| defense_offense_control_prior80 | 0.779408 | 11.331 | 14.239 | 0.835231 |
| defense_only_prior80 | 0.779499 | 11.331 | 14.243 | 0.835326 |
| joint_prior160 | 0.781152 | 11.371 | 14.252 | 0.840760 |
| defense_offense_control_prior160 | 0.781644 | 11.373 | 14.267 | 0.840753 |
| defense_only_prior160 | 0.781713 | 11.373 | 14.270 | 0.840847 |

The current season was evaluated only after this selection and was not used to choose family or shrinkage. It is still a previously inspected sample, not untouched forward evidence. All new live records are prospective and methodology-tagged.

## Selected model with prior-only confidence calibration

The selected point model was also passed through the confidence calibration used in the integration: a=10/ATS14/totals19 defaults, at least 30 prior corrected out-of-sample targets, the existing 75-completed-game confidence weighting and shrinkage/daily caps, and no extra scalar margin/total biases. This did not rerun family/prior selection.

- 2025-26_archive, full_season: n=1230, calibrated log loss 0.5752, Brier 0.1961.
- 2025-26_archive, final_check: n=96, calibrated log loss 0.4347, Brier 0.1420.
- 2026-27_legacy_first, current: n=117, calibrated log loss 0.5848, Brier 0.1998.

The selected correction still did not beat the existing adaptive probability baseline in these retrospective checks. Its point-error result is not a claim of superior WP or betting performance. New native v1.20 probability calibration is kept separate and learns prospectively from its own corrected forecasts.
