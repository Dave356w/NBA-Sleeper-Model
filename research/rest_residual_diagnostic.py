"""Do the model's spread and total errors depend on rest (back-to-backs)? (diagnostic)

Owner request, 26 November 2026, while iterating on spreads and totals. Rest is not a model input,
and using it would be a new scoring feature that needs the owner's decision. This script only asks
whether the residuals of the current method show a rest effect. Research only: nothing here writes
a ledger record or changes a forecast, selection, grade or the public record.

Residuals: the walk-forward v1.33 replay of `spread_totals_iteration.replay` (230 games). Each team's
adjusted points are (total ± margin) / 2. Rest: Eastern calendar days since the team's previous
game in the ESPN box-score extract, which lists every completed game; a back-to-back is 1 day. A
team's first game of the season has no rest value and is left out of the rest tests.

Fixed before the first run: if a back-to-back effect on margin or total is distinguishable from 0
at 95% (slate bootstrap), it is reported to the owner as a candidate input with a forward test to
be specified; nothing is adopted here.

    python research/rest_residual_diagnostic.py --root . \\
        --box research/inputs/espn_box_2026-27_2026-11-26.csv
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from spread_totals_iteration import replay

from sleeper_nba.common import ET, dt, load_config, team
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/rest_residual_diagnostic_2026-27.json"
BOOT = 4000


def rest_days(box_path):
    days = collections.defaultdict(set)
    with open(box_path, newline="") as handle:
        for r in csv.DictReader(handle):
            days[team(r["team"])].add(dt(r["tip_utc"]).astimezone(ET).date())
    rest = {}
    for code, dates in days.items():
        ordered = sorted(dates)
        for prev, cur in zip(ordered, ordered[1:]):
            rest[(code, cur.isoformat())] = (cur - prev).days
    return rest


def ols(x, y):
    x = np.column_stack([np.ones(len(x)), x])
    return np.linalg.lstsq(x, y, rcond=None)[0][1:]


def boot(items, fn, rng):
    slates = sorted({i["slate"] for i in items})
    by = {s: [i for i in items if i["slate"] == s] for s in slates}
    point = fn(items)
    draws = np.array(
        [fn([i for j in rng.choice(len(slates), len(slates)) for i in by[slates[j]]]) for _ in range(BOOT)]
    )
    lo, hi = np.percentile(draws, [2.5, 97.5], axis=0)
    return {
        "estimate": np.atleast_1d(point).tolist(),
        "ci95": [np.atleast_1d(lo).tolist(), np.atleast_1d(hi).tolist()],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--box", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    rows = replay(records, root, config, None)
    rest = rest_days(args.box)
    rng = np.random.default_rng(20261126)
    games, teams = [], []
    for r in rows.values():
        slate = r["slate_date"]
        hr, ar = rest.get((r["home"], slate)), rest.get((r["away"], slate))
        if hr is None or ar is None:
            continue
        home_adj, away_adj = (r["total"] + r["margin"]) / 2, (r["total"] - r["margin"]) / 2
        g = {
            "slate": slate,
            "home_b2b": int(hr == 1),
            "away_b2b": int(ar == 1),
            "margin_resid": r["actual_margin"] - r["margin"],
            "total_resid": r["actual_total"] - r["total"],
        }
        games.append(g)
        for own, opp, adj, actual in (
            (hr, ar, home_adj, r["actual_home_score"]),
            (ar, hr, away_adj, r["actual_away_score"]),
        ):
            teams.append(
                {"slate": slate, "own_b2b": int(own == 1), "opp_b2b": int(opp == 1), "resid": actual - adj}
            )

    def margin_fit(s):
        return ols(
            np.array([[i["home_b2b"], i["away_b2b"]] for i in s]), np.array([i["margin_resid"] for i in s])
        )

    def total_fit(s):
        return ols(
            np.array([[i["home_b2b"] + i["away_b2b"]] for i in s]), np.array([i["total_resid"] for i in s])
        )

    def team_fit(s):
        return ols(np.array([[i["own_b2b"], i["opp_b2b"]] for i in s]), np.array([i["resid"] for i in s]))

    out = {
        "generated_for": "rest residual diagnostic (owner request, 26 November 2026); research only",
        "methodology": config.get("methodology"),
        "box_file": args.box,
        "box_sha256": hashlib.sha256(Path(args.box).read_bytes()).hexdigest(),
        "games_with_rest": len(games),
        "games_total": len(rows),
        "back_to_back_team_games": sum(t["own_b2b"] for t in teams),
        "margin_residual_on_b2b": {"terms": ["home_b2b", "away_b2b"], **boot(games, margin_fit, rng)},
        "total_residual_on_b2b_count": {"terms": ["b2b_teams"], **boot(games, total_fit, rng)},
        "team_residual_on_b2b": {"terms": ["own_b2b", "opp_b2b"], **boot(teams, team_fit, rng)},
        "mean_team_residual": {
            "own_b2b": float(np.mean([t["resid"] for t in teams if t["own_b2b"]])),
            "rested": float(np.mean([t["resid"] for t in teams if not t["own_b2b"]])),
        },
    }
    (root / OUTPUT).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
