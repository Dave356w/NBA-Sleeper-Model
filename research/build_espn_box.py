"""Fetch every completed regular-season ESPN summary box score into a CSV (research input).

One row per player listed in each game's box score; a player who did not play has 0 minutes.

    python research/build_espn_box.py research/inputs/espn_box_2026-27_2026-11-22.csv \\
        --start 2026-10-19 --end 2026-11-21
"""

from __future__ import annotations

import argparse
import csv
import datetime as d
import json
import re
import urllib.request

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=30).read())


def number(value):
    return float(value) if value and re.fullmatch(r"[0-9.]+", value) else 0.0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out")
    parser.add_argument("--start", default="2026-10-19")
    parser.add_argument("--end", default="2026-11-21")
    args = parser.parse_args(argv)
    day, end = d.date.fromisoformat(args.start), d.date.fromisoformat(args.end)
    with open(args.out, "w", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["game_id", "tip_utc", "team", "espn_player_id", "name", "minutes", "points", "dnp"])
        while day <= end:
            for e in get(SCOREBOARD.format(day.strftime("%Y%m%d")))["events"]:
                if e["season"]["type"] != 2 or not e["status"]["type"]["completed"]:
                    continue
                for side in get(SUMMARY.format(e["id"]))["boxscore"]["players"]:
                    block = side["statistics"][0]
                    for a in block["athletes"]:
                        stats = dict(zip(block["keys"], a.get("stats") or []))
                        w.writerow(
                            [
                                e["id"],
                                e["date"],
                                side["team"]["abbreviation"],
                                a["athlete"]["id"],
                                a["athlete"]["displayName"],
                                number(stats.get("minutes")),
                                number(stats.get("points")),
                                int(bool(a.get("didNotPlay"))),
                            ]
                        )
            day += d.timedelta(1)


if __name__ == "__main__":
    main()
