"""Full-season confirmation of the team minutes-band correction (owner request 22 November 2026).

The owner asked for the ``team_raw`` correction of ``minutes_band_bias_replay`` to be tested on
all 198 games of the v1.33 season replay, the result to decide a methodology version update.

Pre-registered before this script was first run (owner-facing, 22 November 2026):

- Candidate: ``team_raw`` (each player's projected minutes corrected by his team's walk-forward mean
  error, actual minus projected, in his projected-minutes band; league value where the team has no
  history in the band; team rescaled to Sleeper's projected minutes; points at each player's
  projected rate; v1.32 240-minute cap), applied on top of the v1.33 model with its team terms.
- Adopt only if, over all scored games, log loss improves with its 95% slate-bootstrap interval
  entirely below 0 **and** the margin MAE difference is not positive.
- Confirmation: the 125 seed and backfill games, never used to choose the variant, must point the
  same way (both log loss and margin MAE differences negative). The 73 native games were inspected
  while the variant was chosen and are reported separately.

Player rows: native games from this ledger's frozen snapshot; seed and backfill games from
nba-matchups' ``data/nba_sleeper.csv`` (their source; SHA-256 recorded, every team total checked
against the replay's frozen score). History: every replayed game at its replay snapshot plus native
games outside the replay at their latest pregame snapshot, each matched to the ESPN box score; a
target snapshot only uses games finished (tip + 4 hours) before its capture. Paired differences are bootstrapped by
slate; ``placebo`` relabels team histories across teams. Nothing is written to the ledger.

    python research/minutes_band_full_season_replay.py --root . \\
        --box research/inputs/espn_box_2026-27_2026-11-22.csv \\
        --sleeper-csv ../nba-matchups/data/nba_sleeper.csv
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from minutes_common import available, band, corrected_points, csv_snapshots, load_box, snapshot_players
from minutes_reallocation_replay import compare, per_game

from sleeper_nba.common import dt, load_config
from sleeper_nba.season import _replay, replay_rows
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/minutes_band_full_season_replay_2026-27.json"
CANDIDATE_K = 0
PLACEBO_RUNS = 50


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--box", required=True)
    parser.add_argument("--sleeper-csv", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    settings = config["matchup"]
    records = Ledger(root).records()
    observations, _ = _replay(records, root, config, config["matchup"]["seed_season"])
    box = load_box(args.box)
    archived = csv_snapshots(args.sleeper_csv)

    natives = [
        f
        for f in records
        if f["kind"] == "forecast"
        and f.get("provenance") == "native"
        and "players" in f.get("raw", {})
        and dt(f["captured_at"]) < dt(f["game"]["tip_utc"])
    ]
    by_key = {(f["game"]["game_id"], f["stage"], dt(f["captured_at"])): f for f in natives}

    # Player rows of every replayed snapshot, checked against the frozen (capped) team scores.
    players = {}
    for o in observations:
        if o["segment"] == "replay_native":
            f = by_key[(o["game_id"], o["snapshot_stage"], dt(o["snapshot_utc"]))]
            rows = {side: snapshot_players(f, o[side]) for side in ("home", "away")}
        else:
            snap = archived[(o["game_id"], dt(o["snapshot_utc"]))]
            rows = {side: [p for p in snap if p["team"] == o[side]] for side in ("home", "away")}
        for side in ("home", "away"):
            total = sum(p["points"] for p in rows[side]) * o[f"{side}_minutes_scale"]
            if abs(total - o[f"projected_{side}_score"]) > 0.05:
                raise ValueError(
                    f"{o['game_id']} {side}: players {total} != frozen {o[f'projected_{side}_score']}"
                )
            if (o["game_id"], o[side]) not in box:
                raise ValueError(f"{o['game_id']} {o[side]}: no box score")
            players[(o["game_id"], side)] = rows[side]

    # History: replayed games at their replay snapshot, other native games at their latest one.
    sources = {o["game_id"]: (o, None) for o in observations}
    for f in sorted(natives, key=lambda f: (dt(f["captured_at"]), f["id"])):
        if f["game"]["game_id"] not in sources or sources[f["game"]["game_id"]][1] is not None:
            sources[f["game"]["game_id"]] = (None, f)
    history = []
    for g, (o, f) in sources.items():
        if o is not None:
            tip, sides = dt(o["tip_utc"]), [(o[s], players[(g, s)]) for s in ("home", "away")]
        else:
            tip = dt(f["game"]["tip_utc"])
            sides = [(f["game"][s], snapshot_players(f, f["game"][s])) for s in ("home", "away")]
        for code, rows in sides:
            if (g, code) not in box:
                continue
            for p in rows:
                if p["minutes"] > 0:
                    actual = box[(g, code)].get(p["name"], (0.0, 0.0))[0]
                    history.append((code, tip, band(p["minutes"]), actual - p["minutes"]))

    def corrected(hist, k_team):
        out = []
        for o in observations:
            past = [x for x in hist if available(x[1]) <= dt(o["snapshot_utc"])]
            scores = {
                side: corrected_points(players[(o["game_id"], side)], o[side], past, k_team, settings)
                for side in ("home", "away")
            }
            out.append({**o, "projected_home_score": scores["home"], "projected_away_score": scores["away"]})
        return out

    baseline = per_game(replay_rows(observations, config))
    candidate = per_game(replay_rows(corrected(history, CANDIDATE_K), config))
    ids = sorted(baseline)
    segment = {o["game_id"]: o["segment"] for o in observations}
    subsets = {
        "all": ids,
        "seed_and_backfill": [g for g in ids if segment[g] != "replay_native"],
        "native": [g for g in ids if segment[g] == "replay_native"],
    }
    results = {name: compare(baseline, candidate, games) for name, games in subsets.items()}

    teams = sorted({x[0] for x in history})
    placebo = []
    for seed in range(PLACEBO_RUNS):
        relabel = dict(zip(teams, np.random.default_rng(seed).permutation(teams)))
        rows = per_game(
            replay_rows(corrected([(relabel[t], tip, b, e) for t, tip, b, e in history], 0), config)
        )
        r = compare(baseline, rows, ids)
        placebo.append((r["abs_error"]["difference"], r["log_loss"]["difference"]))
    placebo = np.array(placebo)

    overall = results["all"]
    confirm = results["seed_and_backfill"]
    rule = {
        "log_loss_interval_below_zero": overall["log_loss"]["ci95_slate_bootstrap"][1] < 0,
        "margin_mae_not_worse": overall["abs_error"]["difference"] <= 0,
        "seed_and_backfill_same_direction": confirm["log_loss"]["difference"] < 0
        and confirm["abs_error"]["difference"] < 0,
    }
    rule["adopt"] = all(rule.values())
    report = {
        "question": __doc__.splitlines()[0],
        "methodology": config["methodology"],
        "box_sha256": hashlib.sha256(Path(args.box).read_bytes()).hexdigest(),
        "sleeper_csv_sha256": hashlib.sha256(Path(args.sleeper_csv).read_bytes()).hexdigest(),
        "candidate": "team_raw (k = 0) on the v1.33 model with team terms",
        "history_games": len(sources),
        "history_player_rows": len(history),
        "games": {name: len(g) for name, g in subsets.items()},
        "results": results,
        "placebo": {
            "runs": PLACEBO_RUNS,
            "abs_error_mean": float(placebo[:, 0].mean()),
            "log_loss_mean": float(placebo[:, 1].mean()),
            "runs_at_least_as_good_abs_error": int(
                (placebo[:, 0] <= overall["abs_error"]["difference"]).sum()
            ),
            "runs_at_least_as_good_log_loss": int((placebo[:, 1] <= overall["log_loss"]["difference"]).sum()),
        },
        "decision_rule": rule,
        "notes": [
            "Differences are candidate minus v1.33: negative abs_error, total_abs_error or log_loss is better.",
            "Decision rule pre-registered before the first run; see module docstring.",
        ],
    }
    out = root / OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"history {len(sources)} games, {len(history)} player rows")
    for name, r in results.items():
        print(f"\n{name}: {r['games']} games, {r['slates']} slates")
        for m in ("abs_error", "total_abs_error", "correct", "log_loss"):
            x = r[m]
            lo, hi = x["ci95_slate_bootstrap"]
            print(
                f"  {m:15s} base {x['baseline']:.4f} cand {x['variant']:.4f} diff {x['difference']:+.4f} [{lo:+.4f}, {hi:+.4f}]"
            )
    print("\nplacebo", json.dumps(report["placebo"]))
    print("decision", json.dumps(rule))


if __name__ == "__main__":
    main()
