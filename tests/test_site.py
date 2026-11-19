from __future__ import annotations

import copy
import csv
import io
import json
import shutil
from pathlib import Path

import pytest
import yaml
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


def legacy_only(root, config=None, closing=False):
    """These tests pin the 117-game legacy seed; the outage and closing-price backfills have their own tests."""
    path = root / "config.yaml"
    data = yaml.safe_load(path.read_text())
    data["matchup"].pop("backfill_files", None)
    if not closing:
        data.pop("closing_backfill", None)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    if config is not None:
        config["matchup"].pop("backfill_files", None)
        if not closing:
            config.pop("closing_backfill", None)


def test_season_record_pools_replay_without_entering_live_metrics_or_ledger(root):
    legacy_only(root)
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
    legacy_only(root)
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

    legacy_only(root, config)
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
    assert data["methodology_version"] == "1.30"
    assert data["selection_markets"] == ["moneyline"]
    assert data["sample_gates"] == {"forecast_games": 100, "settled_bets": 250}
    assert (
        data["matchup_settings"]["defense_prior"] == 20 and data["matchup_settings"]["apply_offense"] is True
    )
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
            "late": 0,
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
    assert not any(q["code"] == "missed_lock" for q in Ledger(root).records("quality"))
    build_site(root)
    assert json.loads((root / "site" / "data.json").read_text())["lock_coverage"] == []


def test_season_record_review_fixes_season_scope_quote_timing_and_result_revisions(root, rows, config):
    from sleeper_nba.season import _forecast_markets, _scores_at, live_rows, replay_observations, replay_rows

    legacy_only(root, config)
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


def test_v1_30_replay_uses_game_day_snapshots_only(root, rows, config):
    from datetime import timedelta

    from sleeper_nba.common import iso
    from sleeper_nba.season import _replay

    legacy_only(root, config)
    http = FixtureHTTP(rows)
    capture(root, config, http=http, now=dt(AT))
    http.final = True
    http.at = "2026-11-08T02:00:00+00:00"
    grade(root, config, http=http)
    records = Ledger(root).records()
    current = next(f for f in records if f["kind"] == "forecast" and f["stage"] == "first")
    result = next(r for r in records if r["kind"] == "result")
    game_id = current["game"]["game_id"]
    assert config["replay_snapshot"] == "game_day_et_v1"

    def older(stage, captured_at, bump):
        f = copy.deepcopy(current)
        f["id"] = f"older:{stage}:{captured_at}"
        f["stage"], f["captured_at"] = stage, captured_at
        f["methodology"] = f["config"]["methodology"] = "sleeper_only_v1_24"
        f["raw"] = {**f["raw"], "home_score": f["raw"]["home_score"] + bump}
        return f

    game_day = current["captured_at"]  # 12:00 ET on the game's Eastern date.
    day_before = iso(dt(game_day) - timedelta(days=1))
    first_early, first_today = older("first", day_before, 1), older("first", game_day, 2)
    lock = older("lock", iso(dt(game_day) + timedelta(minutes=1)), 3)
    legacy = {k: v for k, v in config.items() if k != "replay_snapshot"}

    def replayed(records, cfg):
        observations, excluded = _replay(records, root, cfg, 2026)
        native = [o for o in observations if o["game_id"] == game_id]
        return native, [e["game_id"] for e in excluded]

    # A game-day lock stands in for a first snapshot captured the day before.
    (o,), excluded = replayed([first_early, lock, result], config)
    assert (o["snapshot_stage"], o["snapshot_utc"], excluded) == ("lock", lock["captured_at"], [])
    assert o["projected_home_score"] == lock["raw"]["home_score"]
    # A game-day first snapshot is preferred to a lock, as before.
    (o,), _ = replayed([first_early, first_today, lock, result], config)
    assert (o["snapshot_stage"], o["snapshot_utc"]) == ("first", game_day)
    # With no game-day capture the game is excluded and listed.
    native, excluded = replayed([first_early, result], config)
    assert native == [] and excluded == [game_id]
    # Configurations frozen without the key keep the latest first snapshot, whenever captured.
    (o,), excluded = replayed([first_early, lock, result], legacy)
    assert (o["snapshot_stage"], o["snapshot_utc"], excluded) == ("first", day_before, [])
    # The seed is captured on game day, so the policy keeps all 117 seed games.
    seed_games = [o for o in _replay([], root, config, 2026)[0] if o["segment"] == "replay_legacy"]
    assert len(seed_games) == 117


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
        "snapshot_stage",
        "margin",
        "wp_home",
        "actual_home_score",
        "actual_away_score",
        "ml",
        "price_basis",
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


def test_outage_backfill_joins_the_replayed_season_record(root):
    """Owner update, 17 November 2026: backfilled games appear once, as archived first snapshots."""
    copy_quotes(root)
    before = Ledger(root).records()
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    legacy = data["season"]["segments"][0]
    backfill = json.loads((root / "data/calibration_seeds/gap_backfill_2026-11-17.json").read_text())
    assert legacy["n"] == 117 + len(backfill["observations"])
    assert legacy["end_date"] == "2026-11-16"
    assert data["forecasts"] == [] and data["grades"] == {}
    assert Ledger(root).records() == before


def test_v1_27_closing_prices_fill_only_unpriced_season_games(root):
    from sleeper_nba.common import load_config
    from sleeper_nba.season import closing_prices, season_record

    legacy_only(root, closing=True)
    copy_quotes(root)
    before = Ledger(root).records()
    build_site(root)
    season = json.loads((root / "site" / "data.json").read_text())["season"]
    legacy = season["segments"][0]
    # 115 legacy games kept their archived first-snapshot price; the other 2 are priced at the close.
    assert season["price_basis"]["counts"] == {"snapshot": 115, "closing": 2}
    assert season["price_basis"]["closing"]["n"] == 2 and season["price_basis"]["snapshot"]["n"] == 115
    assert legacy["betting"]["directional"]["n"] == 117
    rows = {r["game_id"]: r for r in season["rows"]}
    closes = closing_prices(root, load_config(root))
    for row in rows.values():
        if row["price_basis"] == "closing":
            close = closes[row["game_id"]]["close"]["moneyline"]
            assert row["ml"] == {"home": close["home"], "away": close["away"]}
    # A snapshot price is never replaced by the close: compare with the record built without the backfill.
    cfg = load_config(root)
    cfg.pop("closing_backfill")
    plain = {r["game_id"]: r for r in season_record(Ledger(root).records(), root, cfg)["rows"]}
    assert all(rows[g]["ml"] == plain[g]["ml"] for g in rows if rows[g]["price_basis"] == "snapshot")
    assert all(plain[g]["ml"] is None for g in rows if rows[g]["price_basis"] == "closing")
    assert Ledger(root).records() == before
    # The pinned file is checked against its hash.
    path = root / load_config(root)["closing_backfill"]["file"]
    data = json.loads(path.read_text())
    data["rows"][0]["close"]["moneyline"]["home"] = -999.0
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Closing-price backfill integrity failure"):
        build_site(root)


def test_elapsed_schedule_window_counts_even_if_watcher_never_returns(root, config):
    # Pins the v2 narrow window, which still governs every lock frozen under it (v1.29 adds late locks).
    config = {**config, "lock_policy": "five_hours_15min_v2"}
    from sleeper_nba.pipeline import lock_outcomes
    from sleeper_nba.site import summarize_lock_outcomes

    http = FixtureHTTP([])
    http.at = "2026-11-07T16:00:00Z"
    capture(root, config, http=http, now=dt(http.at))
    records = Ledger(root).records()
    assert not any(r.get("code") == "missed_lock" for r in records)
    assert summarize_lock_outcomes(lock_outcomes(records, config, dt("2026-11-07T17:15:00Z"))) == []
    coverage = summarize_lock_outcomes(lock_outcomes(records, config, dt("2026-11-07T17:15:01Z")))
    assert [(s["games"], s["locked"], s["missed"]) for s in coverage] == [(1, 0, 1)]
    build_site(root)
    data = json.loads((root / "site/data.json").read_text())
    (only,) = data["lock_outcomes"].values()
    (miss,) = data["missed_locks"]
    assert only["status"] == "missed" and miss["instance_id"] == only["instance_id"]
    assert data["lock_coverage"] == coverage


def test_release_change_preserves_operational_lock_status(root, rows, config):
    http = FixtureHTTP(rows)
    old = {**config, "methodology": "sleeper_only_v1_27"}
    capture(root, old, http=http, now=dt(http.at))
    http.at = "2026-11-07T17:20:00Z"
    capture(root, config, http=http, now=dt(http.at))
    ledger = Ledger(root)
    assert not any(r.get("code") == "missed_lock" for r in ledger.records())
    # A historical miss from the old version-specific event writer stays immutable.
    lock = next(f for f in ledger.records("forecast") if f["stage"] == "lock")
    ledger.append(
        [
            dict(
                kind="quality",
                code="missed_lock",
                id="missed_lock:" + lock["instance_id"],
                instance_id=lock["instance_id"],
                context=lock["game"]["game_id"],
                at=http.at,
                window_end="2026-11-07T17:15:00Z",
            )
        ]
    )
    build_site(root)
    data = json.loads((root / "site/data.json").read_text())
    assert data["lock_outcomes"][lock["instance_id"]]["status"] == "locked"
    assert data["missed_locks"] == [] and not any(q["code"] == "missed_lock" for q in data["quality"])
    assert data["lock_coverage"][0]["locked"] == 1
