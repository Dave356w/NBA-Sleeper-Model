from __future__ import annotations

import copy
import json
from datetime import timedelta

import numpy as np
import pytest
from test_pipeline import FixtureHTTP

from sleeper_nba import matchup
from sleeper_nba.common import dt, iso
from sleeper_nba.models import DEFAULTS, fit_parameters, training_observations
from sleeper_nba.pipeline import capture, validate
from sleeper_nba.storage import Ledger


def seed(root, config):
    return json.loads((root / config["matchup"]["seed_file"]).read_text())


def after_seed(root, config):
    data = seed(root, config)
    return max(dt(data["created_at"]), *(dt(o["verified_at"]) for o in data["observations"])) + timedelta(
        seconds=60
    )


def test_calendar_half_life_is_distinct_from_sample_shrinkage(config):
    observations = [{"tip_utc": "2026-11-01T12:00:00Z"}, {"tip_utc": "2026-11-26T12:00:00Z"}]
    weights = matchup.calendar_weights(observations, "2026-11-26T12:00:00Z", 25)
    assert weights.tolist() == [0.5, 1.0]
    with pytest.raises(ValueError, match="Future game"):
        matchup.calendar_weights(observations, "2026-11-25T12:00:00Z", 25)
    assert config["matchup"]["defense_prior"] == 40 and config["matchup"]["offense_prior"] == 20


def test_seed_is_first_only_known_before_use_and_hashed(root, config):
    at = after_seed(root, config)
    observations = matchup.seed_observations(root, config, "first", 2026, iso(at))
    assert len(observations) == 117
    assert not matchup.seed_observations(root, config, "lock", 2026, iso(at))
    assert not matchup.seed_observations(root, config, "first", 2025, iso(at))
    assert not matchup.seed_observations(root, config, "first", 2026, "2026-11-01T00:00:00Z")
    path = root / config["matchup"]["seed_file"]
    data = json.loads(path.read_text())
    data["observations"][0]["actual_home_score"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="integrity"):
        matchup.seed_observations(root, config, "first", 2026, iso(at))


def test_shrinkage_centers_team_effects_and_changes_magnitude(root, config):
    at = iso(after_seed(root, config))
    observations = matchup.seed_observations(root, config, "first", 2026, at)
    fitted = matchup.fit(observations, config["matchup"], at)
    stronger = matchup.fit(observations, {**config["matchup"], "defense_prior": 80}, at)
    assert abs(sum(fitted["params"]["defense"].values())) < 1e-8
    assert abs(sum(fitted["params"]["offense_control"].values())) < 1e-8
    assert fitted["seed_count"] == 117 and fitted["native_count"] == 0
    assert np.linalg.norm(list(stronger["params"]["defense"].values())) < np.linalg.norm(
        list(fitted["params"]["defense"].values())
    )
    assert matchup.fit(observations[:29], config["matchup"], at)["status"] == "insufficient_history"
    assert set(matchup.fit(observations[:29], config["matchup"], at)["params"]["defense"].values()) == {0.0}


def test_opponent_defense_is_applied_and_offense_control_is_not(game):
    params = {
        "global": -1.0,
        "venue": 2.0,
        "defense": dict.fromkeys(matchup.TEAMS_SORTED, 0),
        "offense_control": dict.fromkeys(matchup.TEAMS_SORTED, 100),
    }
    params["defense"]["NYK"] = -3
    params["defense"]["BOS"] = 2
    raw = {"home_score": 120, "away_score": 110, "margin": 10, "total": 230}
    scoring, adjustment = matchup.apply(raw, game, {"params": params})
    assert scoring == {"home_score": 117, "away_score": 110, "margin": 7, "total": 227}
    assert adjustment["home_opponent_defense"] == -3
    assert raw["margin"] == 10


def test_native_observation_replaces_seed_without_duplicate(root, config):
    at = after_seed(root, config)
    old = seed(root, config)["observations"][0]
    game = {
        "game_id": old["game_id"],
        "home": old["home"],
        "away": old["away"],
        "tip_utc": old["tip_utc"],
        "season": 2026,
    }
    forecast = {
        "id": "native-f",
        "kind": "forecast",
        "instance_id": "native-instance",
        "stage": "first",
        "provenance": "native",
        "game": game,
        "captured_at": old["snapshot_utc"],
        "raw": {"home_score": old["projected_home_score"], "away_score": old["projected_away_score"]},
    }
    result = {
        "id": "native-result",
        "kind": "result",
        "instance_id": "native-instance",
        "verified_at": iso(at),
        "home_score": old["actual_home_score"] + 1,
        "away_score": old["actual_away_score"],
    }
    obs = matchup.eligible_observations([forecast, result], root, config, "first", 2026, iso(at))
    assert len(obs) == 117
    assert next(r for r in obs if r["game_id"] == old["game_id"])["provenance"] == "native"
    result["verified_at"] = iso(at + timedelta(days=1))
    obs = matchup.eligible_observations([forecast, result], root, config, "first", 2026, iso(at))
    assert next(r for r in obs if r["game_id"] == old["game_id"])["provenance"] == "legacy-first"


def test_confidence_fits_only_new_method_frozen_corrected_targets():
    f = {
        "id": "f",
        "kind": "forecast",
        "instance_id": "g",
        "stage": "first",
        "provenance": "native",
        "captured_at": "2026-11-01T05:00:00Z",
        "game": {"game_id": "g", "tip_utc": "2026-11-01T23:00:00Z", "status": "pre"},
        "config": {"methodology": "sleeper_only_v1_20"},
        "raw": {"margin": 10, "total": 230},
        "variants": {"adaptive": {"scoring": {"margin": 6, "total": 226}}},
    }
    r = {
        "id": "r",
        "kind": "result",
        "instance_id": "g",
        "verified_at": "2026-11-02T03:00:00Z",
        "home_score": 120,
        "away_score": 110,
    }
    observations = training_observations([f, r], "first", "2026-11-02T04:00:00Z", "sleeper_only_v1_20")
    assert observations[0]["margin"] == 6
    assert observations[0]["ats_residual"] == observations[0]["totals_residual"] == 4
    assert not training_observations([f, r], "first", "2026-11-02T04:00:00Z", "sleeper_only_v1_19")
    assert not training_observations([f, r], "first", "2026-11-01T22:00:00Z", "sleeper_only_v1_20")


def test_no_second_global_bias_is_learned(config):
    observations = [
        {"margin": i % 10 - 5, "home_won": i % 3 != 0, "ats_residual": 4, "totals_residual": -5}
        for i in range(50)
    ]
    params, _, _ = fit_parameters(observations, DEFAULTS, config)
    assert params["ats_bias"] == params["totals_bias"] == 0
    assert params["ats_sigma"] > 0 and params["totals_sigma"] > 0


def test_prospective_transition_preserves_frozen_records_and_previews_do_not_bet(root, rows, config):
    http = FixtureHTTP(rows)
    old = {
        **config,
        "methodology": "sleeper_only_v1_19",
        "learn_scalar_biases": True,
        "matchup": {**config["matchup"], "enabled": False},
    }
    capture(root, old, http=http, now=dt(http.at))
    ledger = Ledger(root)
    original = copy.deepcopy(ledger.records("forecast"))
    at = after_seed(root, config)
    http.at = iso(at)
    result = capture(root, config, http=http, now=at)
    assert result["forecasts"] == 0
    assert ledger.records("forecast") == original
    preview = matchup.load_previews(root)["forecasts"][0]
    assert preview["methodology"] == matchup.METHOD == config["methodology"]
    assert preview["variants"]["adaptive"]["scoring"]["home_score"] != preview["raw"]["home_score"]
    assert preview["variants"]["fixed"]["scoring"]["home_score"] == preview["raw"]["home_score"]
    assert all(v["selections"] == [] for v in preview["variants"].values())
    assert validate(root)["status"] == "valid"
    versions = ledger.records("matchup_calibration")
    first = next(v for v in versions if v["stage"] == "first")
    lock = next(v for v in versions if v["stage"] == "lock")
    assert first["training_count"] == 117 and lock["training_count"] == 0
    count = len(ledger.records())
    capture(root, config, http=http, now=at)
    assert len(ledger.records()) == count


def test_failed_team_refit_preserves_last_accepted_version(root, config, monkeypatch):
    at = after_seed(root, config)
    versions = matchup.prepare_versions([], root, config, iso(at))
    accepted = matchup.active(versions, "first", iso(at), 2026, config)
    later = at + timedelta(days=1)

    def fail(*args, **kwargs):
        raise np.linalg.LinAlgError("fixture unstable fit")

    monkeypatch.setattr(matchup, "fit", fail)
    rejected = matchup.prepare_versions(versions, root, config, iso(later))
    assert rejected and all(r["kind"] == "quality" for r in rejected)
    assert matchup.active(versions + rejected, "first", iso(later), 2026, config)["id"] == accepted["id"]
    assert (
        matchup.active(versions + rejected, "first", iso(later), 2026, config)["params"] == accepted["params"]
    )


def test_v1_21_prior_shrinks_seed_corrections_and_starts_its_own_history(root, config):
    at = iso(after_seed(root, config))
    observations = matchup.seed_observations(root, config, "first", 2026, at)
    current = matchup.fit(observations, config["matchup"], at)["params"]["defense"]
    previous = matchup.fit(observations, {**config["matchup"], "defense_prior": 5.0}, at)["params"]["defense"]
    assert max(map(abs, current.values())) < 0.5 * max(map(abs, previous.values()))
    # A v1.20 version in the ledger is never reused for v1.21 scoring.
    old = {
        **config,
        "methodology": "sleeper_only_v1_20",
        "matchup": {**config["matchup"], "defense_prior": 5.0},
    }
    stale = matchup.prepare_versions([], root, old, at)
    assert stale and matchup.active(stale, "first", at, 2026, old)["training_count"] == 117
    assert matchup.active(stale, "first", at, 2026, config)["id"].startswith("matchup-defaults:")
    fresh = matchup.prepare_versions(stale, root, config, at)
    assert {v["stage"] for v in fresh} == {"first", "lock"}
    assert all(v["settings"]["defense_prior"] == 40 for v in fresh)
