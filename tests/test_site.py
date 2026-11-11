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
REPLAY_FILE = "research/output/defense_prior_replay_2026-27.json"
QUOTES_FILE = "research/inputs/v1_20_market_quotes_2026-11-09.json"


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
    assert "historical_replay" not in data
    public = list(csv.DictReader(io.StringIO((root / "site" / "predictions.csv").read_text())))
    internal = list(csv.DictReader(io.StringIO((root / "data" / "predictions.csv").read_text())))
    assert len(public) == 4 and {r["methodology"] for r in public} == {PUBLIC_METHOD}
    assert {r["forecast_id"] for r in public} == current_ids
    assert len(internal) == 12
    internal_metrics = json.loads((root / "research" / "output" / "performance.json").read_text())
    assert internal_metrics["forecast"]["first:adaptive"]["n"] == 3
    assert ledger.records() == before


def copy_quotes(root):
    target = root / QUOTES_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BASE / QUOTES_FILE, target)
    return target


def test_season_record_pools_replay_without_entering_live_metrics_or_ledger(root):
    copy_quotes(root)
    before = Ledger(root).records()
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    season = data["season"]
    assert season["season"] == "2026–27" and season["stage"] == "first"
    assert season["methodology"] == PUBLIC_METHOD
    legacy, native, live = season["segments"]
    assert [s["key"] for s in season["segments"]] == ["replay_legacy", "replay_native", "live"]
    assert legacy["n"] == 117 and native["n"] == 0 and live["n"] == 0
    assert legacy["start_date"] == "2026-10-22" and legacy["end_date"] == "2026-11-06"
    assert legacy["correct"] == 83 and legacy["winner_count"] == 117
    assert season["pooled"]["n"] == 117 and season["pooled"]["correct"] == 83
    # Moneyline series only, at the archived prices, with the market shown beside the model.
    assert set(legacy["betting"]) == {"directional", "value"}
    assert legacy["betting"]["directional"]["n"] == 115
    versus = legacy["versus_market"]
    assert versus["margin"]["n"] == 115 and versus["total"]["n"] == 60
    assert versus["margin"]["market_mae"] < versus["margin"]["model_mae"]
    assert versus["win_probability"]["market_log_loss"] < versus["win_probability"]["model_log_loss"]
    assert "assumed" in season["replay_clock"]
    # The native record stays empty: no replay forecast, bet or grade is created.
    assert data["forecasts"] == [] and data["grades"] == {}
    assert data["performance"] == {"forecast": {}, "betting": {}, "paired": {}}
    assert not list(csv.DictReader(io.StringIO((root / "site" / "predictions.csv").read_text())))
    assert Ledger(root).records() == before


def test_season_record_works_without_retained_quotes_and_rejects_altered_ones(root):
    build_site(root)
    legacy = json.loads((root / "site" / "data.json").read_text())["season"]["segments"][0]
    assert legacy["n"] == 117 and legacy["betting"]["directional"]["n"] == 0
    assert legacy["versus_market"]["margin"] == {"n": 0, "model_mae": None, "market_mae": None}
    path = copy_quotes(root)
    archive = json.loads(path.read_text())
    archive["rows"][0]["first_home_ml"] = "-101.0"
    path.write_text(json.dumps(archive))
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_site(root)


def test_season_record_replays_earlier_versions_and_adds_live_games_once(root, rows, config):
    from sleeper_nba.season import replay_rows, season_record

    older = {
        **config,
        "methodology": "sleeper_only_v1_21",
        "selection_markets": ["moneyline", "ats", "totals"],
    }
    http = FixtureHTTP(rows)
    capture(root, older, http=http, now=dt(AT))
    ledger = Ledger(root)
    record = season_record(ledger.records(), root, config)
    assert [s["n"] for s in record["segments"]] == [117, 0, 0]  # Ungraded games never count.
    http.final = True
    http.at = "2026-11-08T02:00:00+00:00"
    grade(root, config, http=http)
    record = season_record(ledger.records(), root, config)
    assert [s["n"] for s in record["segments"]] == [117, 1, 0]
    assert record["pooled"]["n"] == 118
    native = record["segments"][1]
    assert native["start_date"] == native["end_date"] == "2026-11-07"
    assert native["betting"]["directional"]["n"] == 1  # Its own contemporaneous moneyline.

    # The same game captured and graded under the current version is live, not replay.
    current = copy.deepcopy(next(f for f in ledger.records("forecast") if f["stage"] == "first"))
    current["id"] = "current:" + current["id"]
    current["methodology"] = current["config"]["methodology"] = config["methodology"]
    ledger.append([current])
    record = season_record(ledger.records(), root, config)
    assert [s["n"] for s in record["segments"]] == [117, 0, 1]
    assert record["pooled"]["n"] == 118  # One row per game.

    # Replay never sees a result before it was usable.
    from sleeper_nba.season import replay_observations

    observations = replay_observations(ledger.records(), root, config, 2026)
    scored = replay_rows(observations, config)
    first = min(scored, key=lambda r: r["slate_date"])
    assert first["wp_a"] == 10 and first["segment"] == "replay_legacy"
    assert scored[0]["margin"] == pytest.approx(
        observations[0]["projected_home_score"] - observations[0]["projected_away_score"]
    )


def test_committed_replay_reproduces_from_the_pinned_seed(root, config):
    import importlib.util

    spec = importlib.util.spec_from_file_location("replay", BASE / "research" / "defense_prior_replay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(["--root", str(root)])

    def close(a, b):
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return len(a) == len(b) and all(map(close, a, b))
        # Linear-algebra builds may differ in the last bits; bootstrap bounds are looser.
        return a == pytest.approx(b, rel=1e-6, abs=1e-6) if isinstance(a, float) else a == b

    assert close(json.loads((root / REPLAY_FILE).read_text()), json.loads((BASE / REPLAY_FILE).read_text()))


def test_export_and_reporting_expose_lead_time_minutes_and_lock_coverage(root, rows, config):
    http = FixtureHTTP(rows)
    capture(root, config, http=http, now=dt(AT))
    http.final = True
    http.at = "2026-11-08T02:00:00+00:00"
    grade(root, config, http=http)
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    assert data["methodology_version"] == "1.24"
    assert data["selection_markets"] == ["moneyline"]
    assert data["sample_gates"] == {"forecast_games": 100, "settled_bets": 250}
    assert data["matchup_settings"]["defense_prior"] == 40
    stages = {f["stage"]: f for f in data["forecasts"]}
    for key, forecast in stages.items():
        buckets = data["performance"]["forecast"][f"{key}:adaptive"]["lead_buckets"]
        assert [b["n"] for b in buckets] == [1] and forecast["lead_hours"] > 0
    coverage = data["lock_coverage"]
    assert sum(c["locked"] for c in coverage) == ("lock" in stages)
    assert all(c["games"] == c["locked"] + c["missed"] for c in coverage)
    rows_out = list(csv.DictReader(io.StringIO((root / "site" / "predictions.csv").read_text())))
    assert [r["variant"] for r in rows_out[:2]] == ["adaptive", "fixed"]
    assert all(float(r["home_minutes"]) == 240 and float(r["lead_hours"]) > 0 for r in rows_out)
    assert all(r["tip_utc"] and r["model_tag"] for r in rows_out)
    assert all(len(r["wp_home"].split(".")[-1]) <= 6 for r in rows_out)
    fixed = next(r for r in rows_out if r["variant"] == "fixed")
    assert fixed["home_correction"] == "" and fixed["matchup_id"] == ""


def test_no_public_fallback_when_only_older_forecasts_exist(root, rows, config):
    config = {**config, "methodology": "sleeper_only_v1_19", "matchup": {"enabled": False}}
    capture(root, config, http=FixtureHTTP(rows), now=dt(AT))
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    assert data["methodology"] == PUBLIC_METHOD
    assert data["forecasts"] == [] and data["performance"]["forecast"] == {}


def test_lock_coverage_counts_uncaptured_misses_but_not_prelaunch_windows(root, rows, config):
    # No valid projection during the whole window: a miss is recorded with no forecast.
    http = FixtureHTTP([])
    http.at = "2026-11-07T16:00:00+00:00"
    capture(root, config, http=http, now=dt(http.at))  # Schedule observed before the window.
    http.at = "2026-11-07T22:05:00+00:00"  # After the window under either lock policy.
    capture(root, config, http=http, now=dt(http.at))
    ledger = Ledger(root)
    assert not ledger.records("forecast")
    assert [q["code"] for q in ledger.records("quality") if q["code"] == "missed_lock"] == ["missed_lock"]
    build_site(root)
    coverage = json.loads((root / "site" / "data.json").read_text())["lock_coverage"]
    assert coverage == [
        {
            "slate_date": "2026-11-07",
            "missed": 1,
            "locked": 0,
            "games": 1,
            "median_lead_hours": None,
            "min_lead_hours": None,
        }
    ]


def test_lock_coverage_ignores_windows_closed_before_first_observation(root, rows, config):
    http = FixtureHTTP(rows)
    http.at = "2026-11-07T22:05:00+00:00"  # First run ever, after the window closed.
    capture(root, config, http=http, now=dt(http.at))
    assert any(q["code"] == "missed_lock" for q in Ledger(root).records("quality"))
    build_site(root)
    assert json.loads((root / "site" / "data.json").read_text())["lock_coverage"] == []


def test_season_record_review_fixes_season_scope_quote_timing_and_result_revisions(root, rows, config):
    from sleeper_nba.season import _forecast_markets, _scores_at, live_rows, replay_observations, replay_rows

    http = FixtureHTTP(rows)
    capture(root, config, http=http, now=dt(AT))
    http.final = True
    http.at = "2026-11-08T02:00:00+00:00"
    grade(root, config, http=http)
    records = Ledger(root).records()
    forecast = next(f for f in records if f["kind"] == "forecast" and f["stage"] == "first")

    # Live rows belong to the reported season only.
    assert len(live_rows(records, config, 2026)) == 1 and live_rows(records, config, 2027) == []

    # A context line needs a paired, fresh, pregame quote, whatever its stored reason says.
    assert _forecast_markets(forecast, "adaptive")["spread"] is not None
    stale = copy.deepcopy(forecast)
    for m in stale["variants"]["adaptive"]["markets"]:
        if m["market"] == "ats":
            m.update(captured_at="2026-11-07T09:00:00+00:00", unavailable_reason="unsupported_line_increment")
            m.update(eligible=False, line=-5.0)
    assert _forecast_markets(stale, "adaptive")["spread"] is None
    assert _forecast_markets(stale, "adaptive")["total_line"] is not None

    # A later score correction is not visible to replay fits made before it.
    older = copy.deepcopy(forecast)
    older["id"] = "older:" + forecast["id"]
    older["methodology"] = older["config"]["methodology"] = "sleeper_only_v1_21"
    result = next(r for r in records if r["kind"] == "result")
    correction = {
        **result,
        "id": "result:correction",
        "verified_at": "2026-11-12T00:00:00+00:00",
        "home_score": result["home_score"] + 9,
        "supersedes": result["id"],
    }
    observations = replay_observations([older, result, correction], root, config, 2026)
    native = next(o for o in observations if o["segment"] == "replay_native")
    assert native["available_at"] == result["verified_at"]
    assert native["actual_home_score"] == correction["home_score"]  # Graded on the final score.
    assert _scores_at(native, dt("2026-11-09T12:00:00+00:00"))[0] == result["home_score"]
    assert _scores_at(native, dt("2026-11-12T12:00:00+00:00"))[0] == correction["home_score"]
    assert len(replay_rows(observations, config)) == 118


def test_site_exports_pooled_rows_and_display_only_live_win_chance(root):
    import math

    from sleeper_nba.site import live_win_fit

    before = Ledger(root).records()
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    season = data["season"]
    # One public row per game of the pooled record, in tip order, with the fields the pages read.
    assert len(season["rows"]) == season["pooled"]["n"] == len({r["game_id"] for r in season["rows"]})
    assert [r["tip_utc"] for r in season["rows"]] == sorted(r["tip_utc"] for r in season["rows"])
    correct = sum(
        (r["margin"] > 0) == (r["actual_home_score"] > r["actual_away_score"])
        for r in season["rows"]
        if r["margin"] != 0
    )
    assert correct == season["pooled"]["correct"]
    assert set(season["rows"][0]) == {
        "game_id",
        "slate_date",
        "tip_utc",
        "home",
        "away",
        "segment",
        "margin",
        "wp_home",
        "actual_home_score",
        "actual_away_score",
        "ml",
    }
    # The live win chance is a browser display: coefficients only, nothing in the ledger.
    fit = data["live_wp"]
    assert set(fit) == {"knots", "a", "b"} and len(fit["knots"]) == len(fit["a"]) == len(fit["b"])

    def chance(lead, mu, t):
        def at(values):
            k = fit["knots"]
            for i in range(1, len(k)):
                if t <= k[i]:
                    return values[i - 1] + (t - k[i - 1]) / (k[i] - k[i - 1]) * (values[i] - values[i - 1])
            return values[-1]

        z = at(fit["a"]) * lead / math.sqrt(t) + at(fit["b"]) * mu * math.sqrt(t)
        return 0.5 * math.erfc(-z / math.sqrt(2))

    assert chance(0, 0, 1.0) == 0.5
    assert 0.6 < chance(0, 5, 1.0) < 0.75  # a five-point favorite at tip
    assert chance(10, 0, 0.5) < chance(10, 0, 0.1) < 1  # the same lead is worth more late
    assert chance(-8, 6, 0.25) < 0.5 < chance(8, -6, 0.25)
    (root / "web" / "live_wp.json").write_text("{}")
    assert live_win_fit(root) is None
    assert Ledger(root).records() == before


def test_page_script_defines_every_renderer_it_dispatches_to():
    import re

    script = (BASE / "web" / "app.js").read_text()
    dispatched = re.search(r"\(\{index:today,([a-z,]+)\}\[page\]", script).group(1).split(",")
    for name in ["today", *dispatched]:
        assert f"function {name}(" in script, name
    for helper in ["card", "teamBlock", "pickSummary", "gameDetails", "seasonStats", "rowRecord"]:
        assert f"function {helper}(" in script, helper
