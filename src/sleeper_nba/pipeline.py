from __future__ import annotations

import json
import math
import platform
import subprocess
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy
import scipy

from . import __version__, matchup
from .common import (
    ET,
    digest,
    dt,
    first_is_priced,
    first_window_open,
    iso,
    load_config,
    lock_primary_end,
    lock_timing,
    lock_window_end,
    utcnow,
)
from .markets import evaluate, settle
from .models import (
    DEFAULTS,
    active_calibration,
    calibration_stage,
    fit_parameters,
    probabilities,
    training_observations,
    weights,
)
from .projections import InvalidProjection, aggregate, eligible_stages, game_rows, public_player_snapshot
from .sources import HTTP, SourceError, dates_around, espn_quotes, schedule, sleeper_feed
from .storage import Ledger, event


def latest_schedules(records):
    result = {}
    for r in records:
        if r["kind"] == "schedule":
            key = r["game"]["game_id"]
            if key not in result or dt(r["at"]) > dt(result[key]["at"]):
                result[key] = r
    return result


def lock_outcomes(records, config, as_of):
    """Operational lock capture by slate, across all native versions."""
    # A game captured under multiple releases contributes one operational lock.
    forecasts = sorted(
        (
            f
            for f in records
            if f["kind"] == "forecast" and f.get("provenance") == "native" and dt(f["captured_at"]) <= as_of
        ),
        key=lambda f: (dt(f["captured_at"]), f["id"]),
    )
    retired = {r["instance_id"] for r in records if r["kind"] == "retired" and dt(r["at"]) <= as_of}
    locks, policies, late = {}, {}, {}
    for f in forecasts:
        policies[f["instance_id"]] = f["config"]
        if f["stage"] == "lock" and f["instance_id"] not in locks:
            locks[f["instance_id"]] = f["lead_hours"]
            late[f["instance_id"]] = f.get("lock_timing") == "late"
    schedules = [r for r in records if r["kind"] == "schedule" and dt(r["at"]) <= as_of]
    launch = min((dt(r["at"]) for r in schedules), default=None)
    recorded_ends = {
        r["instance_id"]: dt(r["window_end"])
        for r in records
        if r["kind"] == "quality" and r["code"] == "missed_lock" and dt(r["at"]) <= as_of
    }
    outcomes = {}
    for schedule_record in latest_schedules(schedules).values():
        game = schedule_record["game"]
        instance = schedule_record["instance_id"]
        if (
            game["competition_class"] != "regular"
            or instance in retired
            or any(status in game["status_name"] for status in ("POSTPONED", "CANCELED", "CANCELLED"))
        ):
            continue
        end = (
            dt(schedule_record["lock_window_end"])
            if schedule_record.get("lock_window_end")
            else recorded_ends.get(instance) or lock_window_end(game, policies.get(instance, config))
        )
        if end < launch:
            continue
        outcome = "locked" if instance in locks else "missed" if end < as_of else "pending"
        outcomes[instance] = {
            "instance_id": instance,
            "game_id": game["game_id"],
            "slate_date": game["slate_date"],
            "status": outcome,
            "elapsed": end < as_of,
            "lead_hours": locks.get(instance),
            "late": late.get(instance, False),
            "window_end": iso(end),
            "target": iso(
                dt(game["tip_utc"]) - timedelta(hours=policies.get(instance, config)["lock_hours"])
            ),
        }
    return outcomes


def missed_lock_event(outcome, at):
    return {
        "id": "missed_lock:" + outcome["instance_id"],
        "kind": "quality",
        "code": "missed_lock",
        "instance_id": outcome["instance_id"],
        "context": outcome["game_id"],
        "at": at,
        "target": outcome["target"],
        "window_end": outcome["window_end"],
    }


def assign_instance(game, records, config=None):
    previous = latest_schedules(records).get(game["game_id"])
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    terminal = any(x in game["status_name"] for x in ["POSTPONED", "CANCELED", "CANCELLED"])
    additions = []
    if previous:
        instance = previous["instance_id"]
        baseline = previous["baseline_tip"]
        shifted = (
            abs((dt(game["tip_utc"]) - dt(baseline)).total_seconds()) > 900
            or game["slate_date"] != dt(baseline).astimezone(ET).date().isoformat()
        )
        # A revised schedule retires the instance only while the game is still scheduled (§11).
        # Once it has started or finished, a changed start time is timing evidence, not a new
        # schedule: the played game keeps its instance and its grades, and the change is flagged.
        major = shifted and game["status"] == "pre"
        if shifted and not major and not terminal:
            flag = event(
                "tip_revision_after_start",
                instance,
                at=game["source_captured_at"],
                baseline_tip=baseline,
                reported_tip=game["tip_utc"],
                status=game["status_name"],
            )
            if all(r["id"] != flag["id"] for r in records):
                additions.append(flag)
        if (major or terminal) and instance not in retired:
            additions.append(
                {
                    "id": "retired:" + instance,
                    "kind": "retired",
                    "instance_id": instance,
                    "at": game["source_captured_at"],
                    "reason": "postponed_or_cancelled" if terminal else "major_tip_revision",
                }
            )
        if (major or instance in retired) and game["status"] == "pre" and not terminal:
            instance = game["game_id"] + ":" + digest([game["tip_utc"], game["schedule_revision"]])[:16]
            baseline = game["tip_utc"]
    else:
        baseline = game["tip_utc"]
        instance = game["game_id"] + ":" + digest([baseline])[:16]
    game = {**game, "instance_id": instance}
    if (
        not previous
        or previous["game"]["schedule_revision"] != game["schedule_revision"]
        or previous["instance_id"] != instance
    ):
        additions.append(
            {
                "id": "schedule:" + digest([instance, game["schedule_revision"]]),
                "kind": "schedule",
                "instance_id": instance,
                "at": game["source_captured_at"],
                "baseline_tip": baseline,
                **({"lock_window_end": iso(lock_window_end(game, config))} if config else {}),
                "game": game,
            }
        )
    return game, additions


def build_variants(raw, game, records, config, captured_at, stage, quotes, preview=False):
    calibration = active_calibration(records, stage, captured_at, config.get("methodology"), config)
    variants = {}
    for variant, params in [("fixed", DEFAULTS), ("adaptive", calibration["params"])]:
        scoring = {k: raw[k] for k in ["home_score", "away_score", "margin", "total"]}
        defense = None
        adjustment = None
        if variant == "adaptive" and matchup.enabled(config):
            defense = matchup.active(records, stage, captured_at, matchup.season_of(game), config)
            scoring, adjustment = matchup.apply(raw, game, defense, config["matchup"])
        output = probabilities(scoring, params)
        markets, selections = evaluate(output, params, quotes, captured_at, game, config, stage)
        variants[variant] = {
            **output,
            "params": dict(params),
            "scoring": scoring,
            "markets": markets,
            "selections": [] if preview else selections,
            **({"recommendations": selections} if preview else {}),
            "model_tag": config.get("methodology", "sleeper_only_v1_19")
            if variant == "adaptive"
            else "fixed:v1.19",
            "calibration_id": "fixed:v1.19" if variant == "fixed" else calibration["id"],
            "training_count": 0 if variant == "fixed" else calibration["training_count"],
            "training_cutoff": None if variant == "fixed" else calibration["training_cutoff"],
            "matchup_id": defense["id"] if defense else None,
            "matchup_training_count": defense["training_count"] if defense else 0,
            "matchup_adjustment": adjustment,
        }
    return variants


def capture(root, config, dry_run=False, http=None, now=None):
    ledger = Ledger(root)
    http = http or HTTP()
    supplied_clock = now is not None
    now = now or utcnow()
    records, additions, games = ledger.records(), [], []
    prepared = matchup.prepare_versions(records, root, config, iso(now))
    if prepared and not dry_run:
        ledger.append(prepared)
    records = records + prepared
    # Keep the last valid pregame snapshot even after tip or a failed refresh.
    previews = {
        p["instance_id"]: p
        for p in matchup.load_previews(root)["forecasts"]
        if dt(p["captured_at"]) < dt(p["game"]["tip_utc"])
    }
    schedule_failures = []
    for date in dates_around(now):
        try:
            found, _ = schedule(http, date)
            for game, source_event in found:
                if game["competition_class"] != "regular":
                    continue
                game, new = assign_instance(game, records + additions, config)
                additions.extend(new)
                games.append((game, source_event))
        except SourceError as exc:
            schedule_failures.append(date)
            additions.append(event("schedule_unavailable", date, message=str(exc)))
    outcomes = lock_outcomes(records + additions, config, now)
    additions.extend(missed_lock_event(o, iso(now)) for o in outcomes.values() if o["status"] == "missed")
    try:
        rows, envelopes, state, warnings = sleeper_feed(http)
        for warning in warnings:
            additions.append(event(warning, now.date().isoformat()))
    except SourceError as exc:
        additions.append(event("sleeper_unavailable", now.date().isoformat(), message=str(exc)))
        existing_ids = {r["id"] for r in records}
        additions = [r for r in additions if r["id"] not in existing_ids]
        if not dry_run:
            ledger.append(additions)
        return {
            "forecasts": 0,
            "status": "sleeper_unavailable",
            "schedule_failures": schedule_failures,
            "events": len(additions),
        }
    # ESPN is the sole market adapter. Its retrieval completion time is the
    # operational quote timestamp; sportsbook update time is not asserted.
    live_quotes = {g["game_id"]: espn_quotes(g, e) for g, e in games}
    for observed_game, _ in games:
        seconds_to_tip = (dt(observed_game["tip_utc"]) - utcnow()).total_seconds()
        if observed_game["status"] != "pre" or not 0 < seconds_to_tip <= 900:
            continue
        for quote in live_quotes.get(observed_game["game_id"], []):
            additions.append(
                {
                    **quote,
                    "id": "market:" + quote["quote_id"],
                    "kind": "market_observation",
                    "instance_id": observed_game["instance_id"],
                }
            )
    source_hashes = {
        str(p.relative_to(Path(root))): digest(p.read_text())
        for p in sorted((Path(root) / "src" / "sleeper_nba").glob("*.py"))
    }
    implementation = {
        "version": __version__,
        "source_tree_hash": digest(source_hashes),
        "source_hashes": source_hashes,
    }
    forecast_count = 0
    for game, source_event in games:
        instance = game["instance_id"]
        all_records = records + additions
        existing = {
            r["stage"]
            for r in all_records
            if r["kind"] == "forecast"
            and r["instance_id"] == instance
            and r.get("methodology", r.get("config", {}).get("methodology")) == config.get("methodology")
        }
        if game["status"] != "pre" or now >= dt(game["tip_utc"]):
            continue
        if any(
            r["kind"] == "forecast"
            and r["instance_id"] == instance
            and (r["game"]["home"], r["game"]["away"]) != (game["home"], game["away"])
            for r in all_records
        ):
            additions.append(event("changed_pregame_teams", instance))
            continue
        relevant = [e for e in envelopes if game_rows(game, e["payload"])]
        captured_at = max((e["captured_at"] for e in relevant), default=iso(now))
        stages = eligible_stages(game, captured_at, existing, config)
        if not stages and not matchup.enabled(config):
            continue
        inputs = game_rows(game, rows)
        try:
            raw = aggregate(game, inputs, captured_at, config)
        except InvalidProjection as exc:
            # Repeated identical rejection does not grow the audit log forever.
            additions.append(event("invalid_projection", instance, reason=str(exc), **exc.details))
            continue
        quotes = live_quotes.get(game["game_id"]) or espn_quotes(game, source_event)
        if (now if supplied_clock else utcnow()) >= dt(game["tip_utc"]):
            additions.append(event("capture_finished_after_tip", instance))
            continue
        if matchup.enabled(config):
            preview = {
                "id": "preview:" + instance,
                "kind": "preview",
                "instance_id": instance,
                "stage": "latest",
                "matchup_stage": "first",
                "game": game,
                "captured_at": captured_at,
                "methodology": config["methodology"],
                "lead_hours": (dt(game["tip_utc"]) - dt(captured_at)).total_seconds() / 3600,
                "raw": {k: raw[k] for k in ["home_score", "away_score", "margin", "total"]},
                "lineup": public_player_snapshot(raw["players"]),
                "variants": build_variants(
                    raw, game, all_records, config, captured_at, "first", quotes, True
                ),
            }
            previous = previews.get(instance)
            if previous is None or dt(captured_at) > dt(previous["captured_at"]):
                previews[instance] = preview
        if stages:
            # Keep the source evidence even when an unpriced first capture is deferred.
            candidate = {
                "kind": "snapshot_candidate",
                "game": game,
                "config": config,
                "captured_at": captured_at,
                "requests": [{k: e[k] for k in ("url", "requested_at", "captured_at")} for e in relevant],
                "raw_rows": inputs,
                "event": source_event,
            }
            candidate["id"] = "candidate:" + digest(candidate)
            additions.append(candidate)
        for stage in stages:
            variants = build_variants(raw, game, all_records, config, captured_at, stage, quotes)
            if stage == "first" and not first_window_open(
                game, config, captured_at, first_is_priced(variants)
            ):
                continue  # v1.27: wait for a qualifying moneyline until the lock target.
            forecast_id = f"forecast:{instance}:{stage}"
            if any(r["id"] == forecast_id for r in all_records):
                # Earlier methodologies remain immutable; a new version freezes
                # its own fresh pregame capture, never a relabeled old preview.
                forecast_id += ":" + config["methodology"]
            forecast = {
                "id": forecast_id,
                "kind": "forecast",
                "candidate_id": candidate["id"],
                "methodology": config.get("methodology", "sleeper_only_v1_19"),
                "instance_id": instance,
                "stage": stage,
                "captured_at": captured_at,
                "created_at": iso(),
                "provenance": "native",
                "implementation": implementation,
                "game": game,
                "lead_hours": (dt(game["tip_utc"]) - dt(captured_at)).total_seconds() / 3600,
                # v1.29: a lock after the on-time window is labelled late, with its real lead time.
                **(
                    {"lock_timing": lock_timing(game, config, captured_at)}
                    if stage == "lock" and lock_timing(game, config, captured_at)
                    else {}
                ),
                "raw": raw,
                "variants": variants,
                "config": config,
                "config_hash": digest(config),
                "source": {
                    "state": state,
                    "requests": [{k: e[k] for k in ["url", "requested_at", "captured_at"]} for e in relevant],
                },
                "market_availability": "qualifying"
                if any(m["eligible"] for m in variants["fixed"]["markets"])
                else "informational_or_unavailable",
                "market_note": "ESPN odds timestamped at fetch; sportsbook update time unavailable",
            }
            additions.append(forecast)
            forecast_count += 1
    # Retried generic events retain their original timestamp instead of conflicting.
    ids = {r["id"] for r in records}
    additions = [r for r in additions if r["id"] not in ids]
    if not dry_run:
        ledger.append(additions)
        if matchup.enabled(config):
            Ledger._atomic(
                Path(root) / "data" / "live_previews.json",
                json.dumps(
                    {
                        "updated_at": iso(now),
                        "purpose": "Untracked previews using first-stage parameters; no registered bets, grades or training observations",
                        "forecasts": list(previews.values()),
                    },
                    allow_nan=False,
                ),
            )
    return {
        "forecasts": forecast_count,
        "records": len(additions),
        "dry_run": dry_run,
        "schedule_failures": schedule_failures,
        "status": "schedule_unavailable" if schedule_failures else "ok",
    }


def next_wake(
    records,
    config,
    now,
    tick_seconds=600,
    idle_seconds=3600,
    active_hours_before_tip=8,
    active_hours_after_tip=6,
    retry_seconds=60,
    floor_seconds=15,
):
    """Seconds a watcher should sleep before its next run, read from the schedule.

    Wakes just after the earliest upcoming lock target and retries quickly while
    a lock window is open without an accepted lock. Otherwise it ticks: briskly
    from a few hours before a tip until that game has a verified result, and
    slowly when no game is near. The slow tick still refreshes the schedule.
    """
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    locked = {r["instance_id"] for r in records if r["kind"] == "forecast" and r["stage"] == "lock"}
    resolved = {r["instance_id"] for r in records if r["kind"] == "result"}
    games = [
        (r["game"], r["instance_id"])
        for r in latest_schedules(records).values()
        if r["game"]["competition_class"] == "regular" and r["instance_id"] not in retired
    ]
    before, after = timedelta(hours=active_hours_before_tip), timedelta(hours=active_hours_after_tip)
    # The ledger's game status can lag, so activity is bounded by time and results.
    active = any(
        dt(g["tip_utc"]) - before <= now <= dt(g["tip_utc"]) + after and i not in resolved for g, i in games
    )
    best = {
        "seconds": float(tick_seconds if active else max(idle_seconds, tick_seconds)),
        "reason": "tick" if active else "idle_tick",
        "game_id": None,
    }

    def consider(wait, reason, game):
        nonlocal best
        if wait < best["seconds"]:
            best = {"seconds": wait, "reason": reason, "game_id": game["game_id"]}

    for game, instance in games:
        tip = dt(game["tip_utc"])
        if now < tip - before:
            consider((tip - before - now).total_seconds(), "active_start", game)
        if game["status"] != "pre" or instance in locked or now > lock_window_end(game, config):
            continue
        target = tip - timedelta(hours=config["lock_hours"])
        if target <= now <= lock_primary_end(game, config):
            consider(float(retry_seconds), "lock_window_open", game)
        elif now > lock_primary_end(game, config):
            # v1.29 late window: the ordinary tick keeps trying without a minute-by-minute loop.
            consider(float(tick_seconds), "late_lock_window", game)
        else:
            # A few seconds past the target so the capture timestamp is inside the window.
            consider((target - now).total_seconds() + 5, "lock_target", game)
    return {**best, "seconds": max(int(floor_seconds), math.ceil(best["seconds"]))}


def grade_variants(forecast, result, retirement):
    variants = {}
    for variant, output in forecast["variants"].items():
        selections = []
        for index, s in enumerate(output["selections"]):
            grading = (
                {"outcome": "void", "units": 0, "reason": retirement["reason"]}
                if retirement
                else settle(s, result["home_score"], result["away_score"])
            )
            selections.append({**s, "selection_id": digest([forecast["id"], variant, index]), **grading})
        variants[variant] = {"selections": selections}
        if not retirement:
            actual_margin = result["home_score"] - result["away_score"]
            actual_total = result["home_score"] + result["away_score"]
            variants[variant].update(
                home_won=actual_margin > 0,
                winner_correct=None
                if output["winner"] is None
                else (output["winner"] == "home") == (actual_margin > 0),
                raw_margin_error=actual_margin - forecast["raw"]["margin"],
                raw_total_error=actual_total - forecast["raw"]["total"],
                ats_margin_error=actual_margin - output["ats_margin"],
                adjusted_total_error=actual_total - output["adjusted_total"],
            )
    return variants


def frozen_final(game, teams):
    """Orient explicitly reported scores to one consistent frozen matchup."""
    scores = {game["home"]: game["home_score"], game["away"]: game["away_score"]}
    if len(teams) != 1 or len(scores) != 2:
        raise ValueError("inconsistent_final_teams")
    home, away = next(iter(teams))
    if {home, away} != set(scores):
        raise ValueError("inconsistent_final_teams")
    if any(type(v) is not int or v < 0 for v in scores.values()):
        raise ValueError("incomplete_final_scores")
    return scores[home], scores[away]


def grade(root, config, http=None):
    ledger, http = Ledger(root), http or HTTP()
    records, additions = ledger.records(), []
    forecasts = [r for r in records if r["kind"] == "forecast"]
    # Re-verify recent finals for source corrections, plus all unresolved games.
    latest_results = {}
    for r in records:
        if r["kind"] == "result" and (
            r["instance_id"] not in latest_results
            or dt(r["verified_at"]) > dt(latest_results[r["instance_id"]]["verified_at"])
        ):
            latest_results[r["instance_id"]] = r
    dates = set()
    for f in forecasts:
        if f["instance_id"] not in latest_results or utcnow() - dt(f["game"]["tip_utc"]) < timedelta(days=7):
            dates.add(f["game"]["slate_date"])
    schedule_failures = []
    for date in sorted(dates):
        try:
            found, _ = schedule(http, date)
        except SourceError as exc:
            schedule_failures.append(date)
            additions.append(event("results_unavailable", date, message=str(exc)))
            continue
        for game, source_event in found:
            if game["competition_class"] != "regular":
                continue
            game, new = assign_instance(game, records + additions, config)
            additions.extend(new)
            instance = game["instance_id"]
            if not game["completed"] or game["status"] != "post":
                continue
            teams = {
                (f["game"]["home"], f["game"]["away"]) for f in forecasts if f["instance_id"] == instance
            }
            if not teams:
                continue
            try:
                home_score, away_score = frozen_final(game, teams)
            except ValueError as exc:
                additions.append(event(str(exc), instance, at=game["source_captured_at"]))
                continue
            if home_score == away_score:
                continue
            old = latest_results.get(instance)
            if old and (old["home_score"], old["away_score"]) == (home_score, away_score):
                continue
            result = {
                "id": "result:" + digest([instance, home_score, away_score, game["source_captured_at"]]),
                "kind": "result",
                "instance_id": instance,
                "verified_at": game["source_captured_at"],
                "home_score": home_score,
                "away_score": away_score,
                "source_hash": game["source_hash"],
                "supersedes": old["id"] if old else None,
                "includes_overtime": True,
            }
            evidence = {
                "kind": "result_evidence",
                "event": source_event,
                "requested_at": game["source_requested_at"],
                "captured_at": game["source_captured_at"],
                "url": game["source_url"],
            }
            evidence["id"] = "result-evidence:" + digest(evidence)
            result["evidence_id"] = evidence["id"]
            additions.append(evidence)
            latest_results[instance] = result
            additions.append(result)
    retired = {r["instance_id"]: r for r in records + additions if r["kind"] == "retired"}
    existing = {r["id"] for r in records}
    for forecast in forecasts:
        instance = forecast["instance_id"]
        result = latest_results.get(instance)
        if instance not in retired and result is None:
            continue
        revision = retired[instance]["id"] if instance in retired else result["id"]
        key = "grade:" + digest([forecast["id"], revision])
        if key in existing:
            continue
        variants = grade_variants(forecast, result, retired.get(instance))
        additions.append(
            {
                "id": key,
                "kind": "grade",
                "forecast_id": forecast["id"],
                "instance_id": instance,
                "at": iso(),
                "revision_id": revision,
                "void": instance in retired,
                "result": None if instance in retired else result,
                "variants": variants,
            }
        )
    additions = [r for r in additions if r["id"] not in existing]
    ledger.append(additions)
    return {
        "schedule_failures": schedule_failures,
        "graded": sum(r["kind"] == "grade" for r in additions),
        "records": len(additions),
    }


def confidence_history(records, root, config, stage, cutoff):
    # Only fully resolved slates enter daily calibration. Postponed/cancelled
    # games are removed; unfinished games keep their slate pending.
    schedules = latest_schedules(records)
    slates = defaultdict(list)
    for r in schedules.values():
        if r["game"]["competition_class"] == "regular":
            slates[r["game"]["slate_date"]].append(r["game"])
    completed = {
        date
        for date, games in slates.items()
        if all(
            g["completed"] or any(s in g["status_name"] for s in ["POSTPONED", "CANCELED", "CANCELLED"])
            for g in games
        )
    }
    allowed_instances = {
        g["instance_id"] for date, games in slates.items() if date in completed for g in games
    }
    observations = [
        o
        for o in training_observations(records, stage, cutoff, config.get("methodology"))
        if o["instance_id"] in allowed_instances
    ]
    if stage == "first" and config.get("calibration", {}).get("history") == "season":
        # v1.24: the season's earlier games, replayed walk-forward under this
        # methodology, start the history; a native game supersedes its replay.
        from .season import calibration_observations

        native_games = {o["game_id"] for o in observations}
        observations = sorted(
            [
                o
                for o in calibration_observations(records, root, config, cutoff)
                if o["game_id"] not in native_games
            ]
            + observations,
            key=lambda o: (dt(o["verified_at"]), o["game_id"]),
        )
    return observations


def refit(root, config, now=None):
    ledger, now = Ledger(root), now or utcnow()
    records, additions = ledger.records(), []
    prepared = matchup.prepare_versions(records, root, config, iso(now))
    ledger.append(prepared)
    records = records + prepared
    try:
        commit = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except subprocess.CalledProcessError:
        commit = "uncommitted"
    for stage in ["first", "lock"]:
        if calibration_stage(config, stage) != stage:
            continue  # v1.24: this stage is scored with another stage's calibration.
        observations = confidence_history(records, root, config, stage, iso(now))
        previous = active_calibration(records, stage, iso(now), config.get("methodology"))
        manifest_hash = digest(observations)
        if previous.get("manifest_hash") == manifest_hash:
            continue
        # One accepted version per UTC day. Rejections preserve prior components.
        if any(
            r["kind"] == "calibration"
            and r["stage"] == stage
            and dt(r["created_at"]).date() == now.date()
            and r.get("methodology", r.get("config", {}).get("methodology")) == config.get("methodology")
            for r in records
        ):
            continue
        if len(observations) < config["minimum_games"]:
            continue
        params, statuses, diagnostics = fit_parameters(observations, previous["params"], config)
        w, neff = weights(len(observations), config["half_life"])
        manifest = [
            {**o, "age": len(observations) - 1 - i, "weight": float(w[i])} for i, o in enumerate(observations)
        ]
        record = {
            "kind": "calibration",
            "methodology": config.get("methodology", "sleeper_only_v1_19"),
            "stage": stage,
            "created_at": iso(now),
            "training_cutoff": iso(now),
            "training_count": len(observations),
            "n_eff": neff,
            "manifest": manifest,
            "manifest_hash": manifest_hash,
            "params": params,
            "statuses": statuses,
            "diagnostics": diagnostics,
            "config": config,
            "config_hash": digest(config),
            "code_commit": commit,
            "runtime": {
                "python": platform.python_version(),
                "numpy": numpy.__version__,
                "scipy": scipy.__version__,
            },
            "previous_id": previous["id"],
        }
        record["id"] = "calibration:" + digest(record)
        additions.append(record)
    ledger.append(additions)
    return {"calibration_versions": len(additions)}


def validate(root):
    config = load_config(root)
    if matchup.enabled(config) and config["matchup"].get("seed_file"):
        matchup.seed_observations(root, config, "first", config["matchup"]["seed_season"], iso())
    records = Ledger(root).records()
    ids = [r["id"] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate immutable record IDs")
    for r in records:
        if r["kind"] == "forecast":
            if not dt(r["captured_at"]) < dt(r["game"]["tip_utc"]):
                raise ValueError("After-tip forecast")
            if r["variants"]["fixed"]["params"] != DEFAULTS:
                raise ValueError("Fixed control changed")
            if digest(r["config"]) != r["config_hash"]:
                raise ValueError("Forecast configuration hash mismatch")
            recomputed = aggregate(r["game"], r["raw"]["raw_rows"], r["captured_at"], r["config"])
            for name in ["home_score", "away_score", "margin", "total", "source_hash"]:
                if recomputed[name] != r["raw"][name]:
                    raise ValueError("Raw snapshot not reproducible")
            if r["stage"] == "first" and not first_window_open(
                r["game"], r["config"], r["captured_at"], first_is_priced(r["variants"])
            ):
                raise ValueError("First snapshot outside its first-snapshot policy")
            if r["stage"] == "lock":
                c = r["config"]
                target = dt(r["game"]["tip_utc"]) - timedelta(hours=c["lock_hours"])
                if not target <= dt(r["captured_at"]) <= lock_window_end(r["game"], c):
                    raise ValueError("Outside-window lock")
                if r.get("lock_timing") != lock_timing(r["game"], c, r["captured_at"]):
                    raise ValueError("Lock timing label does not match its capture time")
            for variant, output in r["variants"].items():
                scoring = output.get("scoring", r["raw"])
                if variant == "fixed" and any(
                    scoring[k] != r["raw"][k] for k in ["home_score", "away_score", "margin", "total"]
                ):
                    raise ValueError("Fixed control scoring changed")
                if output.get("matchup_id"):
                    versions = [v for v in records if v["id"] == output["matchup_id"]]
                    if versions:
                        v = versions[0]
                        if dt(v["created_at"]) > dt(r["captured_at"]) or v["stage"] != matchup.source_stage(
                            r["config"], r["stage"]
                        ):
                            raise ValueError("Matchup chronology or stage mismatch")
                        reproduced, _ = matchup.apply(r["raw"], r["game"], v)
                        if reproduced != scoring:
                            raise ValueError("Matchup score not reproducible")
                    elif not output["matchup_id"].startswith("matchup-defaults:"):
                        raise ValueError("Missing matchup version")
                if probabilities(scoring, output["params"])["wp_home"] != output["wp_home"]:
                    raise ValueError("Probability not reproducible")
                if (
                    variant == "adaptive"
                    and output["training_cutoff"]
                    and dt(output["training_cutoff"]) > dt(r["captured_at"])
                ):
                    raise ValueError("Calibration lookahead")
        elif r["kind"] == "matchup_calibration":
            if digest(r["manifest"]) != r["manifest_hash"] or digest(r["settings"]) != r["settings_hash"]:
                raise ValueError("Matchup manifest or settings hash mismatch")
            if dt(r["training_cutoff"]) > dt(r["created_at"]):
                raise ValueError("Matchup cutoff after creation")
            for o in r["manifest"]:
                if (
                    not dt(o["snapshot_utc"])
                    < dt(o["tip_utc"])
                    <= dt(o["verified_at"])
                    <= dt(r["training_cutoff"])
                ):
                    raise ValueError("Matchup observation lookahead")
            fitted = matchup.fit(r["manifest"], r["settings"], r["training_cutoff"])["params"]
            for key in ["global", "venue", "offense_control", "defense"]:
                a = list(fitted[key].values()) if isinstance(fitted[key], dict) else [fitted[key]]
                b = (
                    list(r["params"][key].values())
                    if isinstance(r["params"][key], dict)
                    else [r["params"][key]]
                )
                if isinstance(fitted[key], dict):
                    a = [fitted[key][t] for t in matchup.TEAMS_SORTED]
                    b = [r["params"][key][t] for t in matchup.TEAMS_SORTED]
                if not numpy.allclose(a, b, rtol=1e-10, atol=1e-10):
                    raise ValueError("Matchup fit not reproducible")
        elif r["kind"] == "calibration":
            if digest(r["config"]) != r["config_hash"] or dt(r["training_cutoff"]) > dt(r["created_at"]):
                raise ValueError("Invalid calibration provenance")
            for o in r["manifest"]:
                if dt(o["verified_at"]) > dt(r["training_cutoff"]):
                    raise ValueError("Training result unavailable at cutoff")
    from .audit import replay_ledger

    audit = replay_ledger(root, records)
    return {
        "audit": audit,
        "status": "valid",
        "records": len(records),
        "transactions": len(Ledger(root).transactions()),
    }


def export_active(root):
    root = Path(root)
    records = Ledger(root).records()
    for stage in ["first", "lock"]:
        config = load_config(root)
        value = active_calibration(records, stage, iso(), config.get("methodology"), config)
        Ledger._atomic(root / "model" / f"active_{stage}.json", json.dumps(value, indent=2) + "\n")
        if matchup.enabled(config):
            seasons = [r["season"] for r in records if r["kind"] == "matchup_calibration"]
            if seasons:
                defense = matchup.active(records, stage, iso(), max(seasons), config)
                Ledger._atomic(
                    root / "model" / f"active_defense_{stage}.json", json.dumps(defense, indent=2) + "\n"
                )
