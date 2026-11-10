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


def lock_window_end(game, config):
    """Honor each frozen forecast's policy, including historical narrow windows."""
    tip = dt(game["tip_utc"])
    policy = config.get("lock_policy")
    if policy == "five_hours_to_tip_v1":
        return tip
    # Unlabeled historical forecasts and the v2 policy share the narrow window.
    if policy not in (None, "five_hours_15min_v2"):
        raise ValueError(f"Unknown lock policy: {policy!r}")
    return tip - timedelta(hours=config["lock_hours"]) + timedelta(minutes=config["lock_tolerance_minutes"])
