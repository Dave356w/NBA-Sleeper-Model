"""Build the owner-authorized closing-price backfill for the season record (v1.27, 19 November 2026).

python research/build_closing_backfill.py [--cache DIR] [--out data/market_backfill/...]

Some games in the pooled season record have no price, because their frozen first
snapshot was captured before DraftKings odds were posted (about 40 hours before
tip under the earlier first-snapshot rule) or during the 14-17 November capture
outage. The owner asked for those games to be priced at the closing line. ESPN's
game summary (`pickcenter`) keeps DraftKings' open and close prices for finished
games; it is the same ESPN DraftKings feed the watcher reads live.

This writes a hash-pinned file of each finished season game's DraftKings close:
both moneylines, the home spread with both prices, and the total with both
prices, plus the open values for reference. A game is included only when ESPN's
home and away teams match this ledger's schedule. The season record uses a close
only where the game's own frozen snapshot has no qualifying price
(`season.price_basis`); nothing here writes a forecast, selection, grade or
ledger record, and closing prices never enter scoring or calibration.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sleeper_nba import matchup  # noqa: E402
from sleeper_nba.common import digest, iso, load_config, team  # noqa: E402
from sleeper_nba.storage import Ledger  # noqa: E402

SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
OUT = "data/market_backfill/espn_draftkings_close_2026-27.json"


def american(value):
    if value in (None, "", "EVEN", "even"):
        return 100.0 if str(value).lower() == "even" else None
    try:
        return float(str(value).replace("+", ""))
    except ValueError:
        return None


def line(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).lstrip("ouOU").replace("+", ""))
    except ValueError:
        return None


def summary(game_id, cache):
    path = cache / f"{game_id}.json" if cache else None
    if path is not None and path.exists():
        return json.loads(path.read_text())
    url = SUMMARY + "?" + urllib.parse.urlencode({"event": game_id})
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed public ESPN host
        data = json.loads(response.read())
    data = {"pickcenter": data.get("pickcenter", []), "header": data.get("header", {})}
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    time.sleep(0.15)
    return data


def side(market, team_side, when):
    return ((market or {}).get(team_side) or {}).get(when) or {}


def closing_row(game, data):
    competition = (data.get("header", {}).get("competitions") or [{}])[0]
    if not competition.get("status", {}).get("type", {}).get("completed"):
        return None, "not_final"
    teams = {c.get("homeAway"): team(c["team"]["abbreviation"]) for c in competition.get("competitors", [])}
    if teams.get("home") != game["home"] or teams.get("away") != game["away"]:
        return None, "team_mismatch"
    dk = [p for p in data.get("pickcenter", []) if p.get("provider", {}).get("name") == "DraftKings"]
    if not dk:
        return None, "no_draftkings"
    p = dk[0]
    row = {"game_id": game["game_id"], "home": game["home"], "away": game["away"], "tip_utc": game["tip_utc"]}
    row["book"] = "draftkings"
    for when in ("close", "open"):
        ml, sp, tot = p.get("moneyline"), p.get("pointSpread"), p.get("total")
        row[when] = {
            "moneyline": {
                "home": american(side(ml, "home", when).get("odds")),
                "away": american(side(ml, "away", when).get("odds")),
            },
            "spread": {
                "line": line(side(sp, "home", when).get("line")),
                "home": american(side(sp, "home", when).get("odds")),
                "away": american(side(sp, "away", when).get("odds")),
            },
            "total": {
                "line": line(side(tot, "over", when).get("line")),
                "over": american(side(tot, "over", when).get("odds")),
                "under": american(side(tot, "under", when).get("odds")),
            },
        }
    if row["close"]["moneyline"]["home"] is None or row["close"]["moneyline"]["away"] is None:
        return None, "no_close_moneyline"
    return row, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cache", type=Path, help="directory of cached ESPN summary responses")
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config = load_config(root)
    records = Ledger(root).records()
    season = max(matchup.season_of(r["game"]) for r in records if r["kind"] in ("forecast", "schedule"))
    games = {}
    for r in records:
        if r["kind"] == "schedule" and matchup.season_of(r["game"]) == season:
            games[r["game"]["game_id"]] = r["game"]
    seed = json.loads((root / config["matchup"]["seed_file"]).read_text())
    for o in seed["observations"]:
        games.setdefault(o["game_id"], {k: o[k] for k in ("game_id", "home", "away", "tip_utc")})
    rows, skipped = [], {}
    for game_id in sorted(games):
        game = games[game_id]
        if game["tip_utc"] >= iso(datetime.now(UTC)):
            continue
        row, reason = closing_row(game, summary(game_id, args.cache))
        if row:
            rows.append(row)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    body = {
        "description": "ESPN DraftKings closing prices for finished games; season-record price backfill only "
        "(owner update, 19 November 2026). Never a forecast, selection, grade or ledger record.",
        "created_at": iso(datetime.now(UTC)),
        "season": season,
        "source": SUMMARY + "?event=<game_id> (pickcenter, provider DraftKings, close and open)",
        "rows": rows,
        "skipped": skipped,
    }
    body["sha256"] = digest(body)
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"rows": len(rows), "skipped": skipped, "sha256": body["sha256"], "out": str(out)}))


if __name__ == "__main__":
    main()
