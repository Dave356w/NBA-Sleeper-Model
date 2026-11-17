"""Build the owner-authorized capture-outage backfill (owner update, 17 November 2026).

python research/build_gap_backfill.py /path/to/nba-matchups [--out data/calibration_seeds/...]

The watcher stopped capturing on 14 November 2026 (an aged-out test fixture
failed the pre-capture test run). The owner authorized backfilling the games
it missed from nba-matchups' archived early-day Sleeper snapshots. This builds
a hash-pinned seed in the same schema as the 117-game legacy-first seed:

* Games: every nba-matchups `first` snapshot this season whose game has no
  native first-stage forecast in this ledger, is not already in the legacy
  seed, is final on ESPN, and whose snapshot precedes tip. A team whose
  projected minutes sum below 200 is excluded, as in the legacy seed.
* Projected scores: the sum of that snapshot's projected player points per
  team, as in the legacy seed.
* Results: ESPN's final score, with the response's SHA-256.
* Quotes: nba-matchups' archived first moneyline / spread / total and their
  timestamps (data/nba_ledger.csv); the replay pairs them under its own
  timing rule.

Nothing here writes a forecast, selection, grade or ledger record.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sleeper_nba.common import digest, dt, iso, load_config, team  # noqa: E402
from sleeper_nba.storage import Ledger  # noqa: E402

SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"
SEASON = 2026
MIN_TEAM_MINUTES = 200
QUOTE_FIELDS = (
    "first_snapshot_utc",
    "first_book",
    "first_home_ml",
    "first_away_ml",
    "first_spread",
    "first_ou_snapshot_utc",
    "first_ou_book",
    "first_ou_line",
)


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def final_score(game_id):
    raw = urllib.request.urlopen(SUMMARY.format(game_id), timeout=30).read()
    body = json.loads(raw)
    comp = body["header"]["competitions"][0]
    if not comp["status"]["type"].get("completed"):
        return None
    scores = {c["homeAway"]: float(c["score"]) for c in comp["competitors"]}
    return scores["home"], scores["away"], hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("matchups", type=Path)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "data/calibration_seeds/gap_backfill_2026-11-17.json"
    )
    args = parser.parse_args()
    config = load_config(ROOT)
    records = Ledger(ROOT).records()
    native = {
        f["game"]["game_id"]
        for f in records
        if f["kind"] == "forecast" and f["stage"] == "first" and f.get("provenance") == "native"
    }
    legacy = json.loads((ROOT / config["matchup"]["seed_file"]).read_text())
    seeded = {o["game_id"] for o in legacy["observations"]}
    excluded_legacy = {legacy.get("excluded_game", "").split(":")[0]}

    snapshots = args.matchups / "data/nba_sleeper.csv"
    ledger_csv = args.matchups / "data/nba_ledger.csv"
    games = {}
    for row in csv.DictReader(snapshots.open()):
        if row["kind"] == "first":
            games.setdefault(row["game_id"], []).append(row)
    quotes = {row["game_id"]: row for row in csv.DictReader(ledger_csv.open())}

    observations, excluded = [], []
    for game_id, rows in sorted(games.items(), key=lambda kv: (kv[1][0]["tip_utc"], kv[0])):
        if game_id in native or game_id in seeded or game_id in excluded_legacy:
            continue
        snapshot, tip = rows[0]["snapshot_utc"], rows[0]["tip_utc"]
        if not dt(snapshot) < dt(tip):
            excluded.append(f"{game_id}: snapshot not before tip")
            continue
        quote = quotes.get(game_id)
        if quote is None:
            excluded.append(f"{game_id}: not in nba-matchups ledger")
            continue
        home, away = team(quote["home"]), team(quote["away"])
        points, minutes = {home: 0.0, away: 0.0}, {home: 0.0, away: 0.0}
        for r in rows:
            t = team(r["team"])
            if t not in points:
                raise ValueError(f"{game_id}: unexpected team {t}")
            points[t] += float(r["pts"] or 0)
            minutes[t] += float(r["minutes"] or 0)
        short = [t for t in (home, away) if minutes[t] < MIN_TEAM_MINUTES]
        if short:
            excluded.append(f"{game_id}: {short[0]} minutes {minutes[short[0]]:g} < {MIN_TEAM_MINUTES}")
            continue
        result = final_score(game_id)
        if result is None:
            continue  # Not final yet; a later build can add it.
        verified_at = iso(datetime.now(UTC))
        observations.append(
            {
                "observation_id": f"gap-backfill-first:{game_id}",
                "game_id": game_id,
                "stage": "first",
                "season": SEASON,
                "home": home,
                "away": away,
                "snapshot_utc": snapshot,
                "tip_utc": iso(dt(tip)),
                "verified_at": verified_at,
                "projected_home_score": points[home],
                "projected_away_score": points[away],
                "actual_home_score": result[0],
                "actual_away_score": result[1],
                "provenance": "gap-backfill-first",
                "result_source": "ESPN",
                "result_source_sha256": result[2],
                "quotes": {k: quote.get(k, "") for k in QUOTE_FIELDS},
            }
        )
    commit = subprocess.run(
        ["git", "-C", str(args.matchups), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    body = {
        "schema_version": 1,
        "provenance": "gap-backfill-first",
        "purpose": (
            "Owner-authorized (17 November 2026) backfill of games missed during the 14-17 November 2026 "
            "capture outage, from nba-matchups' archived early-day Sleeper snapshots. Feeds the first-stage "
            "team fit, the season replay and the replay calibration history; never a forecast, selection or grade."
        ),
        "created_at": iso(datetime.now(UTC)),
        "source_repository": "Dave356w/nba-matchups",
        "source_commit": commit,
        "source_snapshot_sha256": file_sha(snapshots),
        "source_ledger_sha256": file_sha(ledger_csv),
        "excluded": excluded,
        "observations": observations,
    }
    payload = {**body, "sha256": digest(body)}
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"observations": len(observations), "excluded": excluded, "sha256": payload["sha256"]}))


if __name__ == "__main__":
    main()
