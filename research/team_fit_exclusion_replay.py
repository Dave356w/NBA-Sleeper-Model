"""Does leaving the 22 day-before-only games out of the team fit cost accuracy? (v1.31 review)

Walk-forward season replay, run twice on the same 185 game-day games:

- ``game_day``: the team fit trains on the replayed game-day games only (v1.30 replay, v1.31 fit).
- ``with_22``: the team fit also trains on the 22 games that have no game-day capture, from
  their day-before first snapshots (the v1.29 replay's observation for those games).

Everything else is identical and follows ``season.replay_rows``: noon-UTC cutoffs, results usable
from their recorded verification times, a failed fit keeps the previous version, and the
win-probability scale is refitted from earlier scored games with the registered weighting,
shrinkage and daily cap. The extra games train the team fit only; they are never scored and never
enter the confidence history. Paired differences are bootstrapped by slate. Research only: nothing
is written to the ledger.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy.special import ndtr

from sleeper_nba import matchup
from sleeper_nba.common import ET, dt, iso, load_config
from sleeper_nba.models import DEFAULTS, fit_parameters
from sleeper_nba.season import _noon_cutoff, _replay, _scores_at
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/team_fit_exclusion_replay_2026-27.json"
BOOTSTRAP = 10_000


def replay(scored, extra, config):
    """`season.replay_rows` with `extra` observations added to the team-fit training only."""
    settings = config["matchup"]
    versions, params_at, rows = {}, {}, []
    params, last = dict(DEFAULTS), matchup.fit([], settings, iso())
    for o in scored:
        cutoff = _noon_cutoff(dt(o["snapshot_utc"]))
        if cutoff not in versions:
            training = []
            for x in scored + extra:
                if dt(x["available_at"]) <= cutoff and dt(x["tip_utc"]) <= cutoff:
                    home, away = _scores_at(x, cutoff)
                    if home != away:
                        training.append({**x, "actual_home_score": home, "actual_away_score": away})
            try:
                last = matchup.fit(training, settings, iso(cutoff))
            except (ValueError, np.linalg.LinAlgError):
                pass
            versions[cutoff] = last
            usable = sorted(
                (r for r in rows if dt(r["available_at"]) <= cutoff),
                key=lambda r: (dt(r["available_at"]), r["game_id"]),
            )
            if len(usable) >= config["minimum_games"]:
                history = []
                for r in usable:
                    home, away = _scores_at(r, cutoff)
                    if home != away:
                        history.append(
                            {
                                "margin": r["margin"],
                                "home_won": home > away,
                                "ats_residual": home - away - r["margin"],
                                "totals_residual": home + away - r["total"],
                            }
                        )
                params, _, _ = fit_parameters(history, params, config)
            params_at[cutoff] = dict(params)
        raw = {"home_score": o["projected_home_score"], "away_score": o["projected_away_score"]}
        scoring, _ = matchup.apply(raw, o, versions[cutoff], settings)
        rows.append(
            {
                "game_id": o["game_id"],
                "slate_date": dt(o["tip_utc"]).astimezone(ET).date().isoformat(),
                "available_at": o["available_at"],
                "revisions": o.get("revisions", []),
                "actual_home_score": o["actual_home_score"],
                "actual_away_score": o["actual_away_score"],
                "margin": scoring["margin"],
                "total": scoring["total"],
                "wp_a": params_at[cutoff]["wp_a"],
            }
        )
    return rows


def per_game(rows):
    out = {}
    for r in rows:
        actual = r["actual_home_score"] - r["actual_away_score"]
        won = actual > 0
        p = min(max(float(ndtr(r["margin"] / r["wp_a"])), 1e-12), 1 - 1e-12)
        p10 = min(max(float(ndtr(r["margin"] / 10.0)), 1e-12), 1 - 1e-12)
        out[r["game_id"]] = {
            "slate": r["slate_date"],
            "abs_error": abs(r["margin"] - actual),
            "correct": float((r["margin"] > 0) == won) if r["margin"] != 0 else 0.5,
            "log_loss": -math.log(p if won else 1 - p),
            "log_loss_a10": -math.log(p10 if won else 1 - p10),
        }
    return out


def compare(a, b, games, seed=20261119):
    """Mean of each metric under both variants and the slate-bootstrapped paired difference (b − a)."""
    slates = sorted({a[g]["slate"] for g in games})
    by_slate = {s: [g for g in games if a[g]["slate"] == s] for s in slates}
    rng = np.random.default_rng(seed)
    draws = [rng.choice(len(slates), len(slates)) for _ in range(BOOTSTRAP)]
    result = {"games": len(games), "slates": len(slates)}
    for metric in ("abs_error", "correct", "log_loss", "log_loss_a10"):
        diff = {s: [b[g][metric] - a[g][metric] for g in by_slate[s]] for s in slates}
        boot = []
        for idx in draws:
            values = [v for i in idx for v in diff[slates[i]]]
            boot.append(float(np.mean(values)))
        result[metric] = {
            "game_day": float(np.mean([a[g][metric] for g in games])),
            "with_22": float(np.mean([b[g][metric] for g in games])),
            "difference": float(np.mean([b[g][metric] - a[g][metric] for g in games])),
            "ci95_slate_bootstrap": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    season = config["matchup"]["seed_season"]

    scored, excluded = _replay(records, root, config, season)
    legacy = {k: v for k, v in config.items() if k != "replay_snapshot"}
    old, _ = _replay(records, root, legacy, season)
    dropped = {e["game_id"] for e in excluded}
    extra = [o for o in old if o["game_id"] in dropped]
    assert len(extra) == len(dropped), "every excluded game has a day-before observation"

    a = per_game(replay(scored, [], config))
    b = per_game(replay(scored, extra, config))
    first_extra = min(dt(o["available_at"]) for o in extra)
    affected = [
        g for g in a if dt(next(o for o in scored if o["game_id"] == g)["snapshot_utc"]) > first_extra
    ]
    changed = [g for g in a if abs(a[g]["abs_error"] - b[g]["abs_error"]) > 1e-9]
    report = {
        "question": "Does leaving the day-before-only games out of the team fit cost accuracy?",
        "methodology": config["methodology"],
        "scored_games": len(a),
        "extra_training_games": sorted(dropped),
        "extra_first_usable": iso(first_extra),
        "games_whose_forecast_changed": len(changed),
        "all_scored": compare(a, b, sorted(a)),
        "after_first_extra_usable": compare(a, b, sorted(affected)),
        "notes": [
            "Differences are with_22 minus game_day: positive abs_error or log_loss means adding the "
            "22 games made forecasts worse; positive correct means more winners.",
            "log_loss uses the walk-forward calibrated scale; log_loss_a10 holds a = 10 fixed, "
            "isolating the team-fit effect from calibration.",
            "Retrospective analysis on previously inspected data; no threshold was searched.",
        ],
    }
    out = root / OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("scored_games", "games_whose_forecast_changed")}, indent=2))
    for part in ("all_scored", "after_first_extra_usable"):
        r = report[part]
        print(f"\n{part}: {r['games']} games, {r['slates']} slates")
        for m in ("abs_error", "correct", "log_loss", "log_loss_a10"):
            v = r[m]
            lo, hi = v["ci95_slate_bootstrap"]
            print(
                f"  {m:13s} game_day {v['game_day']:.4f}  with_22 {v['with_22']:.4f}  "
                f"diff {v['difference']:+.4f}  95% [{lo:+.4f}, {hi:+.4f}]"
            )


if __name__ == "__main__":
    main()
