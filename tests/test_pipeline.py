from __future__ import annotations

import copy
import json
import shutil
from datetime import timedelta

import pytest

from sleeper_nba.common import dt, iso
from sleeper_nba.evaluation import latest_grades, summarize
from sleeper_nba.models import DEFAULTS
from sleeper_nba.pipeline import assign_instance, capture, grade, next_wake, refit, validate
from sleeper_nba.research import walk_forward
from sleeper_nba.site import build_site
from sleeper_nba.sources import HTTP, SourceError, espn_quotes, schedule
from sleeper_nba.storage import Ledger

AT = "2026-11-07T17:00:00+00:00"


class FixtureHTTP:
    def __init__(self, rows):
        self.rows = rows
        self.final = False
        self.correction = False
        self.at = AT
        self.fail = False

    def request(self, url, params=None):
        if self.fail:
            raise SourceError("fixture outage")
        if "state/nba" in url:
            payload = {"season": "2026", "week": 3}
        elif "projections/nba" in url:
            payload = self.rows if "/2026/3" in url else []
        elif "scoreboard" in url:
            payload = {"events": [self.event()] if params["dates"] == "20261107" else []}
        else:
            raise AssertionError(url)
        return {"url": url, "requested_at": self.at, "captured_at": self.at, "payload": payload}

    def event(self):
        return {
            "id": "fixture-1",
            "date": "2026-11-07T22:00Z",
            "season": {"type": 2, "year": 2027},
            "status": {
                "type": {
                    "state": "post" if self.final else "pre",
                    "name": "STATUS_FINAL" if self.final else "STATUS_SCHEDULED",
                    "completed": self.final,
                }
            },
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"abbreviation": "BOS", "displayName": "Boston Celtics"},
                            "score": "125" if self.correction else "120" if self.final else "0",
                        },
                        {
                            "homeAway": "away",
                            "team": {"abbreviation": "NYK", "displayName": "New York Knicks"},
                            "score": "110" if self.final else "0",
                        },
                    ],
                    "odds": [
                        {
                            "provider": {"name": "Draft Kings"},
                            "moneyline": {
                                "home": {"close": {"odds": "-200"}},
                                "away": {"close": {"odds": "+170"}},
                            },
                            "pointSpread": {
                                "home": {"close": {"line": "-5.5", "odds": "-110"}},
                                "away": {"close": {"line": "+5.5", "odds": "-110"}},
                            },
                            "total": {
                                "over": {"close": {"line": "o225.5", "odds": "-110"}},
                                "under": {"close": {"line": "u225.5", "odds": "-110"}},
                            },
                        }
                    ],
                }
            ],
        }


def test_full_capture_grade_report_and_correction(root, rows, config):
    http = FixtureHTTP(rows)
    result = capture(root, config, http=http, now=dt(AT))
    assert result["forecasts"] == 2  # First and lock share the same valid capture.
    ledger = Ledger(root)
    forecasts = ledger.records("forecast")
    assert len(forecasts) == 2
    assert all(f["market_availability"] == "qualifying" for f in forecasts)
    assert all(f["variants"]["fixed"]["selections"] for f in forecasts)
    original = copy.deepcopy(forecasts)
    count = len(ledger.records())
    assert capture(root, config, http=http, now=dt(AT))["forecasts"] == 0
    assert len(ledger.records()) == count
    assert validate(root)["status"] == "valid"
    # No outcomes exist before verified completion.
    assert summarize(ledger.records())["forecast"] == {}
    http.final = True
    http.at = "2026-11-08T02:00:00+00:00"
    assert grade(root, config, http=http)["graded"] == 2
    metrics = summarize(ledger.records())
    assert metrics["forecast"]["first:fixed"]["accuracy"] == 1
    assert metrics["forecast"]["first:adaptive"]["margin"]["mae"] == 0
    assert grade(root, config, http=http)["graded"] == 0
    http.correction = True
    http.at = "2026-11-08T03:00:00+00:00"
    assert grade(root, config, http=http)["graded"] == 2
    assert len(ledger.records("result")) == 2
    assert all(g["result"]["home_score"] == 125 for g in latest_grades(ledger.records()).values())
    assert ledger.records("forecast") == original
    assert refit(root, config, now=dt(http.at))["calibration_versions"] == 0
    assert build_site(root)["pages"] == 4
    site_data = json.loads((root / "site" / "data.json").read_text())
    assert site_data["performance"]["forecast"]["first:fixed"]["n"] == 1
    assert "raw_rows" not in site_data["forecasts"][0]["raw"]
    assert "players" not in site_data["forecasts"][0]["raw"]
    assert site_data["forecasts"][0]["lineup"]
    assert set(site_data["forecasts"][0]["lineup"][0]) == {
        "name",
        "team",
        "projection_status",
        "points",
        "minutes",
        "injury_status",
    }
    assert walk_forward(root, config)["replay_rows"] == 4
    assert validate(root)["status"] == "valid"
    # Durable restore reproduces record counts and forecast hashes.
    backup = root / "restored"
    shutil.copytree(root / "data", backup / "data")
    assert Ledger(backup).records() == ledger.records()


def test_dry_run_has_no_writes(root, rows, config):
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    assert capture(root, config, dry_run=True, http=FixtureHTTP(rows), now=dt(AT))["forecasts"] == 2
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before


def test_outage_preserves_accepted_forecasts(root, rows, config):
    http = FixtureHTTP(rows)
    capture(root, config, http=http, now=dt(AT))
    original = Ledger(root).records("forecast")
    http.fail = True
    result = capture(root, config, http=http, now=dt(AT))
    assert result["status"] == "sleeper_unavailable"
    assert Ledger(root).records("forecast") == original
    # Retry outage is idempotent as well.
    capture(root, config, http=http, now=dt(AT))
    assert Ledger(root).records("forecast") == original


def test_schedule_change_retires_then_reschedules(game):
    original, schedules = assign_instance(game, [])
    shifted = {**game, "tip_utc": "2026-11-07T23:00:00+00:00", "schedule_revision": "r2"}
    new, additions = assign_instance(shifted, schedules)
    assert new["instance_id"] != original["instance_id"]
    assert any(a["kind"] == "retired" for a in additions)
    minor = {**game, "tip_utc": "2026-11-07T22:10:00+00:00", "schedule_revision": "r3"}
    same, additions = assign_instance(minor, schedules)
    assert same["instance_id"] == original["instance_id"]
    assert not any(a["kind"] == "retired" for a in additions)


def test_postponement_voids_research_selections(root, rows, config):
    http = FixtureHTTP(rows)
    capture(root, config, http=http, now=dt(AT))
    ledger = Ledger(root)
    forecast = ledger.records("forecast")[0]
    ledger.append(
        [
            {
                "id": "retired:" + forecast["instance_id"],
                "kind": "retired",
                "instance_id": forecast["instance_id"],
                "at": AT,
                "reason": "postponed",
            }
        ]
    )
    grade(root, config, http=http)
    grades = latest_grades(ledger.records())
    assert all(g["void"] for g in grades.values())
    assert all(
        s["outcome"] == "void"
        for g in grades.values()
        for v in g["variants"].values()
        for s in v["selections"]
    )
    assert not summarize(ledger.records())["forecast"]


def test_espn_parser_timestamp_and_line_pairing(rows):
    http = FixtureHTTP(rows)
    games, _ = schedule(http, "2026-11-07")
    game, event = games[0]
    quotes = espn_quotes(game, event)
    assert len(quotes) == 3
    assert all(q["captured_at"] == AT and q["updated_at"] is None for q in quotes)
    assert next(q for q in quotes if q["market"] == "ats")["line"] == -5.5
    event["competitions"][0]["odds"][0]["pointSpread"]["away"]["close"]["line"] = "6.5"
    assert not any(q["market"] == "ats" for q in espn_quotes(game, event))


def test_bounded_http_failure(monkeypatch):
    import httpx

    calls = []
    http = HTTP()

    def bad(*args, **kwargs):
        calls.append(1)
        raise httpx.ConnectError("failure")

    monkeypatch.setattr(http.client, "get", bad)
    monkeypatch.setattr("sleeper_nba.sources.time.sleep", lambda _: None)
    with pytest.raises(SourceError):
        http.request("https://api.sleeper.app/v1/state/nba")
    assert len(calls) == 3
    http.close()


def test_daily_refit_manifest_and_future_application(root, config):
    from sleeper_nba.common import digest
    from sleeper_nba.models import active_calibration

    ledger = Ledger(root)
    additions = []
    for i in range(35):
        captured = dt(AT) - timedelta(days=40 - i)
        tip = captured + timedelta(hours=5)
        verified = tip + timedelta(hours=3)
        instance = f"training-{i}"
        game = {
            "game_id": str(i),
            "instance_id": instance,
            "tip_utc": iso(tip),
            "slate_date": tip.date().isoformat(),
            "status": "post",
            "status_name": "STATUS_FINAL",
            "completed": True,
            "competition_class": "regular",
        }
        additions.extend(
            [
                {
                    "id": "schedule:" + instance,
                    "kind": "schedule",
                    "instance_id": instance,
                    "at": iso(verified),
                    "game": game,
                },
                {
                    "id": "forecast:" + instance,
                    "kind": "forecast",
                    "instance_id": instance,
                    "stage": "first",
                    "provenance": "native",
                    "captured_at": iso(captured),
                    "game": {**game, "status": "pre"},
                    "raw": {"margin": 4, "total": 230},
                    "config": {"methodology": config["methodology"]},
                    "variants": {"adaptive": {"scoring": {"margin": 4, "total": 230}}},
                },
                {
                    "id": "result:" + instance,
                    "kind": "result",
                    "instance_id": instance,
                    "verified_at": iso(verified),
                    "home_score": 120 if i % 3 else 105,
                    "away_score": 110,
                },
            ]
        )
    ledger.append(additions)
    now = dt(AT)
    assert refit(root, config, now=now)["calibration_versions"] == 1
    cal = ledger.records("calibration")[0]
    assert cal["training_count"] == 35
    assert cal["manifest"][-1]["weight"] == 1
    assert digest(cal["config"]) == cal["config_hash"]
    assert (
        active_calibration(ledger.records(), "first", iso(now - timedelta(seconds=1)))["params"] == DEFAULTS
    )
    assert active_calibration(ledger.records(), "first", iso(now))["id"] == cal["id"]
    assert active_calibration(ledger.records(), "lock", iso(now))["params"] == DEFAULTS
    assert refit(root, config, now=now)["calibration_versions"] == 0


def test_late_lock_is_qualifying_and_immutable(root, rows, config):
    # Frozen v1.20.1 policy: locks were accepted until strictly before tip.
    config = {**config, "lock_policy": "five_hours_to_tip_v1"}
    http = FixtureHTTP(rows)
    http.at = "2026-11-07T21:59:59+00:00"
    assert capture(root, config, http=http, now=dt(http.at))["forecasts"] == 2
    forecasts = Ledger(root).records("forecast")
    lock = next(f for f in forecasts if f["stage"] == "lock")
    assert lock["lead_hours"] == pytest.approx(1 / 3600)
    assert all(m["eligible"] for m in lock["variants"]["adaptive"]["markets"])
    assert validate(root)["status"] == "valid"
    http.at = "2026-11-07T22:00:00+00:00"
    assert capture(root, config, http=http, now=dt(http.at))["forecasts"] == 0
    assert Ledger(root).records("forecast") == forecasts


def test_latest_pregame_fallback_survives_tip_invalid_feed_and_outage(root, rows, config):
    from sleeper_nba.matchup import load_previews

    http = FixtureHTTP(rows)
    http.at = "2026-11-07T16:00:00+00:00"
    capture(root, config, http=http, now=dt(http.at))
    http.at = "2026-11-07T16:30:00+00:00"
    capture(root, config, http=http, now=dt(http.at))
    saved = load_previews(root)["forecasts"]
    assert len(saved) == 1 and dt(saved[0]["captured_at"]) == dt(http.at)
    assert saved[0]["lineup"] and "player_id" not in saved[0]["lineup"][0]
    assert all(not v["selections"] and v["recommendations"] for v in saved[0]["variants"].values())
    assert not any(f["stage"] == "lock" for f in Ledger(root).records("forecast"))
    http.rows = []
    http.at = "2026-11-07T21:00:00+00:00"
    capture(root, config, http=http, now=dt(http.at))
    assert load_previews(root)["forecasts"] == saved
    http.at = "2026-11-07T22:00:00+00:00"
    capture(root, config, http=http, now=dt(http.at))
    assert load_previews(root)["forecasts"] == saved
    http.fail = True
    capture(root, config, http=http, now=dt(http.at))
    assert load_previews(root)["forecasts"] == saved
    build_site(root)
    data = json.loads((root / "site" / "data.json").read_text())
    assert data["previews"] == saved  # Remains visible after tip, without new bets.
    assert len(Ledger(root).records("forecast")) == 1


def test_watcher_wakes_at_lock_target_retries_in_window_and_ticks(root, rows, config):
    http = FixtureHTTP(rows)
    http.at = "2026-11-07T15:00:00+00:00"
    capture(root, config, http=http, now=dt(http.at))  # First snapshot; lock target is 17:00.
    ledger = Ledger(root)

    def wake(at, **kwargs):
        return next_wake(ledger.records(), config, dt(at), **kwargs)

    assert wake("2026-11-07T15:00:00+00:00") == {"seconds": 600, "reason": "tick", "game_id": None}
    near = wake("2026-11-07T16:56:00+00:00")
    assert near == {"seconds": 245, "reason": "lock_target", "game_id": "fixture-1"}
    assert wake("2026-11-07T16:59:59.500000+00:00")["seconds"] == 15  # Floor; never a busy loop.
    assert wake("2026-11-07T17:03:00+00:00")["reason"] == "lock_window_open"
    assert wake("2026-11-07T17:03:00+00:00", retry_seconds=60)["seconds"] == 60
    assert wake("2026-11-07T17:15:01+00:00")["reason"] == "tick"  # Window closed.
    # Schedule-aware pace: slow when no game is near, brisk from 8h before tip.
    assert wake("2026-11-07T09:00:00+00:00") == {"seconds": 3600, "reason": "idle_tick", "game_id": None}
    early = wake("2026-11-07T13:40:00+00:00")
    assert early == {"seconds": 1200, "reason": "active_start", "game_id": "fixture-1"}
    assert wake("2026-11-07T14:00:00+00:00")["reason"] == "tick"
    assert wake("2026-11-07T23:30:00+00:00")["reason"] == "tick"  # In play, awaiting the final.
    assert wake("2026-11-08T04:00:01+00:00")["reason"] == "idle_tick"  # Bounded even with no result.
    assert wake("2026-11-07T09:00:00+00:00", idle_seconds=120)["seconds"] == 600  # Never below the tick.
    count = len(ledger.records())

    http.at = "2026-11-07T17:00:05+00:00"
    assert capture(root, config, http=http, now=dt(http.at))["forecasts"] == 1
    lock = next(f for f in ledger.records("forecast") if f["stage"] == "lock")
    assert lock["lead_hours"] == pytest.approx(5 - 5 / 3600)
    assert lock["config"]["lock_policy"] == "five_hours_15min_v2"
    assert wake("2026-11-07T17:03:00+00:00")["reason"] == "tick"  # Locked; nothing left to chase.
    assert len(ledger.records()) == count + 1
    assert validate(root)["status"] == "valid"
    http.final = True
    http.at = "2026-11-08T00:40:00+00:00"
    grade(root, config, http=http)
    assert wake("2026-11-08T00:41:00+00:00")["reason"] == "idle_tick"  # Graded; nothing near.


def test_capture_after_narrow_window_records_a_missed_lock(root, rows, config):
    http = FixtureHTTP(rows)
    http.at = "2026-11-07T17:15:01+00:00"
    assert capture(root, config, http=http, now=dt(http.at))["forecasts"] == 1
    ledger = Ledger(root)
    assert [f["stage"] for f in ledger.records("forecast")] == ["first"]
    assert [q["code"] for q in ledger.records("quality")] == ["missed_lock"]


def test_methodology_transition_captures_fresh_forecasts_once(root, rows, config):
    http = FixtureHTTP(rows)
    previous = copy.deepcopy(config)
    previous["methodology"] = "sleeper_only_v1_22"
    assert capture(root, previous, http=http, now=dt(AT))["forecasts"] == 2
    originals = copy.deepcopy(Ledger(root).records("forecast"))
    http.at = "2026-11-07T17:01:00+00:00"
    assert capture(root, config, http=http, now=dt(http.at))["forecasts"] == 2
    forecasts = Ledger(root).records("forecast")
    assert all(f in forecasts for f in originals)
    current = [f for f in forecasts if f["methodology"] == config["methodology"]]
    assert len(current) == 2
    assert all(f["captured_at"] == http.at for f in current)
    assert len({f["id"] for f in forecasts}) == 4
    assert capture(root, config, http=http, now=dt(http.at))["forecasts"] == 0
    validate(root)
