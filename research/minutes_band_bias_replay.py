"""Walk-forward minutes correction by projected-minutes band (research only, owner request 22 November 2026).

Question: Sleeper's minutes errors differ by role (starters are over-projected, deep bench
under-projected). If each player's projected minutes are corrected by the rolling error of his
projected-minutes band, league-wide or for his own team, does the model forecast better?

History: every native game in this ledger with an ESPN box score (one snapshot per game: the replay
snapshot where the game is replayed, otherwise its latest pregame native snapshot). A player
projected above 0 minutes gives one observation, actual minus projected minutes, in his band
(``BANDS``). For a target snapshot only games tipped before its capture are used.

Correction for a player in band b of team t, from the history before the snapshot:

- ``league``: league-wide band mean error, shrunk toward 0 with ``K_LEAGUE`` pseudo-observations.
- ``team``: the team's own band mean error, shrunk toward the league value with ``K_TEAM``
  pseudo-observations (league value as above).
- ``team_loose``: as ``team`` with weaker shrinkage (``K_TEAM_LOOSE``).
- ``team_raw`` (added after the first run, owner request): the team's own band mean with no
  shrinkage, the league value only where the team has no history in the band.

Corrected minutes are floored at 0 and then rescaled so the team keeps Sleeper's projected total
(the correction moves minutes between roles, it does not add any). Each player's points scale with
his minutes at his projected rate, and the v1.32 240-minute cap applies as in production. Every
variant re-scores the v1.33 season replay walk-forward on its native games through
``season.replay_rows``; the 125 seed and backfill games have no player rows here and stay
unadjusted (training only, not scored). Paired differences are bootstrapped by slate.

``placebo_team_raw`` relabels each team's history as another team's (``PLACEBO_RUNS`` fixed
permutations) and re-runs ``team_raw``: if the gain comes from team-specific errors, the shuffled
runs should do worse. ``halves`` splits the scored games at 14 November.

``persistence`` asks the prior question directly: for each team, band and game, is the team's
earlier band error (relative to the league's) correlated with its error in that game?

Settings were fixed before running and not tuned. Nothing is written to the ledger.

    python research/minutes_band_bias_replay.py --root . \\
        --box research/inputs/espn_box_2026-27_2026-11-22.csv
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

import numpy as np
from minutes_common import BANDS, K_LEAGUE, band, corrected_points, load_box, snapshot_players
from minutes_reallocation_replay import compare, per_game

from sleeper_nba.common import dt, load_config
from sleeper_nba.season import _replay, replay_rows
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/minutes_band_bias_replay_2026-27.json"
K_TEAM = 20
K_TEAM_LOOSE = 5
VARIANTS = {"league": None, "team": K_TEAM, "team_loose": K_TEAM_LOOSE, "team_raw": 0}
PLACEBO_RUNS = 50
SPLIT = "2026-11-14"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--box", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    settings = config["matchup"]
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
    chosen = {}
    for o in observations:
        if o["segment"] == "replay_native":
            chosen[o["game_id"]] = by_key[(o["game_id"], o["snapshot_stage"], dt(o["snapshot_utc"]))]
    for f in sorted(natives, key=lambda f: (dt(f["captured_at"]), f["id"])):
        g = f["game"]["game_id"]
        if g not in chosen or g not in {
            o["game_id"] for o in observations if o["segment"] == "replay_native"
        }:
            chosen[g] = f  # latest pregame snapshot of a game outside the replay

    # History rows: (team, tip, band, actual - projected minutes).
    history = []
    for g, f in chosen.items():
        tip = dt(f["game"]["tip_utc"])
        for side in ("home", "away"):
            code = f["game"][side]
            if (g, code) not in box:
                continue
            for p in snapshot_players(f, code):
                if p["minutes"] > 0:
                    actual = box[(g, code)].get(p["name"], (0.0, 0.0))[0]
                    history.append((code, tip, band(p["minutes"]), actual - p["minutes"]))

    # Persistence: team's earlier band error relative to the league's, against the next game's.
    by_team_game = collections.defaultdict(list)
    for code, tip, b, e in history:
        by_team_game[(code, tip, b)].append(e)
    pairs = collections.defaultdict(list)
    for (code, tip, b), errs in by_team_game.items():
        past = [x for x in history if x[1] < tip]
        team_past = [e for t, _, bb, e in past if t == code and bb == b]
        league_past = [e for _, _, bb, e in past if bb == b]
        if len(team_past) >= 5 and league_past:
            league_now = np.mean(league_past)
            pairs[b].append((np.mean(team_past) - league_now, np.mean(errs) - league_now))
    persistence = {}
    for b, xy in pairs.items():
        a = np.array(xy)
        rng = np.random.default_rng(20261122)
        boot = [
            np.corrcoef(a[i, 0], a[i, 1])[0, 1]
            for i in (rng.integers(0, len(a), len(a)) for _ in range(2000))
        ]
        persistence[f"{BANDS[b][0]}-{BANDS[b][1]}"] = {
            "team_games": len(a),
            "correlation": float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]),
            "ci95": [float(np.nanpercentile(boot, 2.5)), float(np.nanpercentile(boot, 97.5))],
            "slope": float(np.polyfit(a[:, 0], a[:, 1], 1)[0]),
        }

    baseline = per_game(replay_rows(observations, config))
    native_ids = sorted(o["game_id"] for o in observations if o["segment"] == "replay_native")
    report = {
        "question": __doc__.splitlines()[0],
        "methodology": config["methodology"],
        "box_sha256": hashlib.sha256(Path(args.box).read_bytes()).hexdigest(),
        "bands": [list(b) for b in BANDS],
        "shrinkage": {"league": K_LEAGUE, "team": K_TEAM, "team_loose": K_TEAM_LOOSE, "team_raw": 0},
        "history_games": len(chosen),
        "history_player_rows": len(history),
        "scored_native_games": len(native_ids),
        "league_band_error_all_history": {
            f"{lo}-{hi}": float(np.mean([e for _, _, b, e in history if b == i]))
            for i, (lo, hi) in enumerate(BANDS)
        },
        "persistence": persistence,
        "variants": {},
        "notes": [
            "Differences are variant minus baseline over the native replay games: negative abs_error, "
            "total_abs_error or log_loss means the correction forecast better.",
            "Seed and backfill games (no player rows here) stay unadjusted in training and are not scored.",
            "Shrinkage and bands were fixed before running; retrospective analysis on inspected games.",
        ],
    }

    def run(k_team, hist):
        variant, raw_err, moved = [], {"baseline": [], "variant": []}, []
        for o in observations:
            if o["segment"] == "replay_native":
                f = chosen[o["game_id"]]
                before = dt(o["snapshot_utc"])
                past = [x for x in hist if x[1] < before]
                scores = {}
                for side in ("home", "away"):
                    code = o[side]
                    scores[side] = corrected_points(snapshot_players(f, code), code, past, k_team, settings)
                    moved.append(scores[side] - o[f"projected_{side}_score"])
                    actual = o[f"actual_{side}_score"]
                    raw_err["baseline"].append(abs(o[f"projected_{side}_score"] - actual))
                    raw_err["variant"].append(abs(scores[side] - actual))
                o = {**o, "projected_home_score": scores["home"], "projected_away_score": scores["away"]}
            variant.append(o)
        return per_game(replay_rows(variant, config)), raw_err, moved

    def halves(rows):
        out = {}
        for label, keep in (
            ("to_2026-11-14", lambda s: s <= SPLIT),
            ("from_2026-11-15", lambda s: s > SPLIT),
        ):
            games = [g for g in native_ids if keep(baseline[g]["slate"])]
            out[label] = {
                "games": len(games),
                "abs_error": float(np.mean([rows[g]["abs_error"] - baseline[g]["abs_error"] for g in games])),
                "log_loss": float(np.mean([rows[g]["log_loss"] - baseline[g]["log_loss"] for g in games])),
            }
        return out

    for name, k_team in VARIANTS.items():
        rows, raw_err, moved = run(k_team, history)
        report["variants"][name] = {
            "mean_abs_team_points_change": float(np.mean(np.abs(moved))),
            "mean_team_points_change": float(np.mean(moved)),
            "raw_team_points_mae": {k: float(np.mean(v)) for k, v in raw_err.items()},
            "replay": compare(baseline, rows, native_ids),
            "halves": halves(rows),
        }

    # Placebo: each team's history is relabelled as another team's (fixed random permutations).
    if PLACEBO_RUNS:
        real = report["variants"]["team_raw"]["replay"]
        teams = sorted({x[0] for x in history})
        draws = []
        for seed in range(PLACEBO_RUNS):
            relabel = dict(zip(teams, np.random.default_rng(seed).permutation(teams)))
            rows, _, _ = run(0, [(relabel[t], tip, b, e) for t, tip, b, e in history])
            draws.append(compare(baseline, rows, native_ids))
        report["placebo_team_raw"] = {
            "runs": PLACEBO_RUNS,
            "abs_error": [d["abs_error"]["difference"] for d in draws],
            "log_loss": [d["log_loss"]["difference"] for d in draws],
            "runs_at_least_as_good_abs_error": sum(
                d["abs_error"]["difference"] <= real["abs_error"]["difference"] for d in draws
            ),
            "runs_at_least_as_good_log_loss": sum(
                d["log_loss"]["difference"] <= real["log_loss"]["difference"] for d in draws
            ),
        }

    out = root / OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"history {len(chosen)} games, {len(history)} player rows; scored {len(native_ids)}")
    print("league band error", json.dumps(report["league_band_error_all_history"]))
    print("persistence", json.dumps(persistence, indent=1))
    for name, v in report["variants"].items():
        r, raw = v["replay"], v["raw_team_points_mae"]
        print(
            f"\n{name}: team points change |mean| {v['mean_abs_team_points_change']:.2f} "
            f"(mean {v['mean_team_points_change']:+.2f}); raw team MAE {raw['baseline']:.3f} -> {raw['variant']:.3f}"
        )
        for m in ("abs_error", "total_abs_error", "correct", "log_loss"):
            x = r[m]
            lo, hi = x["ci95_slate_bootstrap"]
            print(
                f"  {m:15s} base {x['baseline']:.4f} var {x['variant']:.4f} diff {x['difference']:+.4f} [{lo:+.4f}, {hi:+.4f}]"
            )

        print("  halves", json.dumps(v["halves"]))
    if "placebo_team_raw" in report:
        pl = report["placebo_team_raw"]
        print(
            f"\nplacebo ({pl['runs']} runs): at least as good as team_raw on margin "
            f"{pl['runs_at_least_as_good_abs_error']}, on log loss {pl['runs_at_least_as_good_log_loss']}"
        )


if __name__ == "__main__":
    main()
