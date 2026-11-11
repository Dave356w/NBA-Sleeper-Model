from __future__ import annotations

import copy
import json
import math
import struct
from datetime import timedelta
from html import unescape
from xml.etree import ElementTree

import pytest
from test_pipeline import AT, FixtureHTTP

from sleeper_nba.common import dt, iso
from sleeper_nba.pipeline import capture, grade
from sleeper_nba.reporting import live_market_comparisons
from sleeper_nba.season import favorite_benchmark
from sleeper_nba.site import build_site
from sleeper_nba.storage import Ledger


def graded_records(root, rows, config):
    http = FixtureHTTP(rows)
    capture(root, config, http=http, now=dt(AT))
    http.final = True
    http.at = "2026-11-08T02:00:00+00:00"
    grade(root, config, http=http)
    return Ledger(root).records()


def test_live_market_comparisons_follow_stage_variant_and_frozen_predictions(root, rows, config):
    records = graded_records(root, rows, config)
    for forecast in (r for r in records if r["kind"] == "forecast"):
        adaptive, fixed = forecast["variants"]["adaptive"], forecast["variants"]["fixed"]
        adaptive.update(wp_home=0.9, ats_margin=10, adjusted_total=220, winner="home")
        fixed.update(wp_home=0.4, ats_margin=-4, adjusted_total=200, winner="away")
        if forecast["stage"] == "lock":
            adaptive.update(wp_home=0.6, ats_margin=3, adjusted_total=210)
    before = copy.deepcopy(records)
    report = live_market_comparisons(records)
    first, lock, fixed = (report[k] for k in ["first:adaptive", "lock:adaptive", "first:fixed"])
    assert first["n"] == lock["n"] == fixed["n"] == 1
    assert first["versus_market"]["margin"]["model_mae"] == 0
    assert lock["versus_market"]["margin"]["model_mae"] == 7
    assert fixed["versus_market"]["margin"]["model_mae"] == 14
    assert first["versus_market"]["total"]["model_mae"] == 10
    assert fixed["versus_market"]["total"]["model_mae"] == 30
    assert first["versus_market"]["win_probability"]["model_log_loss"] == pytest.approx(-math.log(0.9))
    assert fixed["versus_market"]["win_probability"]["model_log_loss"] == pytest.approx(-math.log(0.4))
    # -200/+170 implies a fair home probability of 9/14.
    assert first["versus_market"]["win_probability"]["market_log_loss"] == pytest.approx(-math.log(9 / 14))
    assert first["favorite_benchmark"]["model"]["units"] == 0.5
    assert fixed["favorite_benchmark"]["model"]["units"] == -1
    assert fixed["favorite_benchmark"]["favorite"]["units"] == 0.5
    assert records == before


def test_comparisons_use_latest_grade_and_exclude_pending_void_and_retired(root, rows, config):
    records = graded_records(root, rows, config)
    pending = copy.deepcopy(next(f for f in records if f["kind"] == "forecast"))
    pending["id"] = "pending:forecast"
    records.append(pending)
    current = next(g for g in records if g["kind"] == "grade")
    correction = copy.deepcopy(current)
    correction.update(id="later:grade", at=iso(dt(current["at"]) + timedelta(days=1)))
    correction["result"]["home_score"] = 130
    records.append(correction)
    forecast = next(f for f in records if f["kind"] == "forecast" and f["id"] == current["forecast_id"])
    key = f"{forecast['stage']}:adaptive"
    assert live_market_comparisons(records)[key]["versus_market"]["margin"]["model_mae"] == 10
    correction["void"] = True
    assert live_market_comparisons(records)[key]["n"] == 0
    records.append({"kind": "retired", "instance_id": forecast["instance_id"]})
    assert all(m["n"] == 0 for m in live_market_comparisons(records).values())


def test_each_market_keeps_its_own_paired_quote_count(root, rows, config):
    records = graded_records(root, rows, config)
    for forecast in (r for r in records if r["kind"] == "forecast"):
        for output in forecast["variants"].values():
            for market in output["markets"]:
                if market["market"] == "moneyline":
                    market["eligible"] = False
    first = live_market_comparisons(records)["first:adaptive"]
    assert first["n"] == 1
    assert first["versus_market"]["win_probability"]["n"] == 0
    assert first["versus_market"]["margin"]["n"] == first["versus_market"]["total"]["n"] == 1
    assert first["favorite_benchmark"]["n"] == 0


def test_favorite_benchmark_uses_matched_stakes_and_excludes_pickems_and_tied_models():
    prices = {"home": -200, "away": 170}
    rows = [
        {"margin": 3, "actual_margin": 4, "ml": prices},
        {"margin": -2, "actual_margin": 4, "ml": prices},
        {"margin": 0, "actual_margin": 4, "ml": prices},
        {"margin": 3, "actual_margin": 4, "ml": {"home": -110, "away": -110}},
        {"margin": 3, "actual_margin": 4, "ml": None},
    ]
    result = favorite_benchmark(rows)
    assert result["n"] == 2 and result["agreements"] == 1 and result["pickems_excluded"] == 1
    assert result["model"] == {"wins": 1, "losses": 1, "units": -0.5, "roi": -0.25}
    assert result["favorite"] == {"wins": 2, "losses": 0, "units": 1.0, "roi": 0.5}


def test_site_copies_complete_brand_and_team_assets_without_network(root):
    before = Ledger(root).records()
    build_site(root)
    output = root / "site"
    assets = output / "assets"
    sources = json.loads((assets / "teams" / "sources.json").read_text())
    assert len(sources["assets"]) == 60
    assert len({row["team"] for row in sources["assets"]}) == 30
    assert {row["theme"] for row in sources["assets"]} == {"light", "dark"}
    for row in sources["assets"]:
        image = (assets / "teams" / row["file"]).read_bytes()
        assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"
        assert len(image) == row["bytes"]
        if row["team"] == "NOP":
            assert row["source"].endswith("/no.png")
    for name, size in [
        ("favicon-16.png", (16, 16)),
        ("apple-touch-icon.png", (180, 180)),
        ("social-card.png", (1200, 630)),
    ]:
        png = (assets / name).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == size
    for name in ["brand-mark.svg", "wordmark-dark.svg", "wordmark-light.svg", "team-placeholder.svg"]:
        assert ElementTree.parse(assets / name).getroot().tag.endswith("svg")
    for page in ["index", "results", "performance", "methodology"]:
        html = (output / f"{page}.html").read_text()
        assert 'rel="icon"' in html and 'rel="apple-touch-icon"' in html
        assert 'property="og:image"' in html and 'name="twitter:card"' in html
        assert 'class="nav-icon"' in html and "▤" not in html
    assert "Today's picks" in unescape((output / "index.html").read_text())
    data = json.loads((output / "data.json").read_text())
    assert set(data["market_comparisons"]) == {"first:adaptive", "first:fixed", "lock:adaptive", "lock:fixed"}
    assert Ledger(root).records() == before
