# Repository guidance

This is a new independent Sleeper-only model. Follow MODEL.md, including the owner's ESPN fetch-timestamp override. Do not edit any other repository. Keep raw scoring, fixed controls, stages, provenance and all accepted records immutable. Never add an external scoring feature or retroactively change a threshold. All methodology changes are prospective and versioned.

Before changing logic, run the relevant fixture tests. Before publishing, run `ruff check src tests`, `ruff format --check src tests`, `pytest -q`, `python -m sleeper_nba validate`, and `python -m sleeper_nba build-site`. Tests must not depend on live APIs. Browser smoke tests are optional after UI changes and use a locally served generated site. Do not put synthetic fixtures in the native transaction ledger.

The authoritative data is data/transactions. Preserve accepted forecasts on rendering failures; derived exports may be regenerated. Never print tokens or credentials. ESPN odds require no external API secret.
