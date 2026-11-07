from __future__ import annotations

import math
from datetime import timedelta

from .common import canonical, digest, dt, team


class InvalidProjection(ValueError):
    pass


def aggregate(game, raw, captured_at, config):
    if dt(captured_at) >= dt(game["tip_utc"]) or game["status"] != "pre":
        raise InvalidProjection("at_or_after_tip_or_started")
    selected = []
    for row in raw:
        if not isinstance(row, dict) or str(row.get("date", ""))[:10] != game["slate_date"]:
            continue
        try:
            t, opponent = team(row.get("team")), team(row.get("opponent"))
        except ValueError:
            continue
        if {t, opponent} == {game["home"], game["away"]}:
            selected.append(row)
    if not selected:
        raise InvalidProjection("missing_game_projection")
    companies = {r.get("company") for r in selected}
    if config["sleeper_company"] not in companies:
        raise InvalidProjection("configured_projection_company_unavailable")
    selected = [r for r in selected if r.get("company") == config["sleeper_company"]]
    ids = {str(r.get("game_id") or "") for r in selected}
    if len(ids) != 1 or "" in ids:
        raise InvalidProjection("ambiguous_sleeper_game_id")
    seen, normalized, duplicates = {}, [], 0
    scores = {game["home"]: 0.0, game["away"]: 0.0}
    minutes = dict.fromkeys(scores, 0.0)
    projected_counts = dict.fromkeys(scores, 0)
    for row in selected:
        try:
            row_hash = digest(row)
        except (ValueError, TypeError) as exc:
            raise InvalidProjection("nonfinite_or_invalid_source_row") from exc
        t = team(row["team"])
        pid = str(row.get("player_id") or "")
        if not pid:
            raise InvalidProjection("missing_player_id")
        key = (t, pid)
        if key in seen:
            if canonical(seen[key]) != canonical(row):
                raise InvalidProjection("conflicting_duplicate_player")
            duplicates += 1
            continue
        if any(k[1] == pid for k in seen):
            raise InvalidProjection("player_on_both_teams")
        seen[key] = row
        stats = row.get("stats")
        player = row.get("player") or {}
        base = {
            "player_id": pid,
            "name": player.get("full_name")
            or f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
            "team": t,
            "opponent": team(row["opponent"]),
            "game_id": game["game_id"],
            "sleeper_game_id": str(row["game_id"]),
            "captured_at": captured_at,
            "injury_status": player.get("injury_status"),
            "source_hash": row_hash,
            "provider_updated_at": row.get("last_modified"),
        }
        # An explicitly empty stat object is an unprojected roster placeholder,
        # not an assumed zero. Partially populated stats must validate strictly.
        if stats == {}:
            normalized.append({**base, "projection_status": "unprojected", "points": None, "minutes": None})
            continue
        if not isinstance(stats, dict) or "pts" not in stats or "sp" not in stats:
            raise InvalidProjection("missing_required_projection_fields")
        values = [stats["pts"], stats["sp"]]
        if any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0
            for x in values
        ):
            raise InvalidProjection("invalid_points_or_minutes")
        points, seconds = values
        if seconds == 0 and points != 0:
            raise InvalidProjection("zero_minutes_positive_points")
        normalized.append(
            {**base, "projection_status": "projected", "points": points, "minutes": seconds / 60}
        )
        scores[t] += points
        minutes[t] += seconds / 60
        projected_counts[t] += 1
    lower, upper = config["team_minutes_bounds"]
    if not all(lower <= m <= upper for m in minutes.values()) or not all(projected_counts.values()):
        raise InvalidProjection("incomplete_team_minutes")
    h, a = scores[game["home"]], scores[game["away"]]
    return {
        "home_score": h,
        "away_score": a,
        "margin": h - a,
        "total": h + a,
        "team_minutes": minutes,
        "players": normalized,
        "raw_rows": list(seen.values()),
        "exact_duplicates_collapsed": duplicates,
        "source_hash": digest(list(seen.values())),
    }


def eligible_stages(game, captured_at, existing, config):
    now, tip = dt(captured_at), dt(game["tip_utc"])
    if now >= tip or game["status"] != "pre":
        return []
    stages = []
    if "first" not in existing:
        stages.append("first")
    target = tip - timedelta(hours=config["lock_hours"])
    if "lock" not in existing and target <= now <= target + timedelta(
        minutes=config["lock_tolerance_minutes"]
    ):
        stages.append("lock")
    return stages
