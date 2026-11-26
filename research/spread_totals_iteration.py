"""Team-fit settings for spreads and totals, walk-forward (owner request, 26 November 2026).

The owner asked to keep iterating on spreads and totals after the slope test failed. Market lines
may not enter the score (no external scoring feature), so the candidates only change how the
model's own team fit learns from Sleeper projections and results. Research only: nothing here
writes a ledger record or changes a forecast, selection, grade or the public record.

An exploratory look at the pooled record came first (26 November): the market beat the model on
margin MAE (10.64 against 10.97) and total MAE (14.77 against 15.14). Actual totals ran about 1.0
point below the model on average, most in opening week (−3.1). Hit rates against the line did not
rise with the model's disagreement from it. The candidates below were chosen after that look and
before this script's first run.

Pre-registered (26 November 2026, before the first run):

- Games: every game of the v1.30 game-day season replay with the methodology label set to a
  research value, so the current version's native games replay too (230 games). Baseline and
  variants are replayed walk-forward the same way, with the team fit and confidence scale refitted,
  so all comparisons are paired. Lines: frozen snapshot, else the pinned DraftKings close.
- Variants, one setting changed each (baseline in brackets):
  `half_12`: `half_life_days` 12.5 (25);  `half_50`: 50 (25);
  `prior_10`: `offense_prior` and `defense_prior` 10 (20);  `prior_40`: both 40 (20);
  `global_25`: `global_prior` 25 (100);
  `total_level`: totals only, total + the walk-forward mean total residual of finished earlier
  games, shrunk with 40 pseudo-games at 0 (margins and win chances unchanged).
- Measures: margin MAE, total MAE, ATS and over/under hit rate against the line (pushes and missing
  lines no bet), moneyline log loss. Paired differences, variant minus baseline, slate bootstrap.
- Decision, per variant and market (spread: margin MAE and ATS; total: total MAE and over/under):
  a pass needs the MAE difference's interval, Bonferroni-widened for 6 variants × 2 markets
  (two-sided 1 − 0.05/12), entirely below 0, the hit rate not lower and, for variants that move
  margins, log loss not higher. A pass is not adopted here: it gets a forward test on games after
  this run, specified then. Spread and total quotes stay context only (v1.22).

    python research/spread_totals_iteration.py --root .
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

import numpy as np
from slope_walk_forward import graded

from sleeper_nba.common import dt, load_config
from sleeper_nba.season import _replay, closing_prices, replay_rows, with_closing_prices
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/spread_totals_iteration_2026-27.json"
BOOT = 4000
TESTS = 12
LEVEL_K = 40
VARIANTS = {
    "half_12": {"half_life_days": 12.5},
    "half_50": {"half_life_days": 50.0},
    "prior_10": {"offense_prior": 10.0, "defense_prior": 10.0},
    "prior_40": {"offense_prior": 40.0, "defense_prior": 40.0},
    "global_25": {"global_prior": 25.0},
    "total_level": None,
}


def replay(records, root, config, changes):
    config = copy.deepcopy(config)
    config["methodology"] = "research_spread_totals"
    config["matchup"].update(changes or {})
    observations, _ = _replay(records, root, config, config["matchup"]["seed_season"])
    rows = with_closing_prices(replay_rows(observations, config), closing_prices(root, config))
    return {r["game_id"]: r for r in rows}


def total_level(rows):
    """Totals shifted by the shrunk mean residual of games finished before each tip."""
    out = {}
    for g, r in rows.items():
        tip = dt(r["tip_utc"])
        done = [h["actual_total"] - h["total"] for h in rows.values() if dt(h["available_at"]) <= tip]
        out[g] = {**r, "total": r["total"] + sum(done) / (len(done) + LEVEL_K)}
    return out


def measures(r):
    m = graded(r, r["margin"], r["total"])
    p = min(max(r["wp_home"], 1e-12), 1 - 1e-12)
    m["log_loss"] = -math.log(p if r["actual_margin"] > 0 else 1 - p)
    return m


def compare(base, variant, rng):
    games = sorted(base, key=lambda g: (dt(base[g]["tip_utc"]), g))
    a = {g: measures(base[g]) for g in games}
    b = {g: measures(variant[g]) for g in games}
    slates = sorted({base[g]["slate_date"] for g in games})
    by = {s: [g for g in games if base[g]["slate_date"] == s] for s in slates}
    draws = [rng.choice(len(slates), len(slates)) for _ in range(BOOT)]
    lo, hi = 100 * 0.025 / TESTS, 100 * (1 - 0.025 / TESTS)

    def diff(sample, key):
        if key in ("ats", "ou"):
            hits = [[x[g][key] for g in sample if x[g][key] is not None] for x in (a, b)]
            return np.mean(hits[1]) - np.mean(hits[0])
        return float(np.mean([b[g][key] - a[g][key] for g in sample]))

    out = {}
    for key in ("margin_abs_error", "total_abs_error", "ats", "ou", "log_loss"):
        boot = [diff([g for i in d for g in by[slates[i]]], key) for d in draws]
        base_value = (
            np.mean([a[g][key] for g in games if a[g][key] is not None])
            if key in ("ats", "ou")
            else np.mean([a[g][key] for g in games])
        )
        out[key] = {
            "baseline": float(base_value),
            "difference": float(diff(games, key)),
            "ci95": np.percentile(boot, [2.5, 97.5]).tolist(),
            "ci_bonferroni": np.percentile(boot, [lo, hi]).tolist(),
        }
    out["changed_ats_picks"] = sum(
        a[g]["ats"] is not None and b[g]["ats"] is not None and a[g]["ats"] != b[g]["ats"] for g in games
    )
    out["changed_ou_picks"] = sum(
        a[g]["ou"] is not None and b[g]["ou"] is not None and a[g]["ou"] != b[g]["ou"] for g in games
    )
    return out


def decide(result, moves_margins):
    ll_ok = (not moves_margins) or result["log_loss"]["difference"] <= 0
    return {
        "spread": bool(
            result["margin_abs_error"]["ci_bonferroni"][1] < 0 and result["ats"]["difference"] >= 0 and ll_ok
        ),
        "total": bool(
            result["total_abs_error"]["ci_bonferroni"][1] < 0 and result["ou"]["difference"] >= 0 and ll_ok
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    rng = np.random.default_rng(20261126)
    base = replay(records, root, config, None)
    results = {}
    for name, changes in VARIANTS.items():
        variant = total_level(base) if changes is None else replay(records, root, config, changes)
        assert set(variant) == set(base)
        r = compare(base, variant, rng)
        r["pass"] = decide(r, moves_margins=changes is not None)
        results[name] = r
    output = {
        "generated_for": "spread and totals team-fit iteration (owner request, 26 November 2026); research only",
        "methodology": config.get("methodology"),
        "ledger_records": len(records),
        "games": len(base),
        "slates": len({r["slate_date"] for r in base.values()}),
        "segments": {
            s: sum(r["segment"] == s for r in base.values()) for s in ("replay_legacy", "replay_native")
        },
        "bonferroni_tests": TESTS,
        "variants": results,
    }
    (root / OUTPUT).write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output, indent=1))


if __name__ == "__main__":
    main()
