"""Shared helpers for the November 2026 minutes research (research only).

Used by ``out_minutes_adjustment_replay``, ``low_minutes_redistribution_replay``,
``minutes_band_bias_replay``, ``minutes_band_interaction_replay``,
``minutes_band_full_season_replay`` and ``minutes_band_forward_test``. Nothing here is imported by
the model package, writes to the ledger or changes a forecast.
"""

from __future__ import annotations

import collections
import csv
import re
import unicodedata

from sleeper_nba.common import dt, team
from sleeper_nba.matchup import minutes_scale

BANDS = ((0, 15), (15, 24), (24, 30), (30, 99))
K_LEAGUE = 50


def norm(name):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[.'\-]", " ", name)
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    return " ".join(name.split())


def load_box(path):
    """Box scores keyed by (game, team) -> normalized name -> (minutes, points)."""
    box = collections.defaultdict(dict)
    with open(path, newline="") as handle:
        for r in csv.DictReader(handle):
            box[(r["game_id"], team(r["team"]))][norm(r["name"])] = (float(r["minutes"]), float(r["points"]))
    return box


def load_box_history(path):
    """Box scores as team schedules, per-game minutes and each player's played games (Out study)."""
    games = {}  # (team, game_id) -> tip
    minutes = collections.defaultdict(dict)  # (team, game_id) -> name -> minutes
    by_player = collections.defaultdict(list)  # name -> [(tip, minutes)] games played
    with open(path, newline="") as handle:
        for r in csv.DictReader(handle):
            key = (team(r["team"]), r["game_id"])
            tip = dt(r["tip_utc"])
            games[key] = tip
            m = float(r["minutes"])
            minutes[key][norm(r["name"])] = m
            if m > 0:
                by_player[norm(r["name"])].append((tip, m))
    schedule = collections.defaultdict(list)
    for (t, g), tip in games.items():
        schedule[t].append((tip, g))
    for t in schedule:
        schedule[t].sort()
    for n in by_player:
        by_player[n].sort()
    return schedule, minutes, by_player


def snapshot_players(forecast, code):
    out = []
    for p in forecast["raw"]["players"]:
        if team(p["team"]) != code or p.get("minutes") is None:
            continue
        out.append(
            {
                "name": norm(p["name"]),
                "minutes": float(p["minutes"]),
                "points": float(p["points"] or 0),
                "status": (p.get("injury_status") or "").strip().lower(),
            }
        )
    return out


def csv_snapshots(path):
    out = collections.defaultdict(list)
    with open(path, newline="") as handle:
        for r in csv.DictReader(handle):
            if r["kind"] != "first":
                continue
            out[(r["game_id"], dt(r["snapshot_utc"]))].append(
                {
                    "team": team(r["team"]),
                    "name": norm(r["name"]),
                    "minutes": float(r["minutes"] or 0),
                    "points": float(r["pts"] or 0),
                    "status": (r["injury_status"] or "").strip().lower(),
                }
            )
    return out


def band(minutes):
    return next(
        i for i, (lo, hi) in enumerate(BANDS) if lo < minutes <= hi or (lo == 0 and 0 < minutes <= hi)
    )


def shrunk(values, k, centre):
    if k == 0 and not values:
        return centre  # no pull: a team with no history in the band uses the league value
    return (sum(values) + k * centre) / (len(values) + k)


def correction(history, code, b, k_team):
    league = shrunk([e for _, _, bb, e in history if bb == b], K_LEAGUE, 0.0)
    if k_team is None:
        return league
    return shrunk([e for t, _, bb, e in history if t == code and bb == b], k_team, league)


def corrected_points(players, code, history, k_team, settings):
    rows = [p for p in players if p["minutes"] > 0]
    fixed = sum(p["points"] for p in players if p["minutes"] <= 0)
    total = sum(p["minutes"] for p in rows)
    raw = [max(p["minutes"] + correction(history, code, band(p["minutes"]), k_team), 0.0) for p in rows]
    ratio = total / sum(raw) if sum(raw) > 0 else 1.0
    points = fixed + sum(p["points"] * (m * ratio) / p["minutes"] for p, m in zip(rows, raw))
    return points * minutes_scale(total, settings)
