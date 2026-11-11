from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import log_ndtr, ndtr

from .common import digest, dt

DEFAULTS = {"wp_a": 10.0, "ats_bias": 0.0, "ats_sigma": 14.0, "totals_bias": 0.0, "totals_sigma": 19.0}


def probabilities(raw, params):
    p = float(ndtr(raw["margin"] / params["wp_a"]))
    return {
        "wp_home": p,
        "wp_away": 1 - p,
        "winner": "home" if raw["margin"] > 0 else "away" if raw["margin"] < 0 else None,
        "ats_margin": raw["margin"] + params["ats_bias"],
        "adjusted_total": raw["total"] + params["totals_bias"],
    }


def weights(n, half_life=75):
    # Observations are ordered oldest -> newest, latest age zero.
    w = 2.0 ** (-np.arange(n - 1, -1, -1, dtype=float) / half_life)
    neff = float(w.sum() ** 2 / np.square(w).sum()) if n else 0.0
    return w, neff


def fit_parameters(observations, previous, config):
    n = len(observations)
    statuses = {}
    accepted = dict(previous)
    diagnostics = {}
    if n < config["minimum_games"]:
        return dict(DEFAULTS), {"all": "insufficient_history"}, {"n": n, "n_eff": weights(n)[1]}
    w, neff = weights(n, config["half_life"])
    alpha = neff / (neff + config["prior_strength"])
    margins = np.array([o["margin"] for o in observations], dtype=float)
    winners = np.array([o["home_won"] for o in observations], dtype=bool)
    bounds = config["wp_bounds"]

    def objective(log_a):
        z = margins / np.exp(log_a)
        return float(-np.sum(w * np.where(winners, log_ndtr(z), log_ndtr(-z))))

    grid = np.linspace(math.log(bounds[0]), math.log(bounds[1]), 101)
    losses = [objective(x) for x in grid]
    fit = minimize_scalar(
        objective, bounds=(grid[0], grid[-1]), method="bounded", options={"xatol": 1e-10, "maxiter": 1000}
    )
    fitted = math.exp(fit.x)
    diagnostics.update(
        n=n,
        n_eff=neff,
        weight_sum=float(w.sum()),
        alpha=alpha,
        wp_fit=fitted,
        wp_objective=fit.fun,
        wp_grid_range=max(losses) - min(losses),
    )
    if not fit.success or not math.isfinite(fitted):
        statuses["wp"] = "rejected_optimizer"
    elif max(losses) - min(losses) < 1e-8:
        statuses["wp"] = "rejected_unidentified"
    elif min(fitted - bounds[0], bounds[1] - fitted) <= 0.001:
        statuses["wp"] = "rejected_boundary"
    else:
        target = math.exp((1 - alpha) * math.log(10) + alpha * math.log(fitted))
        move = config["daily_scale_movement"]
        accepted["wp_a"] = float(
            np.clip(target, previous["wp_a"] * (1 - move), previous["wp_a"] * (1 + move))
        )
        diagnostics["wp_target"] = target
        statuses["wp"] = "accepted"
    for component, initial_sigma in [("ats", 14.0), ("totals", 19.0)]:
        residual = np.array([o[component + "_residual"] for o in observations], dtype=float)
        bias_target = (
            alpha * float(np.average(residual, weights=w)) if config.get("learn_scalar_biases", True) else 0.0
        )
        bmin, bmax = config[component + "_bias_bounds"]
        diagnostics[component + "_bias_target"] = bias_target
        if not math.isfinite(bias_target) or not bmin <= bias_target <= bmax:
            statuses[component] = "rejected_bias"
            continue
        bias = float(
            np.clip(
                bias_target,
                previous[component + "_bias"] - config["daily_bias_movement"],
                previous[component + "_bias"] + config["daily_bias_movement"],
            )
        )
        if not config.get("learn_scalar_biases", True):
            bias = 0.0  # Global/venue mean correction is already in matchup scoring.
        sigma_target = math.sqrt(
            (config["prior_strength"] * initial_sigma**2 + float(np.sum(w * (residual - bias) ** 2)))
            / (config["prior_strength"] + float(w.sum()))
        )
        diagnostics[component + "_sigma_target"] = sigma_target
        smin, smax = config[component + "_sigma_bounds"]
        if not math.isfinite(sigma_target) or not smin <= sigma_target <= smax:
            statuses[component] = "rejected_sigma"
            continue
        move = config["daily_scale_movement"]
        accepted[component + "_bias"] = bias
        accepted[component + "_sigma"] = float(
            np.clip(
                sigma_target,
                previous[component + "_sigma"] * (1 - move),
                previous[component + "_sigma"] * (1 + move),
            )
        )
        statuses[component] = "accepted"
    return accepted, statuses, diagnostics


def training_observations(records, stage, cutoff, methodology=None):
    results = {}
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    for r in records:
        if r["kind"] == "result" and dt(r["verified_at"]) <= dt(cutoff):
            key = r["instance_id"]
            if key not in results or dt(r["verified_at"]) > dt(results[key]["verified_at"]):
                results[key] = r
    observations = {}
    for forecast in records:
        if (
            forecast["kind"] != "forecast"
            or forecast["stage"] != stage
            or forecast.get("provenance") != "native"
        ):
            continue
        if methodology and forecast.get("config", {}).get("methodology", "sleeper_only_v1_19") != methodology:
            continue
        scoring = forecast.get("variants", {}).get("adaptive", {}).get("scoring", forecast["raw"])
        if not methodology:
            scoring = forecast["raw"]
        instance = forecast["instance_id"]
        if instance in retired or instance not in results:
            continue
        result = results[instance]
        if not dt(forecast["captured_at"]) < dt(forecast["game"]["tip_utc"]) <= dt(result["verified_at"]):
            continue
        if forecast["game"]["status"] != "pre" or result["home_score"] == result["away_score"]:
            continue
        # Stage forecast contains both variants, so one raw observation per game.
        observations[instance] = {
            "instance_id": instance,
            "forecast_id": forecast["id"],
            "forecast_hash": digest(forecast),
            "result_id": result["id"],
            "result_hash": digest(result),
            "verified_at": result["verified_at"],
            "game_id": forecast["game"]["game_id"],
            "margin": scoring["margin"],
            "home_won": result["home_score"] > result["away_score"],
            "ats_residual": result["home_score"] - result["away_score"] - scoring["margin"],
            "totals_residual": result["home_score"] + result["away_score"] - scoring["total"],
        }
    return sorted(observations.values(), key=lambda x: (dt(x["verified_at"]), x["game_id"]))


def calibration_stage(config, stage):
    """The stage whose confidence calibration scores `stage` (v1.24: locks use first)."""
    return (config or {}).get("calibration", {}).get("stage_source", {}).get(stage, stage)


def active_calibration(records, stage, at, methodology=None, config=None):
    stage = calibration_stage(config, stage)
    eligible = [
        r
        for r in records
        if r["kind"] == "calibration"
        and r["stage"] == stage
        and dt(r["created_at"]) <= dt(at)
        and dt(r["training_cutoff"]) <= dt(at)
        and (
            not methodology
            or r.get("methodology", r.get("config", {}).get("methodology", "sleeper_only_v1_19"))
            == methodology
        )
    ]
    return (
        max(eligible, key=lambda x: dt(x["created_at"]))
        if eligible
        else {
            "id": "defaults:" + stage,
            "params": dict(DEFAULTS),
            "training_count": 0,
            "training_cutoff": None,
            "n_eff": 0.0,
        }
    )
