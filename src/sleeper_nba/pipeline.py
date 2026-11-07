from __future__ import annotations

import json
import platform
import subprocess
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy
import scipy

from .common import ET, digest, dt, iso, utcnow
from .markets import evaluate, settle
from .models import (
    DEFAULTS,
    active_calibration,
    fit_parameters,
    probabilities,
    training_observations,
    weights,
)
from .projections import InvalidProjection, aggregate, eligible_stages
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


def assign_instance(game, records):
    previous = latest_schedules(records).get(game["game_id"])
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    terminal = any(x in game["status_name"] for x in ["POSTPONED", "CANCELED", "CANCELLED"])
    additions = []
    if previous:
        instance = previous["instance_id"]
        baseline = previous["baseline_tip"]
        major = (
            abs((dt(game["tip_utc"]) - dt(baseline)).total_seconds()) > 900
            or game["slate_date"] != dt(baseline).astimezone(ET).date().isoformat()
        )
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
                "game": game,
            }
        )
    return game, additions


def capture(root, config, dry_run=False, http=None, now=None):
    ledger = Ledger(root)
    http = http or HTTP()
    now = now or utcnow()
    records, additions, games = ledger.records(), [], []
    for date in dates_around(now):
        try:
            found, _ = schedule(http, date)
            for game, source_event in found:
                if game["competition_class"] != "regular":
                    continue
                game, new = assign_instance(game, records + additions)
                additions.extend(new)
                games.append((game, source_event))
        except SourceError as exc:
            additions.append(event("schedule_unavailable", date, message=str(exc)))
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
        return {"forecasts": 0, "status": "sleeper_unavailable", "events": len(additions)}
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
    forecast_count = 0
    for game, source_event in games:
        instance = game["instance_id"]
        all_records = records + additions
        existing = {
            r["stage"] for r in all_records if r["kind"] == "forecast" and r["instance_id"] == instance
        }
        target = dt(game["tip_utc"]) - timedelta(hours=config["lock_hours"])
        lock_end = target + timedelta(minutes=config["lock_tolerance_minutes"])
        if (
            now > lock_end
            and "lock" not in existing
            and not any(r["kind"] == "retired" and r["instance_id"] == instance for r in all_records)
        ):
            key = "missed_lock:" + instance
            if not any(r["id"] == key for r in all_records):
                additions.append(
                    {
                        "id": key,
                        "kind": "quality",
                        "code": "missed_lock",
                        "instance_id": instance,
                        "context": game["game_id"],
                        "at": iso(now),
                        "target": iso(target),
                        "window_end": iso(lock_end),
                    }
                )
        if game["status"] != "pre" or now >= dt(game["tip_utc"]):
            continue
        relevant = [
            e
            for e in envelopes
            if any(
                str(r.get("date", ""))[:10] == game["slate_date"]
                and r.get("game_id")
                and r.get("team") in {game["home"], game["away"]}
                for r in e["payload"]
            )
        ]
        captured_at = max((e["captured_at"] for e in relevant), default=iso(now))
        stages = eligible_stages(game, captured_at, existing, config)
        if not stages:
            continue
        try:
            raw = aggregate(game, rows, captured_at, config)
        except InvalidProjection as exc:
            # Repeated identical rejection does not grow the audit log forever.
            additions.append(event("invalid_projection", instance, reason=str(exc)))
            continue
        quotes = live_quotes.get(game["game_id"]) or espn_quotes(game, source_event)
        if utcnow() >= dt(game["tip_utc"]):
            additions.append(event("capture_finished_after_tip", instance))
            continue
        for stage in stages:
            calibration = active_calibration(all_records, stage, captured_at)
            variants = {}
            for variant, params in [("fixed", DEFAULTS), ("adaptive", calibration["params"])]:
                output = probabilities(raw, params)
                markets, selections = evaluate(output, params, quotes, captured_at, game, config, stage)
                variants[variant] = {
                    **output,
                    "params": dict(params),
                    "markets": markets,
                    "selections": selections,
                    "calibration_id": "fixed:v1.19" if variant == "fixed" else calibration["id"],
                    "training_count": 0 if variant == "fixed" else calibration["training_count"],
                    "training_cutoff": None if variant == "fixed" else calibration["training_cutoff"],
                }
            forecast = {
                "id": f"forecast:{instance}:{stage}",
                "kind": "forecast",
                "instance_id": instance,
                "stage": stage,
                "captured_at": captured_at,
                "created_at": iso(),
                "provenance": "native",
                "game": game,
                "lead_hours": (dt(game["tip_utc"]) - dt(captured_at)).total_seconds() / 3600,
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
    return {"forecasts": forecast_count, "records": len(additions), "dry_run": dry_run, "status": "ok"}


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
    for date in sorted(dates):
        try:
            found, _ = schedule(http, date)
        except SourceError as exc:
            additions.append(event("results_unavailable", date, message=str(exc)))
            continue
        for game, _ in found:
            if game["competition_class"] != "regular":
                continue
            game, new = assign_instance(game, records + additions)
            additions.extend(new)
            instance = game["instance_id"]
            if not game["completed"] or game["status"] != "post" or game["home_score"] == game["away_score"]:
                continue
            if not any(f["instance_id"] == instance for f in forecasts):
                continue
            old = latest_results.get(instance)
            if old and (old["home_score"], old["away_score"]) == (game["home_score"], game["away_score"]):
                continue
            result = {
                "id": "result:"
                + digest([instance, game["home_score"], game["away_score"], game["source_captured_at"]]),
                "kind": "result",
                "instance_id": instance,
                "verified_at": game["source_captured_at"],
                "home_score": game["home_score"],
                "away_score": game["away_score"],
                "source_hash": game["source_hash"],
                "supersedes": old["id"] if old else None,
                "includes_overtime": True,
            }
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
        variants = {}
        for variant, output in forecast["variants"].items():
            selections = []
            for index, s in enumerate(output["selections"]):
                grading = (
                    {"outcome": "void", "units": 0, "reason": retired[instance]["reason"]}
                    if instance in retired
                    else settle(s, result["home_score"], result["away_score"])
                )
                selections.append({**s, "selection_id": digest([forecast["id"], variant, index]), **grading})
            variants[variant] = {"selections": selections}
            if instance not in retired:
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
    return {"graded": sum(r["kind"] == "grade" for r in additions), "records": len(additions)}


def refit(root, config, now=None):
    ledger, now = Ledger(root), now or utcnow()
    records, additions = ledger.records(), []
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
    try:
        commit = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except subprocess.CalledProcessError:
        commit = "uncommitted"
    for stage in ["first", "lock"]:
        observations = [
            o
            for o in training_observations(records, stage, iso(now))
            if o["instance_id"] in allowed_instances
        ]
        previous = active_calibration(records, stage, iso(now))
        manifest_hash = digest(observations)
        if previous.get("manifest_hash") == manifest_hash:
            continue
        # One accepted version per UTC day. Rejections preserve prior components.
        if any(
            r["kind"] == "calibration" and r["stage"] == stage and dt(r["created_at"]).date() == now.date()
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
            if r["stage"] == "lock":
                c = r["config"]
                target = dt(r["game"]["tip_utc"]) - timedelta(hours=c["lock_hours"])
                if (
                    not target
                    <= dt(r["captured_at"])
                    <= target + timedelta(minutes=c["lock_tolerance_minutes"])
                ):
                    raise ValueError("Outside-window lock")
            for variant, output in r["variants"].items():
                if probabilities(r["raw"], output["params"])["wp_home"] != output["wp_home"]:
                    raise ValueError("Probability not reproducible")
                if (
                    variant == "adaptive"
                    and output["training_cutoff"]
                    and dt(output["training_cutoff"]) > dt(r["captured_at"])
                ):
                    raise ValueError("Calibration lookahead")
        elif r["kind"] == "calibration":
            if digest(r["config"]) != r["config_hash"] or dt(r["training_cutoff"]) > dt(r["created_at"]):
                raise ValueError("Invalid calibration provenance")
            for o in r["manifest"]:
                if dt(o["verified_at"]) > dt(r["training_cutoff"]):
                    raise ValueError("Training result unavailable at cutoff")
    return {"status": "valid", "records": len(records), "transactions": len(Ledger(root).transactions())}


def export_active(root):
    root = Path(root)
    records = Ledger(root).records()
    for stage in ["first", "lock"]:
        value = active_calibration(records, stage, iso())
        Ledger._atomic(root / "model" / f"active_{stage}.json", json.dumps(value, indent=2) + "\n")
