"""Sleeper player projection error against ESPN box scores, and whether player trends matter for teams.

Retrospective v1.25 research (owner request, 19 November 2026). It writes no
ledger record and changes no forecast, selection or grade.

Projections are each game's earliest native first snapshot in this ledger and,
optionally, for earlier games, the first snapshots in nba-matchups'
`data/nba_sleeper.csv` (the source of the legacy seed; pass `--sleeper-csv`, its
SHA-256 is recorded). Actual minutes and points come from the pinned ESPN
box-score extract in `research/inputs`. Every team code from either source goes
through `common.team`, so Basketball-Reference codes (BRK, CHO, PHO) and ESPN
codes (GS, NY, ...) resolve to the model's codes. A first draft outside the
repository skipped that step and silently dropped Brooklyn, Charlotte and
Phoenix team-games from the nba-matchups snapshots.

Players are matched within a team-game on a normalized name: accents,
punctuation and Jr/Sr/II-IV suffixes removed, lower case, single spaces.
ESPN leaves inactive players out of the box score, so a projected player who is
absent is counted as not playing (0 minutes, 0 points).

    python research/player_projection_error.py --root . \
        --sleeper-csv ../nba-matchups/data/nba_sleeper.csv
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import numpy as np

from sleeper_nba.common import dt, team
from sleeper_nba.storage import Ledger

BOX_FILE = "research/inputs/espn_box_2026-27_2026-11-19.csv"
OUTPUT = "research/output/player_projection_error_2026-27.json"
FLAGGED = ("DTD", "GTD", "Out")
SHRINK_GAMES = 10
BOOT = 2000


def norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[.'\-]", " ", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    return " ".join(name.split())


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_box(root):
    box, team_games = {}, set()
    with (root / BOX_FILE).open() as f:
        for r in csv.DictReader(f):
            key = (r["game_id"], team(r["team"]))
            box[(*key, norm(r["name"]))] = (float(r["minutes"]), float(r["points"]))
            team_games.add(key)
    return box, team_games


def load_projections(records, sleeper_csv):
    proj, tips, source = {}, {}, {}
    forecasts = sorted(
        (
            r
            for r in records
            if r["kind"] == "forecast" and r["stage"] == "first" and r.get("provenance") == "native"
        ),
        key=lambda r: (dt(r["captured_at"]), r["id"]),
    )
    for f in forecasts:
        g = f["game"]["game_id"]
        rows = [
            p
            for p in f["raw"].get("players", [])
            if p.get("projection_status", "projected") == "projected" and (p.get("minutes") or 0) > 0
        ]
        if g in proj or not rows:
            continue
        proj[g] = [
            (
                team(p["team"]),
                norm(p["name"]),
                float(p["minutes"]),
                float(p["points"]),
                p.get("injury_status"),
            )
            for p in rows
        ]
        tips[g], source[g] = f["game"]["tip_utc"], "ledger"
    if sleeper_csv:
        by_game = collections.defaultdict(list)
        with Path(sleeper_csv).open() as f:
            for r in csv.DictReader(f):
                if (
                    r["kind"] == "first"
                    and r["game_id"] not in proj
                    and r["minutes"]
                    and float(r["minutes"]) > 0
                ):
                    by_game[r["game_id"]].append(r)
        for g, rows in by_game.items():
            first = min(r["snapshot_utc"] for r in rows)
            rows = [r for r in rows if r["snapshot_utc"] == first]
            proj[g] = [
                (
                    team(r["team"]),
                    norm(r["name"]),
                    float(r["minutes"]),
                    float(r["pts"]),
                    r["injury_status"] or None,
                )
                for r in rows
            ]
            tips[g], source[g] = rows[0]["tip_utc"], "nba-matchups"
    return proj, tips, source


def slope_ci(x, y, rng):
    b = float(np.polyfit(x, y, 1)[0])
    boots = [np.polyfit(x[i], y[i], 1)[0] for i in (rng.integers(0, len(x), len(x)) for _ in range(BOOT))]
    return {"slope": b, "ci95": np.percentile(boots, [2.5, 97.5]).tolist()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--sleeper-csv", type=Path, help="nba-matchups data/nba_sleeper.csv (optional)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    rng = np.random.default_rng(5)
    records = Ledger(root).records()
    box, team_games = load_box(root)
    proj, tips, source = load_projections(records, args.sleeper_csv)
    games = sorted(g for g in proj if any((g, t) in team_games for t, *_ in proj[g]))

    players, teams, unmatched = [], [], 0
    for g in games:
        for side in sorted({p[0] for p in proj[g]}):
            if (g, side) not in team_games:
                unmatched += 1
                continue
            mine = [p for p in proj[g] if p[0] == side]
            names = {p[1] for p in mine}
            rows = []
            for _, name, minutes, points, injury in mine:
                am, ap = box.get((g, side, name), (0.0, 0.0))
                rows.append(
                    {"key": (side, name), "tip": tips[g], "pm": minutes, "pp": points, "am": am, "ap": ap}
                    | {"injury": injury, "played": am > 0, "game": g, "team": side}
                )
            players += rows
            box_rows = [(k[2], v) for k, v in box.items() if k[0] == g and k[1] == side]
            teams.append(
                {
                    "game": g,
                    "team": side,
                    "tip": tips[g],
                    "projected": sum(r["pp"] for r in rows),
                    "actual": sum(v[1] for _, v in box_rows),
                    "played_error": sum(r["ap"] - r["pp"] for r in rows if r["played"]),
                    "dnp_lost": sum(r["pp"] for r in rows if not r["played"]),
                    "unprojected": sum(v[1] for n, v in box_rows if n not in names and v[0] > 0),
                    "flagged": sum(r["pp"] for r in rows if r["injury"] in FLAGGED),
                }
            )

    pp, ap = (np.array([r[k] for r in players]) for k in ("pp", "ap"))
    played = np.array([r["played"] for r in players])
    err = ap - pp
    tiers = []
    for lo, hi in [(0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 99)]:
        s = (pp >= lo) & (pp < hi)
        sp = s & played
        tiers.append(
            {
                "projected_points": [lo, hi],
                "n": int(s.sum()),
                "did_not_play": float((~played[s]).mean()),
                "bias_all": float(err[s].mean()),
                "bias_played": float(err[sp].mean()),
                "mae_played": float(abs(err[sp]).mean()),
            }
        )
    injury = {}
    for status in sorted({str(r["injury"]) for r in players}):
        s = np.array([str(r["injury"]) == status for r in players])
        if s.sum() >= 10:
            injury[status] = {
                "n": int(s.sum()),
                "did_not_play": float((~played[s]).mean()),
                "projected": float(pp[s].mean()),
                "actual": float(ap[s].mean()),
            }

    # Walk-forward player trends: each player-game uses only that player's earlier games.
    history = collections.defaultdict(list)
    for r in sorted(players, key=lambda r: (dt(r["tip"]), r["game"])):
        history[r["key"]].append(r)
    past, form, mins, nxt = [], [], [], []
    team_trend = collections.defaultdict(float)
    for rs in history.values():
        for i, r in enumerate(rs):
            prior = [x for x in rs[:i] if x["played"]]
            n = len(prior)
            if n:
                mean = float(np.mean([x["ap"] - x["pp"] for x in prior]))
                team_trend[(r["game"], r["team"])] += mean * n / (n + SHRINK_GAMES)
            if n >= 3 and r["played"]:
                past.append(np.mean([x["ap"] - x["pp"] for x in prior]))
                form.append(np.mean([x["ap"] for x in prior[-5:]]) - r["pp"])
                mins.append(np.mean([x["am"] for x in prior[-3:]]) - r["pm"])
                nxt.append(r["ap"] - r["pp"])
    past, form, mins, nxt = map(np.array, (past, form, mins, nxt))

    team_error = np.array([t["actual"] - t["projected"] for t in teams])
    flagged = np.array([t["flagged"] for t in teams])
    trend = np.array([team_trend[(t["game"], t["team"])] for t in teams])
    by_game = collections.defaultdict(dict)
    for t, tr in zip(teams, trend, strict=True):
        by_game[t["game"]][t["team"]] = (t["actual"] - t["projected"], tr)
    pairs = [list(v.values()) for v in by_game.values() if len(v) == 2]
    margin_error = np.array([a[0] - b[0] for a, b in pairs])
    margin_trend = np.array([a[1] - b[1] for a, b in pairs])

    report = {
        "generated_for": "v1.25 player projection review (owner request, 19 November 2026); "
        "retrospective, research only",
        "inputs": {
            "box_file": BOX_FILE,
            "box_sha256": sha256(root / BOX_FILE),
            "sleeper_csv_sha256": sha256(args.sleeper_csv) if args.sleeper_csv else None,
            "ledger_records": len(records),
        },
        "games": len(games),
        "games_by_source": dict(collections.Counter(source[g] for g in games)),
        "team_games": len(teams),
        "unmatched_team_games": unmatched,
        "player_games": len(players),
        "player_error": {
            "bias": float(err.mean()),
            "mae": float(abs(err).mean()),
            "correlation": float(np.corrcoef(pp, ap)[0, 1]),
            "did_not_play": float((~played).mean()),
            "by_projected_points": tiers,
            "by_injury_status": injury,
        },
        "team_error_sources": {
            "bias": float(team_error.mean()),
            "mae": float(abs(team_error).mean()),
            "played_error_mean": float(np.mean([t["played_error"] for t in teams])),
            "dnp_lost_mean": float(np.mean([t["dnp_lost"] for t in teams])),
            "unprojected_mean": float(np.mean([t["unprojected"] for t in teams])),
            "flagged_points_slope": slope_ci(flagged, team_error, rng),
        },
        "player_trends_walk_forward": {
            "n": len(nxt),
            "past_error": slope_ci(past, nxt, rng),
            "last5_points_minus_projection": slope_ci(form, nxt, rng),
            "last3_minutes_minus_projection": slope_ci(mins, nxt, rng),
            "mae": float(abs(nxt).mean()),
        },
        "team_level": {
            "summed_player_trend": slope_ci(trend, team_error, rng),
            "team_mae": float(abs(team_error).mean()),
            "team_mae_with_trend": float(abs(team_error - trend).mean()),
            "margin_games": len(pairs),
            "margin_mae": float(abs(margin_error).mean()),
            "margin_mae_with_trend": float(abs(margin_error - margin_trend).mean()),
        },
        "conclusion": "Player-level errors persist and Sleeper projects injury-flagged players at full value, "
        "but neither predicts team scores: teammates absorb the minutes and shots.",
    }
    path = root / OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({k: report[k] for k in ("games", "games_by_source", "team_games", "unmatched_team_games")})
    )


if __name__ == "__main__":
    main()
