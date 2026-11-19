"""Versioned, stage-specific residual-defense correction with calendar decay.

Offense is a nuisance control during estimation. Scoring uses only the global,
venue and opponent-defense coefficients. Prior strengths come from the frozen
configuration of each version; v1.21 raised the defensive prior from 5 to 40.

v1.23 (owner update, 10 November 2026): the correction applies from the first
graded game of a season (`minimum_games: 1`), locks score with the first-stage
fit (`stage_source`), and the fit refreshes once each slate is fully graded
(`refit: slate`). Configurations frozen without these keys keep the earlier
30-game minimum, stage-specific fits and one refit per UTC day.

v1.25 (owner update, 19 November 2026): the fitted offense terms are applied to
scores (`apply_offense: true`) and the defensive prior is 20. A fit record carries
its frozen settings, so every earlier record keeps the defense-only scoring.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path

import numpy as np

from .common import ET, TEAMS, digest, dt, iso

METHOD = "sleeper_only_v1_32"
# v1.31 (owner update, 19 November 2026): the team fit trains on game-day snapshots only.
GAME_DAY_HISTORY = "game_day_latest"
# A slate stops holding back a refit once its ungraded games are this old.
PENDING_HOURS = 24
TEAMS_SORTED = sorted(TEAMS)
TEAM_INDEX = {t: i for i, t in enumerate(TEAMS_SORTED)}


def enabled(config):
    return config.get("matchup", {}).get("enabled", False)


def source_stage(config, stage):
    """The stage whose fit scores a forecast of `stage` (v1.23: locks use first)."""
    return config.get("matchup", {}).get("stage_source", {}).get(stage, stage)


def fitted_stages(config):
    return [s for s in ["first", "lock"] if source_stage(config, s) == s]


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


def _pinned(root, file, sha256, label):
    path = Path(root) / file
    if not path.exists():
        raise ValueError(f"Configured matchup {label} file is missing")
    seed = json.loads(path.read_text())
    body = {k: v for k, v in seed.items() if k != "sha256"}
    if digest(body) != seed["sha256"] or seed["sha256"] != sha256:
        raise ValueError(f"Residual-defense {label} integrity failure")
    return seed


def backfill_files(root, config):
    """Owner-authorized outage backfills (17 November 2026), each hash-pinned in config."""
    return [
        _pinned(root, entry["file"], entry["sha256"], "backfill")
        for entry in config.get("matchup", {}).get("backfill_files", [])
    ]


def minutes_scale(minutes, settings):
    """v1.32 (`team_minutes_cap`): the factor that brings a team's projected minutes down to the cap.

    A team at or below the cap, or with no recorded minutes, keeps its projection (factor 1). The
    rule only ever scales down: a short team is usually short-handed, not under-counted.
    """
    cap = (settings or {}).get("team_minutes_cap")
    if cap is None or minutes is None or minutes <= cap:
        return 1.0
    return cap / minutes


def capped(observation, home_minutes, away_minutes, settings):
    """A copy of a scoring observation with its projected scores minutes-capped (v1.32)."""
    home, away = minutes_scale(home_minutes, settings), minutes_scale(away_minutes, settings)
    return {
        **observation,
        "projected_home_score": observation["projected_home_score"] * home,
        "projected_away_score": observation["projected_away_score"] * away,
        "home_minutes_scale": home,
        "away_minutes_scale": away,
    }


def seed_minutes(root, settings):
    """Pinned team minutes of the seed and backfill snapshots, keyed by (game, snapshot) (v1.32)."""
    entry = (settings or {}).get("seed_minutes")
    if not entry:
        return {}
    data = _pinned(root, entry["file"], entry["sha256"], "seed minutes")
    return {(r["game_id"], iso(dt(r["snapshot_utc"]))): r["team_minutes"] for r in data["rows"]}


def cap_seed(observations, root, settings):
    """Apply the v1.32 minutes cap to seed or backfill observations; unchanged without the key."""
    if (settings or {}).get("team_minutes_cap") is None:
        return observations
    minutes = seed_minutes(root, settings)
    out = []
    for o in observations:
        teams = minutes.get((o["game_id"], iso(dt(o["snapshot_utc"]))))
        if teams is None:
            raise ValueError(f"No pinned team minutes for seed game {o['game_id']}")
        out.append(capped(o, teams[o["home"]], teams[o["away"]], settings))
    return out


def seed_observations(root, config, stage, season, cutoff):
    if stage != "first" or not config.get("matchup", {}).get("seed_file"):
        return []
    seeds = [_pinned(root, config["matchup"]["seed_file"], config["matchup"]["seed_sha256"], "seed")]
    seeds += backfill_files(root, config)
    observations = [
        o
        for seed in seeds
        if dt(seed["created_at"]) <= dt(cutoff)
        for o in seed["observations"]
        if o["stage"] == stage and o["season"] == season and dt(o["verified_at"]) <= dt(cutoff)
    ]
    return cap_seed(observations, root, config.get("matchup"))


def on_game_day(snapshot_utc, tip_utc):
    """Captured on the game's Eastern calendar date."""
    return dt(snapshot_utc).astimezone(ET).date() == dt(tip_utc).astimezone(ET).date()


def eligible_observations(records, root, config, stage, season, cutoff):
    observations = {o["game_id"]: o for o in seed_observations(root, config, stage, season, cutoff)}
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    results = {}
    for r in records:
        if r["kind"] == "result" and dt(r["verified_at"]) <= dt(cutoff):
            previous = results.get(r["instance_id"])
            if previous is None or dt(r["verified_at"]) > dt(previous["verified_at"]):
                results[r["instance_id"]] = r
    policy = config.get("matchup", {}).get("history_snapshot")
    game_day = policy == GAME_DAY_HISTORY
    natives = [r for r in records if r["kind"] == "forecast"]
    if policy in ("latest", GAME_DAY_HISTORY):
        # v1.28: a game forecast under several tags trains on its latest first snapshot,
        # the one nearest tip and on the game day when one exists, as the season replay
        # does. Configurations without the key keep the ledger-order choice they were fitted with.
        natives.sort(key=lambda r: (dt(r["captured_at"]), r["id"]))
    if game_day:
        # v1.31: as the v1.30 season replay, only snapshots captured on the game's Eastern date
        # train the fit. A game-day lock stands in for an earlier-day first snapshot; a seed or
        # backfill snapshot must also be on game day; a game with neither is left out.
        observations = {k: o for k, o in observations.items() if on_game_day(o["snapshot_utc"], o["tip_utc"])}
    stages = (stage, "lock") if game_day and stage == "first" else (stage,)
    chosen = {}
    for f in natives:
        if f["stage"] not in stages or f.get("provenance") != "native":
            continue
        game = f["game"]
        if season_of(game) != season or f["instance_id"] in retired or f["instance_id"] not in results:
            continue
        if "home_score" not in f["raw"] or "home" not in game:
            continue
        if game_day:
            if not on_game_day(f["captured_at"], game["tip_utc"]):
                continue
            held = chosen.get(game["game_id"])
            if held is not None and held["stage"] == stage and f["stage"] != stage:
                continue  # A game-day first snapshot outranks a lock.
        chosen[game["game_id"]] = f
    for f in chosen.values():
        game = f["game"]
        r = results[f["instance_id"]]
        observation = {
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
        if game_day:
            observation["snapshot_stage"] = f["stage"]
        if config.get("matchup", {}).get("team_minutes_cap") is not None:
            minutes = f["raw"].get("team_minutes", {})
            observation = capped(
                observation, minutes.get(game["home"]), minutes.get(game["away"]), config["matchup"]
            )
        observations[game["game_id"]] = observation
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
        "backfill_count": sum(o["provenance"] == "gap-backfill-first" for o in observations),
        "native_count": sum(o["provenance"] == "native" for o in observations),
        "team_counts": team_counts,
        "weight_sum": float(w.sum()),
        "n_eff": float(w.sum() ** 2 / (w @ w)) if len(w) else 0.0,
        "weights": list(map(float, w)),
        "status": "fit" if len(observations) >= settings["minimum_games"] else "insufficient_history",
    }


def slate_pending(records, stage, season, cutoff):
    """True while a started slate still has an ungraded forecast game.

    A slate has started once one of its forecast games has tipped. Games
    ungraded more than PENDING_HOURS after tip no longer hold a refit back.
    """
    cutoff = dt(cutoff)
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    graded = {r["instance_id"] for r in records if r["kind"] == "result" and dt(r["verified_at"]) <= cutoff}
    games = [
        f["game"] | {"instance_id": f["instance_id"]}
        for f in records
        if f["kind"] == "forecast"
        and f["stage"] == stage
        and f.get("provenance") == "native"
        and f["instance_id"] not in retired
        and season_of(f["game"]) == season
    ]
    started = {g["slate_date"] for g in games if dt(g["tip_utc"]) <= cutoff}
    return any(
        g["slate_date"] in started
        and g["instance_id"] not in graded
        and dt(g["tip_utc"]) > cutoff - timedelta(hours=PENDING_HOURS)
        for g in games
    )


def active(records, stage, at, season, config):
    stage = source_stage(config, stage)
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
        for stage in fitted_stages(config):
            old = active(records, stage, cutoff, season, config)
            by_slate = config["matchup"].get("refit") == "slate"
            if by_slate:
                # An existing fit waits for the whole slate; a first fit does not.
                if old.get("created_at") and slate_pending(records, stage, season, cutoff):
                    continue
            elif old.get("created_at") and dt(old["created_at"]).date() == dt(cutoff).date():
                continue
            observations = eligible_observations(records, root, config, stage, season, cutoff)
            if by_slate and (
                digest(observations) == old.get("manifest_hash")
                or not (observations or old.get("created_at"))
            ):
                continue
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


def applies_offense(version, settings=None):
    """True when the fit's own frozen settings (else `settings`) apply offense (v1.25)."""
    frozen = version.get("settings") or settings or {}
    return bool(frozen.get("apply_offense", False))


def apply(raw, game, version, settings=None):
    """Correct raw scores with a fitted version.

    Through v1.24 offense controls are used in the fit only. From v1.25
    (`apply_offense`) each team's own offense term is added to its score too.
    """
    params = version["params"]
    h = params["global"] + 0.5 * params["venue"] + params["defense"][game["away"]]
    a = params["global"] - 0.5 * params["venue"] + params["defense"][game["home"]]
    offense = applies_offense(version, settings)
    if offense:
        h += params["offense_control"][game["home"]]
        a += params["offense_control"][game["away"]]
    home, away = raw["home_score"] + h, raw["away_score"] + a
    if not all(math.isfinite(x) and x >= 0 for x in [home, away]):
        raise ValueError("Invalid corrected score")
    adjustment = {
        "global": params["global"],
        "venue": params["venue"],
        "home_opponent_defense": params["defense"][game["away"]],
        "away_opponent_defense": params["defense"][game["home"]],
    }
    if offense:
        adjustment["home_offense"] = params["offense_control"][game["home"]]
        adjustment["away_offense"] = params["offense_control"][game["away"]]
    adjustment |= {"home_correction": h, "away_correction": a}
    return {"home_score": home, "away_score": away, "margin": home - away, "total": home + away}, adjustment


def load_previews(root):
    path = Path(root) / "data" / "live_previews.json"
    return json.loads(path.read_text()) if path.exists() else {"updated_at": None, "forecasts": []}
