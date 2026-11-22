"""Redistributing small projected minutes to the rotation (research only, owner request 22 November 2026).

Question: players Sleeper projects below n minutes often do not play, or play less. If their projected
minutes (and points) are taken away and handed to teammates projected at n minutes or more, in
proportion to each one's projected minutes, does the model forecast better, and at what n?

For every native game in the v1.33 season replay (the game's frozen snapshot), and n in
``THRESHOLDS``, each team's players projected above 0 and below n minutes are dropped and their
minutes reallocated with ``minutes_reallocation_replay.reallocate`` (owner cap of 38 minutes per
player; minutes that cannot be placed are dropped and counted). Points scale with minutes at each
receiving player's own projected rate, so team minutes are unchanged, and the v1.32 240-minute cap
is then applied as in production (snapshot scores in the replay are already capped).

Two readings:

- ``diagnostic``: each projected-minutes bucket against ESPN box scores (share that did not play,
  actual minutes and points against projected), from the same frozen snapshots.
- ``replay``: every n re-scores the season through ``season.replay_rows`` (team fit and confidence
  scale refitted walk-forward). The 125 seed and backfill games have no player rows in this
  repository and stay unadjusted: they train the fit and scale but are not scored. Paired
  differences against the baseline are bootstrapped by slate. Raw team-point error against the
  final score is also reported.

Several thresholds are compared on the same, previously inspected games, so the best one is chosen
with optimism. Nothing is written to the ledger.

    python research/low_minutes_redistribution_replay.py --root . \\
        --box research/inputs/espn_box_2026-27_2026-11-22.csv
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

import numpy as np
from minutes_reallocation_replay import compare, per_game, reallocate
from out_minutes_adjustment_replay import norm

from sleeper_nba.common import dt, load_config, team
from sleeper_nba.matchup import minutes_scale
from sleeper_nba.season import _replay, replay_rows
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/low_minutes_redistribution_replay_2026-27.json"
THRESHOLDS = (3, 5, 8, 10, 12, 15)
BUCKETS = ((0, 3), (3, 5), (5, 8), (8, 10), (10, 12), (12, 15), (15, 20), (20, 30), (30, 99))
LOW = "__below_threshold__"


def load_box(path):
    import csv

    box = collections.defaultdict(dict)
    with open(path, newline="") as handle:
        for r in csv.DictReader(handle):
            box[(r["game_id"], team(r["team"]))][norm(r["name"])] = (float(r["minutes"]), float(r["points"]))
    return box


def snapshot_players(forecast, code):
    out = []
    for p in forecast["raw"]["players"]:
        if team(p["team"]) != code or p.get("minutes") is None:
            continue
        out.append(
            {
                "name": norm(p["name"]),
                "minutes": float(p["minutes"]),
                "points": float(p["points"] or 0),
                "status": (p.get("injury_status") or "").strip().lower(),
            }
        )
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--box", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    observations, _ = _replay(records, root, config, config["matchup"]["seed_season"])
    box = load_box(args.box)
    natives = {
        (f["game"]["game_id"], f["stage"], dt(f["captured_at"])): f
        for f in records
        if f["kind"] == "forecast" and f.get("provenance") == "native" and "players" in f.get("raw", {})
    }

    players = {}
    for o in observations:
        if o["segment"] != "replay_native":
            continue
        f = natives[(o["game_id"], o["snapshot_stage"], dt(o["snapshot_utc"]))]
        for side in ("home", "away"):
            mine = snapshot_players(f, o[side])
            total = sum(p["points"] for p in mine) * o[f"{side}_minutes_scale"]
            if abs(total - o[f"projected_{side}_score"]) > 0.05:
                raise ValueError(
                    f"{o['game_id']} {side}: players {total} != frozen {o[f'projected_{side}_score']}"
                )
            if (o["game_id"], o[side]) not in box:
                raise ValueError(f"{o['game_id']} {o[side]}: no box score")
            players[(o["game_id"], side)] = mine

    # Diagnostic: projected-minutes buckets against the box score.
    diagnostic = []
    for lo, hi in BUCKETS:
        rows = []
        for (g, side), mine in players.items():
            code = next(o[side] for o in observations if o["game_id"] == g)
            for p in mine:
                if lo < p["minutes"] <= hi if lo else 0 < p["minutes"] <= hi:
                    am, ap = box[(g, code)].get(p["name"], (0.0, 0.0))
                    rows.append((p["minutes"], p["points"], am, ap))
        if not rows:
            continue
        a = np.array(rows)
        diagnostic.append(
            {
                "projected_minutes": [lo, hi],
                "players": len(rows),
                "did_not_play": float((a[:, 2] == 0).mean()),
                "projected_minutes_mean": float(a[:, 0].mean()),
                "actual_minutes_mean": float(a[:, 2].mean()),
                "projected_points_mean": float(a[:, 1].mean()),
                "actual_points_mean": float(a[:, 3].mean()),
                "points_bias": float((a[:, 3] - a[:, 1]).mean()),
            }
        )

    baseline = per_game(replay_rows(observations, config))
    native_ids = sorted(o["game_id"] for o in observations if o["segment"] == "replay_native")
    report = {
        "question": __doc__.splitlines()[0],
        "methodology": config["methodology"],
        "box_sha256": hashlib.sha256(Path(args.box).read_bytes()).hexdigest(),
        "scored_native_games": len(native_ids),
        "cap_minutes": 38.0,
        "diagnostic": diagnostic,
        "variants": {},
        "notes": [
            "Differences are variant minus baseline over the native games only: negative abs_error, "
            "total_abs_error or log_loss means the redistribution forecast better; positive correct means "
            "more winners.",
            "raw_team_points_mae compares unadjusted Sleeper team points with the final score.",
            "Seed and backfill games (no player rows here) stay unadjusted in training and are not scored.",
            "Retrospective; several thresholds compared on previously inspected games.",
        ],
    }
    for n in THRESHOLDS:
        variant, diag, raw_err = [], collections.Counter(), {"baseline": [], "variant": []}
        for o in observations:
            if o["segment"] == "replay_native":
                scores = {}
                for side in ("home", "away"):
                    mine = [
                        {**p, "status": LOW if 0 < p["minutes"] < n else p["status"]}
                        for p in players[(o["game_id"], side)]
                    ]
                    points, d = reallocate(mine, {LOW})
                    team_minutes = sum(p["minutes"] for p in mine) - d["unplaced_minutes"]
                    scores[side] = points * minutes_scale(team_minutes, config["matchup"])
                    diag.update(d)
                    diag["players_dropped"] += sum(1 for p in mine if p["status"] == LOW)
                    actual = o[f"actual_{side}_score"]
                    raw_err["baseline"].append(abs(o[f"projected_{side}_score"] - actual))
                    raw_err["variant"].append(abs(scores[side] - actual))
                o = {**o, "projected_home_score": scores["home"], "projected_away_score": scores["away"]}
            variant.append(o)
        rows = per_game(replay_rows(variant, config))
        report["variants"][f"below_{n}"] = {
            "players_dropped": diag["players_dropped"],
            "minutes_moved": round(diag["freed_minutes"], 1),
            "unplaced_minutes": round(diag["unplaced_minutes"], 1),
            "players_hitting_cap": diag["capped_players"],
            "raw_team_points_mae": {k: float(np.mean(v)) for k, v in raw_err.items()},
            "replay": compare(baseline, rows, native_ids),
        }

    out = root / OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(native_ids)} native games")
    for d in diagnostic:
        print(
            "  proj {lo:>2}-{hi:<2} n {players:4d} dnp {did_not_play:.2f} min {projected_minutes_mean:5.1f}->"
            "{actual_minutes_mean:5.1f} pts {projected_points_mean:5.1f}->{actual_points_mean:5.1f}".format(
                lo=d["projected_minutes"][0], hi=d["projected_minutes"][1], **d
            )
        )
    for name, v in report["variants"].items():
        r = v["replay"]
        raw = v["raw_team_points_mae"]
        print(
            f"\n{name}: {v['players_dropped']} players, {v['minutes_moved']} min moved, "
            f"{v['unplaced_minutes']} unplaced; raw team MAE {raw['baseline']:.3f} -> {raw['variant']:.3f}"
        )
        for m in ("abs_error", "total_abs_error", "correct", "log_loss"):
            x = r[m]
            lo, hi = x["ci95_slate_bootstrap"]
            print(
                f"  {m:15s} base {x['baseline']:.4f} var {x['variant']:.4f} diff {x['difference']:+.4f} [{lo:+.4f}, {hi:+.4f}]"
            )


if __name__ == "__main__":
    main()
