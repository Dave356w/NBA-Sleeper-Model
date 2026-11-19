"""Scaling Sleeper team projections to a full game's minutes (research only, owner request 19 November 2026).

Question: Sleeper's projected minutes for a team rarely sum to exactly 240. If each team's player
projections are scaled so its minutes make a complete regulation game, does the model forecast
better?

For each team in each of the 185 game-day games of the v1.30/v1.31 season replay:

- ``baseline``: Sleeper's projected points as frozen (the current model).
- ``to_240`` (the owner's question): a team projected below 240 minutes has the missing minutes
  handed to its projected players in proportion to each one's projected minutes; a team above 240
  has every player scaled down by 240 / team minutes. Each player's points scale by the same ratio
  as his minutes.
- ``out`` and ``out_dtd`` (also run, as a related reading of the question): players flagged Out,
  IR or suspended (``out``), plus day-to-day and game-time-decision players (``out_dtd``), are
  dropped and their projected minutes handed to their teammates the same way.

Minutes added to a player are capped at 38 (owner setting). A player raised past 38 stops at 38 and
the rest goes to teammates still under the cap, in proportion to their minutes, until every minute
is placed or every player is at 38; minutes that cannot be placed are dropped and counted. A player
already projected above 38 keeps his own projection and receives nothing when minutes are added:
the cap never cuts a Sleeper projection. Scaling a team down is a plain ratio.

Each variant re-scores the same games through ``season.replay_rows`` (team fit and confidence scale
both refitted walk-forward on the variant's own projections). Paired differences against the
baseline are bootstrapped by slate. Player rows come from the frozen native forecasts in this ledger
and, for the 117 seed and 8 backfill games, from nba-matchups' ``data/nba_sleeper.csv`` (the seed's
source; its SHA-256 is recorded and every team total is checked against the pinned seed). Nothing is
written to the ledger.

    python research/minutes_reallocation_replay.py --root . \\
        --sleeper-csv ../nba-matchups/data/nba_sleeper.csv
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.special import ndtr

from sleeper_nba.common import dt, load_config, team
from sleeper_nba.season import _replay, replay_rows
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/minutes_reallocation_replay_2026-27.json"
FULL_GAME = 240.0
CAP = 38.0
BOOTSTRAP = 10_000
OUT = {"out", "ir", "sus", "suspended"}
DAY_TO_DAY = {"dtd", "gtd"}
# name -> (statuses dropped, team minutes target or None)
VARIANTS = {
    "to_240": (frozenset(), FULL_GAME),
    "out": (OUT, None),
    "out_dtd": (OUT | DAY_TO_DAY, None),
}


def reallocate(players, dropped_statuses, cap=CAP, target=None):
    """Team points after reallocating minutes; also returns diagnostics.

    With `target`, no player is dropped: the team's projected minutes are brought to `target`
    (added through the capped proportional rule, or removed by a plain ratio). Otherwise the
    dropped players' minutes are handed to their teammates.
    """
    keep = [p for p in players if p["status"] not in dropped_statuses]
    freed = sum(p["minutes"] for p in players if p["status"] in dropped_statuses)
    if target is not None:
        minutes = sum(p["minutes"] for p in keep)
        if minutes > target:
            ratio = target / minutes
            return sum(p["points"] for p in keep) * ratio, {
                "freed_minutes": 0.0,
                "removed_minutes": minutes - target,
                "unplaced_minutes": 0.0,
                "capped_players": 0,
            }
        freed = target - minutes
    new = {i: p["minutes"] for i, p in enumerate(keep)}
    open_ = {i for i, p in enumerate(keep) if 0 < p["minutes"] < cap}
    left = freed
    while left > 1e-9 and open_:
        base = sum(keep[i]["minutes"] for i in open_)
        ratio = (sum(new[i] for i in open_) + left) / base
        over = {i for i in open_ if keep[i]["minutes"] * ratio > cap}
        if not over:
            for i in open_:
                new[i] = keep[i]["minutes"] * ratio
            left = 0.0
            break
        for i in over:
            left -= cap - new[i]
            new[i] = cap
        open_ -= over
    points = sum(
        p["points"] * (new[i] / p["minutes"]) if p["minutes"] > 0 else p["points"] for i, p in enumerate(keep)
    )
    return points, {
        "freed_minutes": freed,
        "removed_minutes": 0.0,
        "unplaced_minutes": max(left, 0.0),
        "capped_players": sum(1 for i in new if new[i] >= cap - 1e-9 and keep[i]["minutes"] < cap),
    }


def csv_players(path):
    by_snapshot = collections.defaultdict(dict)
    with open(path, newline="") as handle:
        for r in csv.DictReader(handle):
            if r["kind"] != "first":
                continue
            key = (r["game_id"], dt(r["snapshot_utc"]))
            by_snapshot[key][(team(r["team"]), r["sleeper_id"])] = {
                "team": team(r["team"]),
                "minutes": float(r["minutes"] or 0),
                "points": float(r["pts"] or 0),
                "status": (r["injury_status"] or "").strip().lower(),
            }
    return by_snapshot


def native_players(forecast):
    out = []
    for row in forecast["raw"]["raw_rows"]:
        stats = row.get("stats") or {}
        if not stats:
            continue  # Unprojected roster placeholder: no minutes, no points.
        out.append(
            {
                "team": team(row["team"]),
                "minutes": stats["sp"] / 60,
                "points": stats["pts"],
                "status": ((row.get("player") or {}).get("injury_status") or "").strip().lower(),
            }
        )
    return out


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
            "total_abs_error": abs(r["total"] - (r["actual_home_score"] + r["actual_away_score"])),
            "correct": float((r["margin"] > 0) == won) if r["margin"] != 0 else 0.5,
            "log_loss": -math.log(p if won else 1 - p),
            "log_loss_a10": -math.log(p10 if won else 1 - p10),
        }
    return out


def compare(a, b, games, seed=20261119):
    slates = sorted({a[g]["slate"] for g in games})
    by_slate = {s: [g for g in games if a[g]["slate"] == s] for s in slates}
    rng = np.random.default_rng(seed)
    draws = [rng.choice(len(slates), len(slates)) for _ in range(BOOTSTRAP)]
    result = {"games": len(games), "slates": len(slates)}
    for metric in ("abs_error", "total_abs_error", "correct", "log_loss", "log_loss_a10"):
        diff = {s: [b[g][metric] - a[g][metric] for g in by_slate[s]] for s in slates}
        boot = [float(np.mean([v for i in idx for v in diff[slates[i]]])) for idx in draws]
        result[metric] = {
            "baseline": float(np.mean([a[g][metric] for g in games])),
            "variant": float(np.mean([b[g][metric] for g in games])),
            "difference": float(np.mean([b[g][metric] - a[g][metric] for g in games])),
            "ci95_slate_bootstrap": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--sleeper-csv", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    observations, _ = _replay(records, root, config, config["matchup"]["seed_season"])

    archived = csv_players(args.sleeper_csv)
    natives = {
        (f["game"]["game_id"], f["stage"], dt(f["captured_at"])): f
        for f in records
        if f["kind"] == "forecast" and f.get("provenance") == "native" and "raw_rows" in f.get("raw", {})
    }
    players = {}
    for o in observations:
        if o["segment"] == "replay_native":
            rows = native_players(natives[(o["game_id"], o["snapshot_stage"], dt(o["snapshot_utc"]))])
        else:
            rows = list(archived[(o["game_id"], dt(o["snapshot_utc"]))].values())
        for side in ("home", "away"):
            mine = [p for p in rows if p["team"] == o[side]]
            total = sum(p["points"] for p in mine)
            if abs(total - o[f"projected_{side}_score"]) > 0.05:
                raise ValueError(
                    f"{o['game_id']} {side}: player rows {total} != frozen {o[f'projected_{side}_score']}"
                )
            players[(o["game_id"], side)] = mine

    baseline = per_game(replay_rows(observations, config))
    report = {
        "question": __doc__.splitlines()[0],
        "methodology": config["methodology"],
        "cap_minutes": CAP,
        "sleeper_csv_sha256": hashlib.sha256(Path(args.sleeper_csv).read_bytes()).hexdigest(),
        "scored_games": len(baseline),
        "variants": {},
        "notes": [
            "Differences are variant minus baseline: negative abs_error, total_abs_error or log_loss "
            "means the reallocation forecast better; positive correct means more winners.",
            "log_loss uses the walk-forward calibrated scale; log_loss_a10 holds a = 10 fixed.",
            "Retrospective analysis on previously inspected data. The owner set to_240 and the "
            "38-minute cap; out and out_dtd were an earlier reading of the request, kept for context. "
            "No threshold was searched.",
        ],
    }
    for name, (statuses, target) in VARIANTS.items():
        variant_obs, diag = [], collections.Counter()
        changed_games, changed_teams, raw_errors = set(), 0, {"baseline": [], "variant": []}
        for o in observations:
            scores = {}
            for side in ("home", "away"):
                points, d = reallocate(players[(o["game_id"], side)], statuses, target=target)
                scores[side] = points
                if d["freed_minutes"] > 1e-9 or d["removed_minutes"] > 1e-9:
                    changed_teams += 1
                    changed_games.add(o["game_id"])
                diag.update(d)
                actual = o[f"actual_{side}_score"]
                raw_errors["baseline"].append(abs(o[f"projected_{side}_score"] - actual))
                raw_errors["variant"].append(abs(points - actual))
            variant_obs.append(
                {**o, "projected_home_score": scores["home"], "projected_away_score": scores["away"]}
            )
        rows = per_game(replay_rows(variant_obs, config))
        report["variants"][name] = {
            "dropped_statuses": sorted(statuses),
            "team_minutes_target": target,
            "removed_minutes": round(diag["removed_minutes"], 1),
            "team_games_changed": changed_teams,
            "games_changed": len(changed_games),
            "freed_minutes": round(diag["freed_minutes"], 1),
            "unplaced_minutes": round(diag["unplaced_minutes"], 1),
            "players_hitting_cap": diag["capped_players"],
            "raw_team_points_mae": {k: float(np.mean(v)) for k, v in raw_errors.items()},
            "all_scored": compare(baseline, rows, sorted(baseline)),
            "changed_games": compare(baseline, rows, sorted(changed_games)) if changed_games else None,
        }
    out = root / OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    for name, v in report["variants"].items():
        print(
            f"\n{name}: {v['games_changed']} games / {v['team_games_changed']} team-games changed, "
            f"{v['freed_minutes']} min added, {v['removed_minutes']} min removed, "
            f"{v['unplaced_minutes']} unplaced, {v['players_hitting_cap']} capped"
        )
        print(
            "  raw team-points MAE  baseline {baseline:.3f}  variant {variant:.3f}".format(
                **v["raw_team_points_mae"]
            )
        )
        for part in ("all_scored", "changed_games"):
            r = v[part]
            if not r:
                continue
            print(f"  {part}: {r['games']} games, {r['slates']} slates")
            for m in ("abs_error", "total_abs_error", "correct", "log_loss", "log_loss_a10"):
                x = r[m]
                lo, hi = x["ci95_slate_bootstrap"]
                print(
                    f"    {m:15s} base {x['baseline']:.4f}  var {x['variant']:.4f}  "
                    f"diff {x['difference']:+.4f}  95% [{lo:+.4f}, {hi:+.4f}]"
                )


if __name__ == "__main__":
    main()
