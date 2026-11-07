from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sleeper_nba.common import load_config

BASE = Path(__file__).parents[1]


@pytest.fixture
def root(tmp_path):
    shutil.copyfile(BASE / "config.yaml", tmp_path / "config.yaml")
    shutil.copyfile(BASE / "MODEL.md", tmp_path / "MODEL.md")
    shutil.copytree(BASE / "web", tmp_path / "web")
    return tmp_path


@pytest.fixture
def config(root):
    return load_config(root)


@pytest.fixture
def game():
    return {
        "game_id": "fixture-1",
        "instance_id": "fixture-1:initial",
        "home": "BOS",
        "away": "NYK",
        "home_name": "Boston Celtics",
        "away_name": "New York Knicks",
        "tip_utc": "2026-11-07T22:00:00+00:00",
        "slate_date": "2026-11-07",
        "status": "pre",
        "status_name": "STATUS_SCHEDULED",
        "completed": False,
        "competition_class": "regular",
        "season": 2026,
        "source_captured_at": "2026-11-07T17:00:00+00:00",
        "schedule_revision": "r1",
    }


@pytest.fixture
def rows():
    return [
        {
            "date": "2026-11-07",
            "game_id": "sleeper-fixture",
            "player_id": f"{team}-{i}",
            "team": team,
            "opponent": "NYK" if team == "BOS" else "BOS",
            "company": "rotowire",
            "player": {"full_name": f"Fixture {team} {i}"},
            "stats": {"pts": pts, "sp": 1800},
        }
        for team, pts in [("BOS", 15), ("NYK", 13.75)]
        for i in range(8)
    ]
