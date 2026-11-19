"""Team-score accuracy of raw Sleeper projections by snapshot lead time (v1.26 evidence).

Retrospective research (owner request, 19 November 2026); it writes no ledger
record. Team scores are the sums of raw Sleeper projected points, scored against
the pinned ESPN box-score extract. Snapshots: this ledger's native first
snapshots (earliest valid capture, typically about 40 hours before tip) and T-5h
locks, and nba-matchups' `data/nba_sleeper.csv` "first" (about 18 hours) and
"pre" (about 1 hour) snapshots, read from `--sleeper-csv` and recorded by SHA-256.

    python research/snapshot_timing.py --root . --sleeper-csv ../nba-matchups/data/nba_sleeper.csv
"""

from __future__ import annotations

import argparse
import collections
import csv
import importlib.util
import json
from pathlib import Path

import numpy as np

from sleeper_nba.common import dt, team
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/snapshot_timing_2026-27.json"
HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("ppe", HERE / "player_projection_error.py")
ppe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ppe)


def snapshots(records, sleeper_csv):
    out = {}
    by_kind = collections.defaultdict(lambda: collections.defaultdict(list))
    with Path(sleeper_csv).open() as f:
        for r in csv.DictReader(f):
            if r["minutes"] and float(r["minutes"]) > 0:
                by_kind[(r["game_id"], r["kind"])][r["snapshot_utc"]].append(r)
    for (g, kind), snaps in by_kind.items():
        at = min(snaps) if kind == "first" else max(snaps)
        rows = snaps[at]
        out[(g, "matchups_" + kind)] = {
            "lead": (dt(rows[0]["tip_utc"]) - dt(at)).total_seconds() / 3600,
            "rows": [(team(r["team"]), ppe.norm(r["name"]), float(r["pts"])) for r in rows],
        }
    for f in records:
        if f["kind"] != "forecast" or f.get("provenance") != "native":
            continue
        key = (f["game"]["game_id"], "ledger_" + f["stage"])
        earlier = key in out and out[key]["at"] <= f["captured_at"]
        later = key in out and out[key]["at"] >= f["captured_at"]
        if (f["stage"] == "first" and earlier) or (f["stage"] == "lock" and later):
            continue
        rows = [
            (team(p["team"]), ppe.norm(p["name"]), float(p["points"]))
            for p in f["raw"]["players"]
            if p.get("projection_status") == "projected" and (p.get("minutes") or 0) > 0
        ]
        if rows:
            out[key] = {"lead": f["lead_hours"], "rows": rows, "at": f["captured_at"]}
    return out


def errors(snaps, key, actual, team_games):
    g, _ = key
    sides = sorted({r[0] for r in snaps[key]["rows"]})
    if len(sides) != 2 or any((g, t) not in team_games for t in sides):
        return None
    proj = {t: sum(r[2] for r in snaps[key]["rows"] if r[0] == t) for t in sides}
    home, away = sides
    margin = (actual[(g, home)] - actual[(g, away)]) - (proj[home] - proj[away])
    total = sum(actual[(g, t)] for t in sides) - sum(proj.values())
    return margin, total


def compare(snaps, kinds, actual, team_games, rng):
    games = sorted(
        {
            g
            for g, _ in snaps
            if all((g, k) in snaps and errors(snaps, (g, k), actual, team_games) for k in kinds)
        }
    )
    res, summary = {}, {"games": len(games), "snapshots": {}}
    for k in kinds:
        e = np.array([errors(snaps, (g, k), actual, team_games) for g in games])
        res[k] = e
        summary["snapshots"][k] = {
            "median_lead_hours": float(np.median([snaps[(g, k)]["lead"] for g in games])),
            "margin_mae": float(abs(e[:, 0]).mean()),
            "total_mae": float(abs(e[:, 1]).mean()),
            "total_bias": float(e[:, 1].mean()),
        }
    base = kinds[0]
    summary["versus_" + base] = {}
    for k in kinds[1:]:
        item = {}
        for i, name in ((0, "margin_mae"), (1, "total_mae")):
            d = abs(res[k][:, i]) - abs(res[base][:, i])
            boots = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(4000)]
            item[name] = {"mean": float(d.mean()), "ci95": np.percentile(boots, [2.5, 97.5]).tolist()}
        summary["versus_" + base][k] = item
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--sleeper-csv", type=Path, required=True, help="nba-matchups data/nba_sleeper.csv")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    rng = np.random.default_rng(11)
    box, team_games = ppe.load_box(root)
    actual = collections.defaultdict(float)
    for (g, t, _), (_, points) in box.items():
        actual[(g, t)] += points
    records = Ledger(root).records()
    snaps = snapshots(records, args.sleeper_csv)
    report = {
        "generated_for": "v1.26 game-day first snapshot (owner request, 19 November 2026); retrospective",
        "inputs": {
            "box_file": ppe.BOX_FILE,
            "box_sha256": ppe.sha256(root / ppe.BOX_FILE),
            "sleeper_csv_sha256": ppe.sha256(args.sleeper_csv),
            "ledger_records": len(records),
        },
        "matchups_first_vs_pre": compare(snaps, ["matchups_first", "matchups_pre"], actual, team_games, rng),
        "ledger_first_vs_game_day": compare(
            snaps, ["ledger_first", "matchups_first", "ledger_lock", "matchups_pre"], actual, team_games, rng
        ),
        "note": "Team sums of raw Sleeper points only; no defense, offense or calibration terms.",
    }
    path = root / OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: v["snapshots"] for k, v in report.items() if isinstance(v, dict) and "snapshots" in v},
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
