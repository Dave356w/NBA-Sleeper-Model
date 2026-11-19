"""Build the hash-pinned team minutes for the seed and backfill snapshots (v1.32, owner update 19 November 2026).

python research/build_seed_minutes.py /path/to/nba-matchups [--out data/calibration_seeds/...]

v1.32 scales a team whose Sleeper projected minutes exceed 240 down to 240 before scoring
(`matchup.team_minutes_cap`). The legacy seed and the outage backfill keep only team totals, so their
team minutes come from the same nba-matchups snapshots they were built from (`data/nba_sleeper.csv`,
kind `first`): for each seed and backfill observation, the projected minutes of every player of each
team at that exact snapshot. Every team's summed player points must reproduce the pinned projected
score, or nothing is written. The output records the source commit and file SHA-256 and the seed and
backfill digests it covers.

Nothing here writes a forecast, selection, grade or ledger record.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sleeper_nba.common import digest, dt, iso, load_config, team  # noqa: E402

OUT = "data/calibration_seeds/seed_team_minutes_2026-27.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("nba_matchups")
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args(argv)
    source = Path(args.nba_matchups) / "data" / "nba_sleeper.csv"
    config = load_config(ROOT)
    files = [config["matchup"]["seed_file"]] + [
        e["file"] for e in config["matchup"].get("backfill_files", [])
    ]

    snapshots = collections.defaultdict(dict)
    with open(source, newline="") as handle:
        for r in csv.DictReader(handle):
            if r["kind"] == "first":
                snapshots[(r["game_id"], dt(r["snapshot_utc"]))][(team(r["team"]), r["sleeper_id"])] = r

    rows, covered = [], {}
    for name in files:
        seed = json.loads((ROOT / name).read_text())
        covered[name] = seed["sha256"]
        for o in seed["observations"]:
            players = snapshots.get((o["game_id"], dt(o["snapshot_utc"])))
            if not players:
                raise SystemExit(f"{o['game_id']}: no source snapshot at {o['snapshot_utc']}")
            minutes, points = collections.defaultdict(float), collections.defaultdict(float)
            for (t, _), r in players.items():
                minutes[t] += float(r["minutes"] or 0)
                points[t] += float(r["pts"] or 0)
            for side in ("home", "away"):
                if abs(points[o[side]] - o[f"projected_{side}_score"]) > 0.05:
                    raise SystemExit(f"{o['game_id']} {o[side]}: source points do not reproduce the seed")
            rows.append(
                {
                    "game_id": o["game_id"],
                    "snapshot_utc": o["snapshot_utc"],
                    "team_minutes": {o["home"]: minutes[o["home"]], o["away"]: minutes[o["away"]]},
                }
            )
    commit = subprocess.run(
        ["git", "-C", args.nba_matchups, "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    body = {
        "schema_version": 1,
        "provenance": "seed-team-minutes",
        "purpose": "Projected team minutes of the pinned seed and backfill snapshots, for the v1.32 "
        "240-minute cap (scale down only). Reporting and team-fit input only; never a forecast, "
        "selection, grade or ledger record.",
        "created_at": iso(datetime.now(UTC)),
        "source_repository": "Dave356w/nba-matchups",
        "source_commit": commit,
        "source_file": "data/nba_sleeper.csv",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "covers": covered,
        "rows": sorted(rows, key=lambda r: (r["snapshot_utc"], r["game_id"])),
    }
    out = ROOT / args.out
    out.write_text(json.dumps({**body, "sha256": digest(body)}, indent=2) + "\n")
    over = sum(m > 240 for r in rows for m in r["team_minutes"].values())
    print(json.dumps({"rows": len(rows), "teams_over_240": over, "sha256": digest(body), "out": str(out)}))


if __name__ == "__main__":
    main()
