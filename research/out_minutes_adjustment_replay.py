"""Team point adjustment for Out players, from recent minutes (research only, owner request 22 November 2026).

Question: subtract from each team's projected points the recent average minutes of its players
listed Out, divided by k (k = 10, 8 or 5). Does the model forecast better?

For every native game in the v1.33 season replay (the game's frozen snapshot), each team's Out list
is every player in the frozen Sleeper rows whose injury status is Out, IR or suspended, whether or
not Sleeper projected him. Each listed player's recent minutes come from ESPN box scores of games
finished before the snapshot (tip + 4 hours), matched within team on a normalized name, in two readings:

- ``team5``: his average over the team's last 5 games, a game he missed counting 0. A player out
  for weeks has already left the team's recent results, so he is charged little or nothing.
- ``played5``: his average over his own last 5 games played (any team this season), however long
  ago. A long-term absentee is charged in full every game.

The adjustment ``-sum(minutes) / k`` is added to the team's projected points, and every variant
re-scores the season through ``season.replay_rows`` (team fit and confidence scale refitted
walk-forward on the variant's own projections). The 125 seed and backfill games have no player rows
in this repository (they are in nba-matchups' ``data/nba_sleeper.csv``) and stay unadjusted: they
train the fit and the scale as in the baseline but are not scored in the comparison. Paired
differences are bootstrapped by slate. Nothing is written to the ledger.

    python research/out_minutes_adjustment_replay.py --root . --box <espn_box.csv>

The box file has columns game_id, tip_utc, team, espn_player_id, name, minutes, points, dnp (one
row per player listed in each completed regular-season game's ESPN summary box score).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from minutes_common import available, load_box_history, norm
from minutes_reallocation_replay import compare, per_game

from sleeper_nba.common import dt, load_config, team
from sleeper_nba.season import _replay, replay_rows
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/out_minutes_adjustment_replay_2026-27.json"
OUT = {"out", "ir", "sus", "suspended"}
DIVISORS = (10, 8, 5)
WINDOW = 5


def recent_minutes(name, side, before, schedule, minutes, by_player, mode):
    if mode == "team5":
        past = [g for tip, g in schedule[side] if available(tip) <= before][-WINDOW:]
        if not past:
            return 0.0, False
        values = [minutes[(side, g)].get(name, 0.0) for g in past]
        return float(np.mean(values)), any(name in minutes[(side, g)] for g in past)
    past = [m for tip, m in by_player.get(name, []) if available(tip) <= before][-WINDOW:]
    return (float(np.mean(past)), True) if past else (0.0, False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--box", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    observations, _ = _replay(records, root, config, config["matchup"]["seed_season"])
    schedule, minutes, by_player = load_box_history(args.box)

    natives = {
        (f["game"]["game_id"], f["stage"], dt(f["captured_at"])): f
        for f in records
        if f["kind"] == "forecast" and f.get("provenance") == "native" and "raw_rows" in f.get("raw", {})
    }
    out_minutes = {mode: {} for mode in ("team5", "played5")}
    listed = []
    for o in observations:
        if o["segment"] != "replay_native":
            continue
        f = natives[(o["game_id"], o["snapshot_stage"], dt(o["snapshot_utc"]))]
        before = dt(o["snapshot_utc"])
        for side in ("home", "away"):
            code = o[side]
            names = {
                norm(
                    f"{(r.get('player') or {}).get('first_name', '')} {(r.get('player') or {}).get('last_name', '')}"
                )
                for r in f["raw"]["raw_rows"]
                if team(r["team"]) == code
                and ((r.get("player") or {}).get("injury_status") or "").strip().lower() in OUT
            }
            for mode in out_minutes:
                total = 0.0
                for n in sorted(names):
                    m, found = recent_minutes(n, code, before, schedule, minutes, by_player, mode)
                    total += m
                    if mode == "played5":
                        listed.append(
                            {"game_id": o["game_id"], "team": code, "name": n, "minutes": m, "found": found}
                        )
                out_minutes[mode][(o["game_id"], side)] = total

    baseline_rows = replay_rows(observations, config)
    native_ids = sorted(o["game_id"] for o in observations if o["segment"] == "replay_native")
    baseline = per_game(baseline_rows)

    # Implied divisor: walk-forward residual of each native team-game against its Out minutes.
    by_game = {r["game_id"]: r for r in baseline_rows}
    implied = {}
    for mode, table in out_minutes.items():
        xs, ys = [], []
        for g in native_ids:
            r = by_game[g]
            home_pred = (r["total"] + r["margin"]) / 2
            away_pred = (r["total"] - r["margin"]) / 2
            xs += [table[(g, "home")], table[(g, "away")]]
            ys += [r["actual_home_score"] - home_pred, r["actual_away_score"] - away_pred]
        x, y = np.array(xs), np.array(ys)
        slope, intercept = np.polyfit(x, y, 1)
        rng = np.random.default_rng(20261122)
        boot = []
        for _ in range(4000):
            i = rng.integers(0, len(x), len(x))
            boot.append(np.polyfit(x[i], y[i], 1)[0])
        implied[mode] = {
            "team_games": len(x),
            "mean_out_minutes": float(x.mean()),
            "share_with_out_minutes": float((x > 0).mean()),
            "slope_points_per_minute": float(slope),
            "slope_ci95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            "intercept": float(intercept),
        }

    report = {
        "question": __doc__.splitlines()[0],
        "methodology": config["methodology"],
        "box_sha256": hashlib.sha256(Path(args.box).read_bytes()).hexdigest(),
        "scored_native_games": len(native_ids),
        "out_players_listed": len(listed),
        "out_players_unmatched_in_box": sum(1 for x in listed if not x["found"]),
        "implied_points_per_out_minute": implied,
        "variants": {},
        "notes": [
            "Differences are variant minus baseline over the native games only: negative abs_error, "
            "total_abs_error or log_loss means the adjustment forecast better; positive correct means "
            "more winners.",
            "Seed and backfill games (no player rows here) stay unadjusted in training and are not scored.",
            "Implied slope: walk-forward replay residual (actual - predicted team points) regressed on Out "
            "minutes; a divisor k corresponds to a slope of -1/k. Team-game bootstrap.",
            "Retrospective analysis on previously inspected data; the divisors 10, 8 and 5 were set by the owner.",
        ],
    }
    for mode, table in out_minutes.items():
        for k in DIVISORS:
            variant = []
            for o in observations:
                if o["segment"] == "replay_native":
                    o = {
                        **o,
                        "projected_home_score": o["projected_home_score"] - table[(o["game_id"], "home")] / k,
                        "projected_away_score": o["projected_away_score"] - table[(o["game_id"], "away")] / k,
                    }
                variant.append(o)
            rows = per_game(replay_rows(variant, config))
            report["variants"][f"{mode}_k{k}"] = compare(baseline, rows, native_ids)
    out = root / OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("scored_native_games", "out_players_listed", "out_players_unmatched_in_box")
            }
        )
    )
    print(json.dumps(implied, indent=1))
    for name, r in report["variants"].items():
        print(f"\n{name}: {r['games']} games, {r['slates']} slates")
        for m in ("abs_error", "total_abs_error", "correct", "log_loss"):
            x = r[m]
            lo, hi = x["ci95_slate_bootstrap"]
            print(
                f"  {m:15s} base {x['baseline']:.4f} var {x['variant']:.4f} diff {x['difference']:+.4f} [{lo:+.4f}, {hi:+.4f}]"
            )


if __name__ == "__main__":
    main()
