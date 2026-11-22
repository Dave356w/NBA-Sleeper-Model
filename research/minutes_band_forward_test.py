"""Forward test of the team minutes-band correction as a shadow score (owner request 22 November 2026).

Research only: it reads frozen forecasts, writes no ledger record and never touches a forecast,
selection, grade or the public record. The full-season replay (``minutes_band_full_season_replay``)
did not meet its adoption rule, but the correction improved strongly in the latest week. The owner
asked for a forward test on games it was never fitted on before deciding anything.

**Pre-registered on 22 November 2026, before any game in the test window tipped.**

- Window: native forecast games tipping after ``FREEZE`` (2026-11-22 12:00 UTC). Each game uses its
  earliest first-stage forecast frozen under the methodology in force when the script runs (the
  record the public season record uses for live games), graded on the latest ledger result.
- Shadow score: the ``team_raw`` correction of ``minutes_band_bias_replay`` (bands 0–15, 15–24,
  24–30, 30+; each team's walk-forward mean minutes error in the band, no shrinkage, the league value
  where the team has no history; team rescaled to Sleeper's projected minutes; points at each
  player's projected rate; 240-minute cap). Its point change on each team is added to the frozen
  adaptive score, which keeps the frozen team terms, and the win probability uses the forecast's own
  frozen scale. Only games finished before a forecast's capture (tip + 4 hours,
  ``minutes_common.available``) enter its history. Forecasts of retired (rescheduled, postponed or
  cancelled) instances are skipped, as in the public record, so a replacement instance counts.
- The decision cohort is fixed: the first ``MIN_GAMES`` (100) test games in tip order (then game
  ID). It is judged once all 100 are graded, and only on those 100. Recommend a version update only
  if log loss improves with its 95% slate-bootstrap interval entirely below 0 **and** margin MAE is
  not worse. Anything else: no change. Earlier runs are interim and decide nothing; games after the
  100th are reported separately and never change the decision.

Amended before the window opened, after an automated review of the first draft: history admission
moved from tip time to tip + 4 hours, the cohort was fixed at the first 100 games, and retired
instances were excluded.

History rows: every graded game before the window or test game, from the frozen native snapshots in
this ledger (latest pregame snapshot per game) and, for games without one, nba-matchups' first
snapshots (``--sleeper-csv``), matched to the ESPN box scores in ``--box`` (refresh it with
``research/build_espn_box.py`` up to the evaluation date).

    python research/build_espn_box.py /tmp/box.csv --start 2026-10-19 --end <yesterday>
    python research/minutes_band_forward_test.py --root . --box /tmp/box.csv \\
        --sleeper-csv ../nba-matchups/data/nba_sleeper.csv
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from minutes_common import available, band, corrected_points, csv_snapshots, load_box, snapshot_players
from scipy.special import ndtr

from sleeper_nba.common import dt, load_config
from sleeper_nba.matchup import minutes_scale
from sleeper_nba.storage import Ledger

FREEZE = "2026-11-22T12:00:00+00:00"
MIN_GAMES = 100
BOOTSTRAP = 10_000
OUTPUT = "research/output/minutes_band_forward_test_2026-27.json"


def log_loss(p, won):
    p = min(max(p, 1e-12), 1 - 1e-12)
    return -math.log(p if won else 1 - p)


def latest_results(records):
    out = {}
    for r in sorted((r for r in records if r["kind"] == "result"), key=lambda r: dt(r["verified_at"])):
        out[r["instance_id"]] = r
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--box", required=True)
    parser.add_argument("--sleeper-csv", required=True)
    parser.add_argument("--freeze", default=FREEZE, help="Only for checking the script on past games.")
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    settings = config["matchup"]
    freeze = dt(args.freeze)
    records = Ledger(root).records()
    box = load_box(args.box)
    archived = csv_snapshots(args.sleeper_csv)
    results = latest_results(records)

    pregame = sorted(
        (
            f
            for f in records
            if f["kind"] == "forecast"
            and f.get("provenance") == "native"
            and "players" in f.get("raw", {})
            and dt(f["captured_at"]) < dt(f["game"]["tip_utc"])
        ),
        key=lambda f: (dt(f["captured_at"]), f["id"]),
    )

    # History: one snapshot per game with a box score (latest native, else the archived first).
    history, seen = [], set()
    latest = {}
    for f in pregame:
        latest[f["game"]["game_id"]] = f
    for g, f in latest.items():
        seen.add(g)
        for side in ("home", "away"):
            code = f["game"][side]
            if (g, code) in box:
                for p in snapshot_players(f, code):
                    if p["minutes"] > 0:
                        actual = box[(g, code)].get(p["name"], (0.0, 0.0))[0]
                        history.append(
                            (code, dt(f["game"]["tip_utc"]), band(p["minutes"]), actual - p["minutes"])
                        )
    tips = {}
    with open(args.box, newline="") as handle:
        import csv

        for r in csv.DictReader(handle):
            tips[r["game_id"]] = dt(r["tip_utc"])
    for (g, _snapshot), rows in sorted(archived.items(), key=lambda kv: kv[0][1]):
        if g in seen or g not in tips:
            continue
        seen.add(g)
        for p in rows:
            if p["minutes"] > 0 and (g, p["team"]) in box:
                actual = box[(g, p["team"])].get(p["name"], (0.0, 0.0))[0]
                history.append((p["team"], tips[g], band(p["minutes"]), actual - p["minutes"]))

    # Test games: earliest first-stage forecast of the methodology in force, tipped after the
    # freeze, on an instance that was never retired.
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    test = {}
    for f in pregame:
        if (
            f["stage"] == "first"
            and f["config"].get("methodology") == config["methodology"]
            and dt(f["game"]["tip_utc"]) > freeze
            and f["instance_id"] not in retired
            and f["game"]["game_id"] not in test
        ):
            test[f["game"]["game_id"]] = f
    ordered = sorted(test.items(), key=lambda kv: (dt(kv[1]["game"]["tip_utc"]), kv[0]))
    cohort = {g for g, _ in ordered[:MIN_GAMES]}
    rows = []
    for g, f in ordered:
        result = results.get(f["instance_id"])
        if result is None:
            continue
        adaptive = f["variants"]["adaptive"]
        past = [x for x in history if available(x[1]) <= dt(f["captured_at"])]
        delta = {}
        for side in ("home", "away"):
            code = f["game"][side]
            players = snapshot_players(f, code)
            if abs(sum(p["points"] for p in players) - f["raw"][f"{side}_score"]) > 0.05:
                raise ValueError(f"{g} {side}: player rows do not reproduce the frozen raw score")
            capped = f["raw"][f"{side}_score"] * minutes_scale(
                f["raw"].get("team_minutes", {}).get(code), settings
            )
            delta[side] = corrected_points(players, code, past, 0, settings) - capped
        margin = adaptive["scoring"]["margin"]
        total = adaptive["scoring"]["total"]
        shadow_margin = margin + delta["home"] - delta["away"]
        shadow_total = total + delta["home"] + delta["away"]
        actual = result["home_score"] - result["away_score"]
        won = actual > 0
        shadow_p = float(ndtr(shadow_margin / adaptive["params"]["wp_a"]))
        rows.append(
            {
                "game_id": g,
                "slate": f["game"]["slate_date"],
                "decision_cohort": g in cohort,
                "model_margin": margin,
                "shadow_margin": shadow_margin,
                "actual_margin": actual,
                "model_abs_error": abs(margin - actual),
                "shadow_abs_error": abs(shadow_margin - actual),
                "model_total_abs_error": abs(total - result["home_score"] - result["away_score"]),
                "shadow_total_abs_error": abs(shadow_total - result["home_score"] - result["away_score"]),
                "model_correct": float((margin > 0) == won),
                "shadow_correct": float((shadow_margin > 0) == won),
                "model_log_loss": log_loss(adaptive["wp_home"], won),
                "shadow_log_loss": log_loss(shadow_p, won),
            }
        )

    report = {
        "question": __doc__.splitlines()[0],
        "methodology": config["methodology"],
        "freeze": args.freeze,
        "pre_registered_freeze": FREEZE,
        "box_sha256": hashlib.sha256(Path(args.box).read_bytes()).hexdigest(),
        "sleeper_csv_sha256": hashlib.sha256(Path(args.sleeper_csv).read_bytes()).hexdigest(),
        "games": len(rows),
        "minimum_games": MIN_GAMES,
        "rows": rows,
    }

    def summarize(chosen):
        slates = sorted({r["slate"] for r in chosen})
        by_slate = {s: [r for r in chosen if r["slate"] == s] for s in slates}
        rng = np.random.default_rng(20261122)
        draws = [rng.choice(len(slates), len(slates)) for _ in range(BOOTSTRAP)]
        summary = {"games": len(chosen), "slates": len(slates)}
        for metric in ("abs_error", "total_abs_error", "correct", "log_loss"):
            diff = {s: [r[f"shadow_{metric}"] - r[f"model_{metric}"] for r in by_slate[s]] for s in slates}
            boot = [float(np.mean([v for i in idx for v in diff[slates[i]]])) for idx in draws]
            summary[metric] = {
                "model": float(np.mean([r[f"model_{metric}"] for r in chosen])),
                "shadow": float(np.mean([r[f"shadow_{metric}"] for r in chosen])),
                "difference": float(np.mean([r[f"shadow_{metric}"] - r[f"model_{metric}"] for r in chosen])),
                "ci95_slate_bootstrap": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            }
        return summary

    in_cohort = [r for r in rows if r["decision_cohort"]]
    after = [r for r in rows if not r["decision_cohort"]]
    if in_cohort:
        summary = summarize(in_cohort)
        report["summary"] = summary
        if after:
            report["after_endpoint"] = summarize(after)
        decided = len(cohort) == MIN_GAMES and len(in_cohort) == MIN_GAMES and args.freeze == FREEZE
        passes = (
            summary["log_loss"]["ci95_slate_bootstrap"][1] < 0 and summary["abs_error"]["difference"] <= 0
        )
        report["decision"] = (
            ("recommend a version update" if passes else "no change")
            if decided
            else "interim: decides nothing"
        )
    else:
        report["decision"] = "no graded test games yet"

    if args.freeze == FREEZE:
        out = root / OUTPUT
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"history {len(history)} player rows; decision cohort graded {len(in_cohort)} of {MIN_GAMES}; "
        f"after the endpoint {len(after)}"
    )
    for metric, x in report.get("summary", {}).items():
        if metric in ("games", "slates"):
            print(f"{metric} {x}")
            continue
        lo, hi = x["ci95_slate_bootstrap"]
        print(
            f"  {metric:15s} model {x['model']:.4f} shadow {x['shadow']:.4f} "
            f"diff {x['difference']:+.4f} [{lo:+.4f}, {hi:+.4f}]"
        )
    print("decision:", report["decision"])


if __name__ == "__main__":
    main()
