# Repository guidance

This is a new independent Sleeper-only model. Follow MODEL.md, including the owner’s ESPN fetch-timestamp override and prospective v1.20 residual-defense update. Do not edit any other repository. Keep raw scoring, fixed controls, stages, provenance and all accepted records immutable. Never add an external scoring feature or retroactively change a threshold. All methodology changes are prospective and versioned.

Before changing logic, run the relevant fixture tests. Before publishing, run `ruff check src tests`, `ruff format --check src tests`, `pytest -q`, `python -m sleeper_nba validate`, and `python -m sleeper_nba build-site`. Tests must not depend on live APIs. Browser smoke tests are optional after UI changes and use a locally served generated site. Do not put synthetic fixtures in the native transaction ledger.

The authoritative data is data/transactions. Preserve accepted forecasts on rendering failures; derived exports may be regenerated. Never print tokens or credentials. ESPN odds require no external API secret.

The owner authorized incorporating residual defense with 25-calendar-day decay and testing shrinkage on last season. The selected scoring component is defense-only with a 20-game offensive nuisance control and 5-game defensive prior. First-stage parameter history includes the explicitly labeled, hash-pinned 117-game legacy-first seed; locks never borrow it. Preserve raw/fixed forecasts and v1.19 histories. Probability calibration starts a separate v1.20 history and uses frozen corrected targets, with no extra scalar scoring biases. Latest previews are untracked and must never create bets, grades or training observations.
