from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from sleeper_nba.common import digest, load_config, load_evaluation_plan
from sleeper_nba.markets import evaluate
from sleeper_nba.models import DEFAULTS, probabilities
from sleeper_nba.prospective import forward_evaluation, paired_scores
from sleeper_nba.season import _forecast_markets, season_record


@pytest.fixture
def plan(root):
    return load_evaluation_plan(root)


def forecast(plan, number=1, stage="first", slate="2026-11-11", market=True):
    stamp = slate + "T17:00:00+00:00"
    quote = {
        "market": "moneyline",
        "book": "draftkings",
        "quote_id": "quote",
        "provider_event_id": str(number),
        "captured_at": stamp,
        "updated_at": None,
        "line": None,
        "prices": {"home": -150, "away": 150},
        "eligible": True,
    }
    return {
        "kind": "forecast",
        "id": f"f{number}",
        "instance_id": f"i{number}",
        "stage": stage,
        "methodology": plan["methodology"],
        "provenance": "native",
        "captured_at": stamp,
        "config": copy.deepcopy(plan["config"]),
        "config_hash": plan["config_hash"],
        "game": {
            "game_id": str(number),
            "season": 2026,
            "slate_date": slate,
            "tip_utc": slate + "T22:00:00+00:00",
            "status": "pre",
        },
        "raw": {"home_score": 110, "away_score": 110, "margin": 0, "total": 220},
        "variants": {
            "adaptive": {
                "wp_home": 0.8,
                "markets": [quote] if market else [],
                "scoring": {"margin": 8, "total": 220},
                "params": dict(DEFAULTS),
            }
        },
    }


def grade(f, won=True):
    return {
        "kind": "grade",
        "id": "g" + f["id"],
        "forecast_id": f["id"],
        "at": f["game"]["slate_date"] + "T23:59:00+00:00",
        "void": False,
        "result": {"home_score": 120 if won else 100, "away_score": 110},
    }


def test_frozen_configuration_rejects_drift_and_allows_explicit_new_methodology(root, plan):
    assert digest(load_config(root)) == plan["config_hash"]
    config = copy.deepcopy(plan["config"])
    config["matchup"]["defense_prior"] = 5
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="Frozen methodology"):
        load_config(root)
    config["methodology"] = "explicit_future_version"
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    assert load_config(root)["methodology"] == "explicit_future_version"
    plan["config"]["wp_initial"] = 8
    (root / "evaluation_plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="Invalid frozen"):
        load_evaluation_plan(root)


def test_committed_freeze_matches_current_settings():
    root = Path(__file__).parents[1]
    plan = load_evaluation_plan(root)
    assert digest(load_config(root)) == plan["config_hash"]
    assert plan["starts_at"] == "2026-11-10T20:21:22+00:00"
    assert plan["primary_metric"] == "log_loss"


def test_paired_scores_resample_whole_slates_and_keep_game_weights(plan):
    rows = [("a", 0.8, 0.5, 1), ("a", 0.8, 0.5, 1), ("b", 0.8, 0.5, 0)]
    scores = paired_scores(rows, plan)
    ll = scores["scores"]["log_loss"]
    d_win, d_loss = math.log(0.5 / 0.8), math.log(0.5 / 0.2)
    assert ll["model_minus_baseline"] == pytest.approx((2 * d_win + d_loss) / 3)
    assert ll["confidence_interval_95"] == pytest.approx([d_win, d_loss])
    assert scores["n"] == 3 and scores["slates"] == 2 and scores["exploratory"]
    assert paired_scores(rows, plan) == scores
    assert paired_scores(rows[:2], plan)["scores"]["log_loss"]["confidence_interval_95"] is None
    assert paired_scores([], plan)["scores"] == {}
    assert scores["scores"]["brier"]["model_minus_baseline"] == pytest.approx(
        np.mean([0.04 - 0.25, 0.04 - 0.25, 0.64 - 0.25])
    )


def test_forward_cohort_has_separate_stages_prices_and_no_historical_fallback(plan):
    f = forecast(plan)
    unpriced = forecast(plan, 2, slate="2026-11-12", market=False)
    lock = forecast(plan, 3, stage="lock")
    historical = forecast(plan, 4, slate="2026-11-09")
    pending = forecast(plan, 5)
    other_season = forecast(plan, 6)
    other_season["game"]["season"] = 2027
    drift = forecast(plan, 7)
    drift["config"]["value_probability_edge"] = 0.01
    preview = forecast(plan, 8)
    preview["kind"] = "preview"
    old_method = forecast(plan, 9)
    old_method["methodology"] = "sleeper_only_v1_20"
    records = [
        f,
        grade(f),
        unpriced,
        grade(unpriced, False),
        lock,
        grade(lock),
        historical,
        grade(historical),
        pending,
        other_season,
        grade(other_season),
        drift,
        grade(drift),
        preview,
        old_method,
        grade(old_method),
    ]
    before = copy.deepcopy(records)
    result = forward_evaluation(records, plan)
    first = result["stages"]["first"]
    assert first["settled"] == 2 and first["pending"] == 1 and first["excluded_config"] == 1
    assert first["missing_market"] == 1
    assert first["raw_sleeper"]["n"] == 2 and first["market"]["n"] == 1
    assert first["raw_sleeper"]["scores"]["log_loss"]["baseline"] == pytest.approx(-math.log(0.5))
    assert first["market"]["scores"]["log_loss"]["baseline"] == pytest.approx(-math.log(0.6))
    assert result["stages"]["lock"]["settled"] == 1
    assert forward_evaluation([historical, grade(historical)], plan)["stages"]["first"]["settled"] == 0
    assert records == before


@pytest.mark.parametrize(
    "failure",
    ["unpaired", "after_tip", "wrong_book", "ineligible", "bad_price", "wrong_event", "lock_window"],
)
def test_market_baseline_rejects_unusable_frozen_quotes(plan, failure):
    f = forecast(plan)
    quote = f["variants"]["adaptive"]["markets"][0]
    if failure == "unpaired":
        quote["captured_at"] = "2026-11-11T16:00:00+00:00"
    elif failure == "after_tip":
        f["game"]["tip_utc"] = "2026-11-11T17:01:00+00:00"
        quote["captured_at"] = "2026-11-11T17:02:00+00:00"
    elif failure == "wrong_book":
        quote["book"] = "another_book"
    elif failure == "ineligible":
        quote["eligible"] = False
    elif failure == "wrong_event":
        quote["provider_event_id"] = "another-game"
    elif failure == "lock_window":
        f["stage"] = "lock"
        quote["captured_at"] = "2026-11-11T16:59:00+00:00"
    else:
        quote["prices"]["home"] = 50
    first = forward_evaluation([f, grade(f)], plan)["stages"][f["stage"]]
    assert first["settled"] == 1 and first["market"]["n"] == 0


def test_retired_instance_does_not_hide_rescheduled_game_and_latest_grade_wins(plan):
    old = forecast(plan)
    new = forecast(plan, 2)
    new["game"]["game_id"] = old["game"]["game_id"]
    correction = grade(new, False)
    correction["at"] = "2026-11-12T12:00:00+00:00"
    records = [
        old,
        grade(old),
        {"kind": "retired", "instance_id": old["instance_id"]},
        new,
        grade(new),
        correction,
    ]
    out = forward_evaluation(records, plan)["stages"]["first"]
    assert out["settled"] == 1 and out["voids"] == 1
    assert out["raw_sleeper"]["scores"]["log_loss"]["model"] == pytest.approx(-math.log(0.2))


@pytest.mark.parametrize(
    "line,prices", [(-5, {"home": -110, "away": -110}), (-5.5, {"home": -150, "away": 130})]
)
def test_timing_failures_survive_line_and_price_rejections(plan, line, prices):
    f = forecast(plan)
    quote = {
        "market": "ats",
        "book": "draftkings",
        "quote_id": "q",
        "line": line,
        "prices": prices,
        "captured_at": "2026-11-11T16:00:00+00:00",
    }
    markets, picks = evaluate(
        probabilities(f["raw"], DEFAULTS),
        DEFAULTS,
        [quote],
        f["captured_at"],
        f["game"],
        plan["config"],
        "first",
    )
    assert markets[0]["unavailable_reason"] == "unpaired_quote" and picks == []
    # The reader must also reject old frozen records with the masked reason.
    markets[0]["unavailable_reason"] = "unsupported_line_increment"
    f["variants"]["adaptive"]["markets"] = markets
    assert _forecast_markets(f, "adaptive")["spread"] is None


def test_season_rollover_excludes_previous_season_live_games(root, plan):
    f = forecast(plan)
    result = {
        "kind": "result",
        "instance_id": f["instance_id"],
        "verified_at": "2026-11-12T12:00:00+00:00",
        "home_score": 120,
        "away_score": 110,
    }
    future = {"kind": "schedule", "game": {"season": 2027, "tip_utc": "2027-11-11T22:00:00+00:00"}}
    out = season_record([f, result, future], root, plan["config"])
    assert out["season"] == "2027–28" and out["pooled"]["n"] == 0
    assert out["evidence"] == "retrospective_research"
