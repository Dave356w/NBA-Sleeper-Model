"""Versioned, stage-specific residual-defense correction with calendar decay.

Offense is a nuisance control during estimation. Scoring uses only the global,
venue and opponent-defense coefficients. Prior strengths come from the frozen
configuration of each version; v1.21 raised the defensive prior from 5 to 40.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .common import TEAMS, digest, dt

METHOD = "sleeper_only_v1_22"
TEAMS_SORTED = sorted(TEAMS)
TEAM_INDEX = {t: i for i, t in enumerate(TEAMS_SORTED)}


def enabled(config):
    return config.get("matchup", {}).get("enabled", False)


def season_of(game):
    return int(game.get("season") or (dt(game["tip_utc"]).year - (dt(game["tip_utc"]).month < 7)))


def features(scoring_team, opponent, is_home):
    n = len(TEAMS_SORTED)
    offense = np.full(n, -1 / n)
    defense = offense.copy()
    offense[TEAM_INDEX[scoring_team]] += 1
    defense[TEAM_INDEX[opponent]] += 1
    return np.r_[1.0, 0.5 if is_home else -0.5, offense, defense]


def calendar_weights(observations, cutoff, half_life_days):
    if not math.isfinite(half_life_days) or half_life_days <= 0:
        raise ValueError("Matchup half-life must be positive")
    ages = np.array([(dt(cutoff) - dt(o["tip_utc"])).total_seconds() / 86400 for o in observations])
    if np.any(ages < 0):
        raise ValueError("Future game in matchup history")
    return 2.0 ** (-ages / half_life_days)


def seed_observations(root, config, stage, season, cutoff):
    if stage != "first" or not config.get("matchup", {}).get("seed_file"):
        return []
    path = Path(root) / config["matchup"]["seed_file"]
    if not path.exists():
        raise ValueError("Configured matchup seed file is missing")
    seed = json.loads(path.read_text())
    body = {k: v for k, v in seed.items() if k != "sha256"}
    if digest(body) != seed["sha256"] or seed["sha256"] != config["matchup"]["seed_sha256"]:
        raise ValueError("Residual-defense seed integrity failure")
    if dt(seed["created_at"]) > dt(cutoff):
        return []
    return [
        o
        for o in seed["observations"]
        if o["stage"] == stage and o["season"] == season and dt(o["verified_at"]) <= dt(cutoff)
    ]


def eligible_observations(records, root, config, stage, season, cutoff):
    observations = {o["game_id"]: o for o in seed_observations(root, config, stage, season, cutoff)}
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    results = {}
    for r in records:
        if r["kind"] == "result" and dt(r["verified_at"]) <= dt(cutoff):
            previous = results.get(r["instance_id"])
            if previous is None or dt(r["verified_at"]) > dt(previous["verified_at"]):
                results[r["instance_id"]] = r
    for f in records:
        if f["kind"] != "forecast" or f["stage"] != stage or f.get("provenance") != "native":
            continue
        game = f["game"]
        if season_of(game) != season or f["instance_id"] in retired or f["instance_id"] not in results:
            continue
        if "home_score" not in f["raw"] or "home" not in game:
            continue
        r = results[f["instance_id"]]
        observations[game["game_id"]] = {
            "observation_id": f["id"],
            "game_id": game["game_id"],
            "stage": stage,
            "season": season,
            "home": game["home"],
            "away": game["away"],
            "snapshot_utc": f["captured_at"],
            "tip_utc": game["tip_utc"],
            "verified_at": r["verified_at"],
            "projected_home_score": f["raw"]["home_score"],
            "projected_away_score": f["raw"]["away_score"],
            "actual_home_score": r["home_score"],
            "actual_away_score": r["away_score"],
            "provenance": "native",
            "forecast_hash": digest(f),
            "result_id": r["id"],
            "result_hash": digest(r),
        }
    valid = []
    for o in observations.values():
        if not dt(o["snapshot_utc"]) < dt(o["tip_utc"]) <= dt(o["verified_at"]) <= dt(cutoff):
            raise ValueError("Invalid matchup observation chronology")
        if o["home"] not in TEAM_INDEX or o["away"] not in TEAM_INDEX or o["home"] == o["away"]:
            raise ValueError("Invalid matchup team identity")
        if any(
            not math.isfinite(o[k]) or o[k] < 0
            for k in [
                "projected_home_score",
                "projected_away_score",
                "actual_home_score",
                "actual_away_score",
            ]
        ):
            raise ValueError("Invalid matchup score")
        if o["actual_home_score"] == o["actual_away_score"]:
            continue
        valid.append(o)
    return sorted(valid, key=lambda o: (dt(o["tip_utc"]), o["game_id"]))


def fit(observations, settings, cutoff):
    nteams = len(TEAMS_SORTED)
    coefficients = np.zeros(2 + 2 * nteams)
    w = calendar_weights(observations, cutoff, settings["half_life_days"])
    if len(observations) >= settings["minimum_games"]:
        X, residual = [], []
        for o in observations:
            X.extend([features(o["home"], o["away"], True), features(o["away"], o["home"], False)])
            residual.extend(
                [
                    o["actual_home_score"] - o["projected_home_score"],
                    o["actual_away_score"] - o["projected_away_score"],
                ]
            )
        X = np.asarray(X)
        weights = np.repeat(w, 2)
        penalty = np.r_[
            settings["global_prior"],
            settings["venue_prior"],
            np.full(nteams, settings["offense_prior"]),
            np.full(nteams, settings["defense_prior"]),
        ]
        if np.any(penalty <= 0):
            raise ValueError("Live matchup priors must be positive")
        coefficients = np.linalg.solve(
            X.T @ (weights[:, None] * X) + np.diag(penalty), X.T @ (weights * np.asarray(residual))
        )
        if (
            not np.all(np.isfinite(coefficients))
            or np.max(np.abs(coefficients)) > settings["coefficient_bound"]
        ):
            raise ValueError("Unstable matchup coefficients")
    if abs(coefficients[2 : 2 + nteams].sum()) > 1e-8 or abs(coefficients[2 + nteams :].sum()) > 1e-8:
        raise ValueError("Uncentered team effects")
    params = {
        "global": float(coefficients[0]),
        "venue": float(coefficients[1]),
        "offense_control": dict(zip(TEAMS_SORTED, map(float, coefficients[2 : 2 + nteams]), strict=True)),
        "defense": dict(zip(TEAMS_SORTED, map(float, coefficients[2 + nteams :]), strict=True)),
    }
    team_counts = {t: sum(t in [o["home"], o["away"]] for o in observations) for t in TEAMS_SORTED}
    return {
        "params": params,
        "training_count": len(observations),
        "seed_count": sum(o["provenance"] == "legacy-first" for o in observations),
        "native_count": sum(o["provenance"] == "native" for o in observations),
        "team_counts": team_counts,
        "weight_sum": float(w.sum()),
        "n_eff": float(w.sum() ** 2 / (w @ w)) if len(w) else 0.0,
        "weights": list(map(float, w)),
        "status": "fit" if len(observations) >= settings["minimum_games"] else "insufficient_history",
    }


def active(records, stage, at, season, config):
    eligible = [
        r
        for r in records
        if r["kind"] == "matchup_calibration"
        and r["stage"] == stage
        and r["season"] == season
        and r["methodology"] == config["methodology"]
        and dt(r["created_at"]) <= dt(at)
        and dt(r["training_cutoff"]) <= dt(at)
    ]
    if eligible:
        return max(eligible, key=lambda r: dt(r["created_at"]))
    default = fit([], config["matchup"], at)
    return {
        **default,
        "id": f"matchup-defaults:{config['methodology']}:{season}:{stage}",
        "training_cutoff": None,
        "created_at": None,
        "stage": stage,
        "season": season,
    }


def prepare_versions(records, root, config, cutoff):
    if not enabled(config):
        return []
    seasons = {season_of(r["game"]) for r in records if r["kind"] in ["forecast", "schedule"]}
    if config["matchup"].get("seed_season"):
        seasons.add(int(config["matchup"]["seed_season"]))
    additions = []
    for season in sorted(seasons):
        for stage in ["first", "lock"]:
            old = active(records, stage, cutoff, season, config)
            if old.get("created_at") and dt(old["created_at"]).date() == dt(cutoff).date():
                continue
            observations = eligible_observations(records, root, config, stage, season, cutoff)
            try:
                fitted = fit(observations, config["matchup"], cutoff)
            except (ValueError, np.linalg.LinAlgError) as exc:
                from .storage import event

                rejection = event(
                    "matchup_fit_failed", f"{season}:{stage}:{dt(cutoff).date()}", at=cutoff, reason=str(exc)
                )
                if not any(r["id"] == rejection["id"] for r in records):
                    additions.append(rejection)
                continue
            body = {
                "kind": "matchup_calibration",
                "implementation_sha256": digest(Path(__file__).read_text()),
                "methodology": config["methodology"],
                "created_at": cutoff,
                "training_cutoff": cutoff,
                "stage": stage,
                "season": season,
                "settings": config["matchup"],
                "settings_hash": digest(config["matchup"]),
                "manifest": observations,
                "manifest_hash": digest(observations),
                **fitted,
            }
            body["id"] = "matchup:" + digest(body)
            additions.append(body)
    return additions


def apply(raw, game, version):
    params = version["params"]
    # Offense controls are used in the fit only; do not apply them to scoring.
    h = params["global"] + 0.5 * params["venue"] + params["defense"][game["away"]]
    a = params["global"] - 0.5 * params["venue"] + params["defense"][game["home"]]
    home, away = raw["home_score"] + h, raw["away_score"] + a
    if not all(math.isfinite(x) and x >= 0 for x in [home, away]):
        raise ValueError("Invalid corrected score")
    return {"home_score": home, "away_score": away, "margin": home - away, "total": home + away}, {
        "global": params["global"],
        "venue": params["venue"],
        "home_opponent_defense": params["defense"][game["away"]],
        "away_opponent_defense": params["defense"][game["home"]],
        "home_correction": h,
        "away_correction": a,
    }


def load_previews(root):
    path = Path(root) / "data" / "live_previews.json"
    return json.loads(path.read_text()) if path.exists() else {"updated_at": None, "forecasts": []}
