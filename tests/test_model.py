from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from sleeper_nba.common import digest, dt, team
from sleeper_nba.markets import (
    evaluate,
    expected_value,
    freshness,
    half_point,
    implied,
    no_vig,
    payout,
    settle,
)
from sleeper_nba.models import DEFAULTS, fit_parameters, probabilities, training_observations, weights
from sleeper_nba.projections import InvalidProjection, aggregate, eligible_stages
from sleeper_nba.storage import Ledger

AT = "2026-11-07T17:00:00+00:00"


def test_scoring_and_probabilities(game, rows, config):
    raw = aggregate(game, rows, AT, config)
    assert (raw["home_score"], raw["away_score"], raw["margin"], raw["total"]) == (120, 110, 10, 230)
    p = probabilities(raw, DEFAULTS)
    assert p["wp_home"] == pytest.approx(0.841344746)
    assert p["wp_home"] + p["wp_away"] == 1
    assert probabilities({"margin": 0, "total": 200}, DEFAULTS)["winner"] is None


@pytest.mark.parametrize("field,value", [("pts", None), ("pts", float("nan")), ("sp", -1), ("pts", True)])
def test_invalid_fields(game, rows, config, field, value):
    rows[0]["stats"][field] = value
    with pytest.raises(InvalidProjection):
        aggregate(game, rows, AT, config)


def test_missing_is_not_zero(game, rows, config):
    del rows[0]["stats"]["pts"]
    with pytest.raises(InvalidProjection):
        aggregate(game, rows, AT, config)


def test_empty_roster_placeholder_retained(game, rows, config):
    placeholder = copy.deepcopy(rows[0])
    placeholder.update(player_id="unprojected", stats={})
    result = aggregate(game, rows + [placeholder], AT, config)
    assert result["home_score"] == 120
    assert result["players"][-1]["points"] is None
    assert result["players"][-1]["projection_status"] == "unprojected"


def test_zero_minutes_inconsistent(game, rows, config):
    rows[0]["stats"]["sp"] = 0
    with pytest.raises(InvalidProjection):
        aggregate(game, rows, AT, config)
    rows[0]["stats"]["pts"] = 0
    result = aggregate(game, rows, AT, config)
    assert result["players"][0]["points"] == 0


def test_duplicates(game, rows, config):
    result = aggregate(game, rows + [rows[0]], AT, config)
    assert result["exact_duplicates_collapsed"] == 1
    changed = copy.deepcopy(rows[0])
    changed["stats"]["pts"] = 22
    with pytest.raises(InvalidProjection):
        aggregate(game, rows + [changed], AT, config)


def test_missing_team_and_ambiguous_game(game, rows, config):
    with pytest.raises(InvalidProjection):
        aggregate(game, rows[:8], AT, config)
    rows[0]["game_id"] = "other"
    with pytest.raises(InvalidProjection):
        aggregate(game, rows, AT, config)


@pytest.mark.parametrize(
    "at,expected",
    [
        ("16:59:59", ["first"]),
        ("17:00:00", ["first", "lock"]),
        ("17:15:00", ["first", "lock"]),
        ("17:15:01", ["first"]),
        ("22:00:00", []),
    ],
)
@pytest.mark.parametrize("policy", [None, "five_hours_15min_v2"])
def test_snapshot_window(game, config, at, expected, policy):
    # Unlabeled historical forecasts and the current policy share the narrow window.
    config = {**config, "lock_policy": policy}
    assert config["lock_tolerance_minutes"] == 15
    assert eligible_stages(game, "2026-11-07T" + at + "+00:00", set(), config) == expected
    assert not eligible_stages(game, "2026-11-07T" + at + "+00:00", {"first", "lock"}, config)


@pytest.mark.parametrize(
    "a,b",
    [
        ("CHO", "CHA"),
        ("BRK", "BKN"),
        ("PHO", "PHX"),
        ("GS", "GSW"),
        ("SA", "SAS"),
        ("NY", "NYK"),
        ("UTH", "UTA"),
    ],
)
def test_alias(a, b):
    assert team(a) == b


def quote(market, line=None, prices=None, **kw):
    return {
        "quote_id": "fixture-q",
        "market": market,
        "book": "draftkings",
        "line": line,
        "prices": prices
        or ({"over": -110, "under": -110} if market == "totals" else {"home": -110, "away": -110}),
        "captured_at": AT,
        "updated_at": AT,
        **kw,
    }


ALL_MARKETS = ["moneyline", "ats", "totals"]


def test_market_equations_and_signs(game, config):
    # Spread and totals series as registered before v1.22.
    config = {**config, "selection_markets": ALL_MARKETS}
    out = probabilities({"margin": 10, "total": 230}, DEFAULTS)
    markets, selections = evaluate(
        out, DEFAULTS, [quote("ats", -5.5), quote("totals", 225.5)], AT, game, config, "lock"
    )
    assert markets[0]["probabilities"]["home"] == pytest.approx(0.626, abs=0.001)
    assert markets[1]["probabilities"]["over"] == pytest.approx(0.5936103573)
    assert any(s["series"] == "edge_3plus" for s in selections)
    assert all(m["eligible"] for m in markets)


def test_integer_line_excluded(game, config):
    out = probabilities({"margin": 10, "total": 230}, DEFAULTS)
    markets, selections = evaluate(out, DEFAULTS, [quote("totals", 225)], AT, game, config, "first")
    assert not selections
    assert markets[0]["unavailable_reason"] == "unsupported_line_increment"
    assert "probabilities" not in markets[0]


def test_espn_fetch_timestamp_qualifies_without_provider_update(game, config):
    config = {**config, "selection_markets": ALL_MARKETS}
    out = probabilities({"margin": 10, "total": 230}, DEFAULTS)
    markets, selections = evaluate(
        out, DEFAULTS, [quote("ats", -5.5, updated_at=None)], AT, game, config, "first"
    )
    assert selections
    assert markets[0]["eligible"]
    assert markets[0]["updated_at"] is None
    assert "probabilities" in markets[0]


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"book": "other"}, "different_book"),
        ({"updated_at": "2026-11-07T16:40:00+00:00"}, "stale_quote"),
        ({"updated_at": "2026-11-07T17:02:00+00:00"}, "future_timestamp"),
        ({"captured_at": "2026-11-07T17:06:00+00:00"}, "unpaired_quote"),
    ],
)
def test_quote_freshness(config, changes, reason):
    assert freshness(quote("moneyline", **changes), AT, config) == reason


def test_moneyline_value_can_select_projected_loser(game, config):
    out = probabilities({"margin": 1, "total": 230}, DEFAULTS)
    _, selections = evaluate(
        out, DEFAULTS, [quote("moneyline", prices={"home": -300, "away": 250})], AT, game, config, "first"
    )
    assert any(s["side"] == "away" and s["series"] == "value" and s["ev"] > 0 for s in selections)
    assert any(s["side"] == "home" and s["series"] == "directional" for s in selections)


def test_odds_ev_and_payouts():
    assert implied(-200) == pytest.approx(2 / 3)
    assert implied(200) == pytest.approx(1 / 3)
    assert no_vig(-110, -110) == (0.5, 0.5)
    assert payout(200) == 2 and payout(-200) == 0.5
    assert expected_value(0.6, -110) == pytest.approx(0.6 * 100 / 110 - 0.4)
    assert expected_value(0.5, 100, push=0.1) == pytest.approx(0.1)
    assert half_point(225.5) and not half_point(225)
    with pytest.raises(ValueError):
        implied(-50)


@pytest.mark.parametrize(
    "market,side,line,h,a,outcome,units",
    [
        ("moneyline", "away", None, 100, 110, "win", 2),
        ("ats", "home", -5, 110, 105, "push", 0),
        ("ats", "away", -5.5, 110, 105, "win", 2),
        ("totals", "over", 215, 110, 105, "push", 0),
        ("totals", "under", 214.5, 110, 105, "loss", -1),
    ],
)
def test_grading(market, side, line, h, a, outcome, units):
    assert settle({"market": market, "side": side, "line": line, "odds": 200}, h, a) == {
        "outcome": outcome,
        "units": units,
    }


def observations(n=500):
    rng = np.random.default_rng(19)
    margins = rng.normal(0, 9, n)
    residuals = rng.normal(0, 12, n)
    return [
        {
            "margin": float(m),
            "home_won": bool(m + e > 0),
            "ats_residual": float(e),
            "totals_residual": float(t),
        }
        for m, e, t in zip(margins, residuals, rng.normal(-4, 19, n), strict=True)
    ]


def test_weight_age_and_persistent_prior():
    w, neff = weights(76)
    assert w[0] == 0.5 and w[-1] == 1
    assert neff == pytest.approx(w.sum() ** 2 / (w * w).sum())
    w, neff = weights(5000)
    assert float(w.sum()) == pytest.approx(108.702898, rel=0.001)
    assert neff == pytest.approx(216.405, rel=0.001)


def test_regularization_caps_and_independent_components(config):
    config = {**config, "learn_scalar_biases": True}  # Retain v1.19 failure semantics.
    accepted, statuses, diag = fit_parameters(observations(), DEFAULTS, config)
    assert statuses == {"wp": "accepted", "ats": "accepted", "totals": "accepted"}
    assert 9 <= accepted["wp_a"] <= 11
    assert abs(accepted["totals_bias"]) <= 1
    assert 17.1 <= accepted["totals_sigma"] <= 20.9
    assert 0 < diag["alpha"] < 1
    assert fit_parameters(observations(), DEFAULTS, config) == (accepted, statuses, diag)
    broken = observations()
    for o in broken:
        o["ats_residual"] = 1000
    accepted, statuses, _ = fit_parameters(broken, DEFAULTS, config)
    assert statuses["ats"] == "rejected_bias"
    assert accepted["ats_sigma"] == 14 and accepted["ats_bias"] == 0
    assert statuses["totals"] == "accepted"


def test_insufficient_history_and_flat_objective(config):
    assert fit_parameters(observations(29), DEFAULTS, config)[0] == DEFAULTS
    obs = observations(40)
    for o in obs:
        o["margin"] = 0
    p, status, _ = fit_parameters(obs, DEFAULTS, config)
    assert status["wp"] == "rejected_unidentified" and p["wp_a"] == 10


def test_no_lookahead_and_stage_separation():
    f = {
        "id": "f1",
        "kind": "forecast",
        "instance_id": "g1",
        "stage": "first",
        "provenance": "native",
        "captured_at": AT,
        "game": {"tip_utc": "2026-11-07T22:00:00+00:00", "status": "pre", "game_id": "g1"},
        "raw": {"margin": 10, "total": 230},
    }
    r = {
        "id": "r1",
        "kind": "result",
        "instance_id": "g1",
        "verified_at": "2026-11-08T01:00:00+00:00",
        "home_score": 120,
        "away_score": 110,
    }
    assert not training_observations([f, r], "first", "2026-11-08T00:59:59+00:00")
    obs = training_observations([f, r], "first", "2026-11-08T01:00:00+00:00")
    assert len(obs) == 1 and obs[0]["ats_residual"] == 0
    assert not training_observations([f, r], "lock", "2026-11-08T02:00:00+00:00")
    assert not training_observations(
        [f, r, {"kind": "retired", "instance_id": "g1"}], "first", "2026-11-08T02:00:00+00:00"
    )
    with pytest.raises(ValueError):
        dt("2026-11-07T17:00:00")


def test_atomic_immutable_storage(root):
    ledger = Ledger(root)
    record = {"kind": "test", "id": "immutable", "value": 1}
    assert ledger.append([record]) == 1
    assert ledger.append([record]) == 0
    assert ledger.records() == [record]
    with pytest.raises(ValueError):
        ledger.append([{**record, "value": 2}])
    path = next(ledger.path.glob("*.json"))
    path.write_text(path.read_text().replace('"value":1', '"value":2'))
    with pytest.raises(ValueError):
        ledger.records()
    assert not list(ledger.path.glob(".pending-*"))


def test_canonical_hash():
    assert digest({"b": 1, "a": 2}) == digest({"a": 2, "b": 1})
    assert math.isfinite(expected_value(0.5, -110))


@pytest.mark.parametrize(
    "at,locked",
    [
        ("16:59:59", False),
        ("17:00:00", True),
        ("17:15:01", True),
        ("21:59:59", True),
        ("22:00:00", False),
        ("22:00:01", False),
    ],
)
def test_full_pregame_lock_window(game, config, at, locked):
    # v1.20.1 forecasts keep the through-tip window they were frozen with.
    config = {**config, "lock_policy": "five_hours_to_tip_v1"}
    stages = eligible_stages(game, "2026-11-07T" + at + "+00:00", set(), config)
    assert ("lock" in stages) == locked


def test_registered_lock_policy_and_unknown_policy(game, config):
    from sleeper_nba.common import dt, lock_window_end

    assert config["lock_policy"] == "five_hours_15min_v2"
    assert lock_window_end(game, config) == dt("2026-11-07T17:15:00+00:00")
    with pytest.raises(ValueError, match="Unknown lock policy"):
        lock_window_end(game, {**config, "lock_policy": "typo"})


def test_v1_22_selects_moneyline_only_and_keeps_spread_and_total_as_context(game, config):
    assert config["selection_markets"] == ["moneyline"]
    out = probabilities({"margin": 10, "total": 230}, DEFAULTS)
    quotes = [
        quote("moneyline", None, prices={"home": -150, "away": 130}),
        quote("ats", -5.5),
        quote("totals", 225.5),
    ]
    markets, selections = evaluate(out, DEFAULTS, quotes, AT, game, config, "first")
    assert {s["market"] for s in selections} == {"moneyline"}
    assert {s["series"] for s in selections} == {"directional", "value"}
    assert [m["selects"] for m in markets] == [True, False, False]
    # The lines and the model's view of them stay available for forecast-versus-market checks.
    assert all(m["eligible"] and "probabilities" in m for m in markets)
    # A forecast frozen before v1.22 has no key and still registers every market.
    legacy = {k: v for k, v in config.items() if k != "selection_markets"}
    _, older = evaluate(out, DEFAULTS, quotes, AT, game, legacy, "first")
    assert {s["market"] for s in older} == {"moneyline", "ats", "totals"}
    with pytest.raises(ValueError, match="Unknown selection market"):
        evaluate(out, DEFAULTS, quotes, AT, game, {**config, "selection_markets": ["props"]}, "first")
