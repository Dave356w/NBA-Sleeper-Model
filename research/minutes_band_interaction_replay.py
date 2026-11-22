"""Minutes-band correction with and without the team terms (research only, owner request 22 November 2026).

Question: does the walk-forward ``team_raw`` minutes-band correction (``minutes_band_bias_replay``)
add to the team offense/defense terms, or stand in for them? The 73 native games of the v1.33
season replay are re-scored walk-forward in a 2 x 3 design: band correction off or on, crossed
with the team fit as configured, with offense and defense switched off (priors 1e9; global and
venue kept), and with no team fit at all (every prior 1e9). Paired differences are bootstrapped by
slate. Nothing is written to the ledger.

    python research/minutes_band_interaction_replay.py --root . \\
        --box research/inputs/espn_box_2026-27_2026-11-22.csv
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import numpy as np
from minutes_common import band, corrected_points, load_box, snapshot_players
from minutes_reallocation_replay import compare, per_game

from sleeper_nba.common import dt, load_config
from sleeper_nba.season import _replay, replay_rows
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/minutes_band_interaction_replay_2026-27.json"
OFF = 1e9


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

    natives = [
        f
        for f in records
        if f["kind"] == "forecast"
        and f.get("provenance") == "native"
        and "players" in f.get("raw", {})
        and dt(f["captured_at"]) < dt(f["game"]["tip_utc"])
    ]
    by_key = {(f["game"]["game_id"], f["stage"], dt(f["captured_at"])): f for f in natives}
    replayed = {o["game_id"] for o in observations if o["segment"] == "replay_native"}
    chosen = {
        o["game_id"]: by_key[(o["game_id"], o["snapshot_stage"], dt(o["snapshot_utc"]))]
        for o in observations
        if o["segment"] == "replay_native"
    }
    for f in sorted(natives, key=lambda f: (dt(f["captured_at"]), f["id"])):
        if f["game"]["game_id"] not in replayed:
            chosen[f["game"]["game_id"]] = f

    history = []
    for g, f in chosen.items():
        for side in ("home", "away"):
            code = f["game"][side]
            if (g, code) not in box:
                continue
            for p in snapshot_players(f, code):
                if p["minutes"] > 0:
                    actual = box[(g, code)].get(p["name"], (0.0, 0.0))[0]
                    history.append(
                        (code, dt(f["game"]["tip_utc"]), band(p["minutes"]), actual - p["minutes"])
                    )

    banded = []
    for o in observations:
        if o["segment"] == "replay_native":
            f = chosen[o["game_id"]]
            past = [x for x in history if x[1] < dt(o["snapshot_utc"])]
            scores = {
                side: corrected_points(snapshot_players(f, o[side]), o[side], past, 0, config["matchup"])
                for side in ("home", "away")
            }
            o = {**o, "projected_home_score": scores["home"], "projected_away_score": scores["away"]}
        banded.append(o)

    no_team = copy.deepcopy(config)
    no_team["matchup"].update(offense_prior=OFF, defense_prior=OFF)
    no_fit = copy.deepcopy(no_team)
    no_fit["matchup"].update(global_prior=OFF, venue_prior=OFF)
    fits = {"team terms": config, "global and venue only": no_team, "no team fit": no_fit}
    arms = {}
    for name, cfg in fits.items():
        arms[(name, "no band")] = per_game(replay_rows(observations, cfg))
        arms[(name, "band")] = per_game(replay_rows(banded, cfg))
    ids = sorted(replayed)
    current = arms[("team terms", "no band")]

    report = {
        "question": __doc__.splitlines()[0],
        "methodology": config["methodology"],
        "games": len(ids),
        "levels": {
            f"{fit} | {b}": {
                "abs_error": float(np.mean([v[g]["abs_error"] for g in ids])),
                "total_abs_error": float(np.mean([v[g]["total_abs_error"] for g in ids])),
                "correct": float(sum(v[g]["correct"] for g in ids)),
                "log_loss": float(np.mean([v[g]["log_loss"] for g in ids])),
            }
            for (fit, b), v in arms.items()
        },
        "versus_current": {
            f"{fit} | {b}": compare(current, v, ids)
            for (fit, b), v in arms.items()
            if (fit, b) != ("team terms", "no band")
        },
        "band_effect": {fit: compare(arms[(fit, "no band")], arms[(fit, "band")], ids) for fit in fits},
        "notes": [
            "Differences are second minus first: negative abs_error, total_abs_error or log_loss is better.",
            "Seed and backfill games stay unadjusted in training and are not scored.",
        ],
    }
    out = root / OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    for k, v in report["levels"].items():
        print(
            f"{k:32s} margin {v['abs_error']:.3f} total {v['total_abs_error']:.3f} winners {v['correct']:.0f} ll {v['log_loss']:.4f}"
        )
    for k, r in report["band_effect"].items():
        a, ll = r["abs_error"], r["log_loss"]
        print(
            f"band effect | {k:22s} margin {a['difference']:+.3f} {np.round(a['ci95_slate_bootstrap'], 3)} "
            f"ll {ll['difference']:+.4f} {np.round(ll['ci95_slate_bootstrap'], 4)} "
            f"total {r['total_abs_error']['difference']:+.3f}"
        )
    for k, r in report["versus_current"].items():
        a = r["abs_error"]
        print(f"vs current | {k:32s} margin {a['difference']:+.3f} {np.round(a['ci95_slate_bootstrap'], 3)}")


if __name__ == "__main__":
    main()
