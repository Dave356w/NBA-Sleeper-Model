from __future__ import annotations

import copy
import csv
import io
import json
import shutil
from pathlib import Path

import pytest
from test_pipeline import AT, FixtureHTTP

from sleeper_nba.common import dt
from sleeper_nba.pipeline import capture, grade
from sleeper_nba.site import PUBLIC_METHOD, build_site
from sleeper_nba.storage import Ledger

BASE = Path(__file__).parents[1]


def test_public_cohort_excludes_older_and_unversioned_forecasts(root, rows, config):
    http = FixtureHTTP(rows)
    capture(root, config, http=http, now=dt(AT))
    http.final = True
    http.at = "2026-11-08T02:00:00+00:00"
    grade(root, config, http=http)
    ledger = Ledger(root)
    current_ids = {f["id"] for f in ledger.records("forecast")}
    copies = []
    for cohort in ["older", "unversioned"]:
        for original in ledger.records("forecast"):
            f = copy.deepcopy(original)
            f["id"] = cohort + ":" + original["id"]
            if cohort == "older":
                f["methodology"] = f["config"]["methodology"] = "sleeper_only_v1_19"
            else:
                f.pop("methodology")
                f["config"].pop("methodology")
            copies.append(f)
        for original in ledger.records("grade"):
            g = copy.deepcopy(original)
            g["id"] = cohort + ":" + original["id"]
            g["forecast_id"] = cohort + ":" + original["forecast_id"]
            for variant in g["variants"].values():
                variant["winner_correct"] = False
                for selection in variant["selections"]:
                    selection["units"] = -100
            copies.append(g)
    ledger.append(copies)
    before = ledger.records()

    assert build_site(root)["forecasts"] == 2
    data = json.loads((root / "site" / "data.json").read_text())
    assert data["methodology"] == PUBLIC_METHOD
    assert {f["id"] for f in data["forecasts"]} == current_ids
    assert set(data["grades"]) == current_ids
    assert all(row["forecast_id"] in current_ids for row in data["clv"])
    assert data["performance"]["forecast"]["first:adaptive"]["n"] == 1
    assert data["performance"]["forecast"]["first:adaptive"]["accuracy"] == 1
    assert "performance_by_methodology" not in data and "methodologies" not in data
    assert data["historical_replay"] is None
    public = list(csv.DictReader(io.StringIO((root / "site" / "predictions.csv").read_text())))
    internal = list(csv.DictReader(io.StringIO((root / "data" / "predictions.csv").read_text())))
    assert len(public) == 4 and {r["methodology"] for r in public} == {PUBLIC_METHOD}
    assert {r["forecast_id"] for r in public} == current_ids
    assert len(internal) == 12
    internal_metrics = json.loads((root / "research" / "output" / "performance.json").read_text())
    assert internal_metrics["forecast"]["first:adaptive"]["n"] == 3
    assert ledger.records() == before


def copy_replay_archive(root):
    target = root / "research" / "output" / "defense_selection_2025-26.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BASE / "research" / "output" / target.name, target)
    return target


def test_historical_summary_does_not_enter_live_metrics_or_download(root):
    copy_replay_archive(root)
    before = Ledger(root).records()
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    replay = data["historical_replay"]
    assert replay["methodology"] == PUBLIC_METHOD
    assert replay["stage"] == "first" and replay["provenance"] == "legacy-first"
    assert replay["start_date"] == "2026-10-22" and replay["end_date"] == "2026-11-06"
    assert replay["metrics"]["n"] == 117
    assert replay["metrics"]["correct"] == 86 and replay["metrics"]["incorrect"] == 31
    assert replay["metrics"]["log_loss"] == pytest.approx(0.584843581869992)
    assert "assumed" in replay["outcome_availability"]
    assert data["forecasts"] == [] and data["grades"] == {}
    assert data["performance"] == {"forecast": {}, "betting": {}, "paired": {}}
    assert not list(csv.DictReader(io.StringIO((root / "site" / "predictions.csv").read_text())))
    assert "raw_sleeper" not in json.dumps(replay) and "registered_adaptive" not in json.dumps(replay)
    assert Ledger(root).records() == before


def test_replay_count_mismatch_cannot_be_published(root):
    path = copy_replay_archive(root)
    archive = json.loads(path.read_text())
    archive["current_season_check"][archive["selected_iteration"]]["n"] -= 1
    path.write_text(json.dumps(archive))
    with pytest.raises(ValueError, match="sample counts differ"):
        build_site(root)


def test_no_public_fallback_when_only_older_forecasts_exist(root, rows, config):
    config = {**config, "methodology": "sleeper_only_v1_19", "matchup": {"enabled": False}}
    capture(root, config, http=FixtureHTTP(rows), now=dt(AT))
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    assert data["methodology"] == PUBLIC_METHOD
    assert data["forecasts"] == [] and data["performance"]["forecast"] == {}
