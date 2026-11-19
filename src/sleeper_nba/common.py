from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ET = ZoneInfo("America/New_York")


def utcnow():
    return datetime.now(UTC)


def dt(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(UTC)


def iso(value=None):
    return (value or utcnow()).astimezone(UTC).isoformat(timespec="microseconds")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def load_config(root):
    return yaml.safe_load((Path(root) / "config.yaml").read_text())


ALIASES = {
    "CHO": "CHA",
    "BRK": "BKN",
    "PHO": "PHX",
    "GS": "GSW",
    "SA": "SAS",
    "NY": "NYK",
    "UTH": "UTA",
    "WSH": "WAS",
    "NO": "NOP",
    "UTAH": "UTA",
}
TEAMS = set(
    "ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS".split()
)


def team(value):
    value = str(value or "").strip().upper()
    result = ALIASES.get(value, value)
    if result not in TEAMS:
        raise ValueError(f"Unknown NBA team: {value!r}")
    return result


def first_window_open(game, config, at, priced=True):
    """Whether a first snapshot may be frozen at `at` under the forecast's frozen policy.

    Configurations without `first_policy` freeze the earliest valid pregame capture.
    `game_day_et_v1` (v1.26) waits for the game's Eastern calendar date.
    `game_day_priced_et_v1` (v1.27) also waits for a qualifying moneyline quote in
    the same capture (`priced`), but freezes unpriced from the lock target (T-5h)
    so that every game still gets a first snapshot.
    """
    policy = config.get("first_policy")
    if policy is None:
        return True
    game_day = dt(at).astimezone(ET).date().isoformat() >= game["slate_date"]
    if policy == "game_day_et_v1":
        return game_day
    if policy == "game_day_priced_et_v1":
        fallback = dt(at) >= dt(game["tip_utc"]) - timedelta(hours=config["lock_hours"])
        return game_day and (priced or fallback)
    raise ValueError(f"Unknown first policy: {policy!r}")


def first_is_priced(variants):
    """A frozen first snapshot is priced when its fixed variant has a qualifying moneyline."""
    return any(m["market"] == "moneyline" and m.get("eligible") for m in variants["fixed"].get("markets", []))


LOCK_POLICIES = (None, "five_hours_to_tip_v1", "five_hours_15min_v2", "five_hours_late_v3")


def lock_primary_end(game, config):
    """End of the on-time lock window: 15 minutes after T-5h (the whole pregame for v1)."""
    policy = config.get("lock_policy")
    if policy not in LOCK_POLICIES:
        raise ValueError(f"Unknown lock policy: {policy!r}")
    if policy == "five_hours_to_tip_v1":
        return dt(game["tip_utc"])
    return (
        dt(game["tip_utc"])
        - timedelta(hours=config["lock_hours"])
        + timedelta(minutes=config["lock_tolerance_minutes"])
    )


def lock_window_end(game, config):
    """Honor each frozen forecast's policy, including historical narrow windows.

    `five_hours_late_v3` (v1.29) keeps the on-time window and, when it passes with no
    valid capture, accepts a late lock until `lock_late_hours` before tip.
    """
    if config.get("lock_policy") == "five_hours_late_v3":
        return dt(game["tip_utc"]) - timedelta(hours=config["lock_late_hours"])
    return lock_primary_end(game, config)


def lock_timing(game, config, at):
    """`on_time` or `late` for a v1.29 lock captured at `at`; None for earlier policies."""
    if config.get("lock_policy") != "five_hours_late_v3":
        return None
    return "late" if dt(at) > lock_primary_end(game, config) else "on_time"
