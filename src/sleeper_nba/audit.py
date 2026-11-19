"""Offline replay from source evidence and immutable, time-bounded ledger records."""

from __future__ import annotations

import math
from collections import Counter

from . import matchup
from .common import digest, dt, first_is_priced, first_window_open
from .models import active_calibration, fit_parameters, weights
from .pipeline import build_variants, confidence_history, frozen_final, grade_variants, latest_schedules
from .projections import aggregate, eligible_stages
from .sources import espn_quotes, parse_schedule


def matches(actual, expected):
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(matches(actual[k], v) for k, v in expected.items())
        )
    if isinstance(expected, (tuple, list)):
        return (
            isinstance(actual, (tuple, list))
            and len(actual) == len(expected)
            and all(matches(a, b) for a, b in zip(actual, expected))
        )
    if isinstance(expected, float):
        return isinstance(actual, (float, int)) and math.isclose(
            actual, expected, rel_tol=1e-12, abs_tol=1e-12
        )
    return actual == expected


def check(record, field, actual, expected):
    if not matches(actual, expected):
        raise ValueError(f"{record['id']}: {field} differs from cutoff replay")


def known_history(records, cutoff):
    stamps = {"forecast": "captured_at", "schedule": "at", "result": "verified_at", "retired": "at"}
    return [
        r
        for r in records
        if r["kind"] in stamps
        and dt(r[stamps[r["kind"]]]) <= dt(cutoff)
        and (r["kind"] != "forecast" or dt(r.get("created_at", r["captured_at"])) <= dt(cutoff))
    ]


def legacy_quotes(f):
    # Older captures retained the raw per-market ESPN structures, without a full event.
    fields = {"moneyline": "moneyline", "ats": "pointSpread", "totals": "total"}
    quotes = []
    for m in f["variants"]["fixed"]["markets"]:
        odds = {"provider": {"name": "DraftKings"}, fields[m["market"]]: m["raw"]}
        quotes.extend(espn_quotes(f["game"], {"competitions": [{"odds": [odds]}]}))
    return quotes


def event_game(record, event, requested_at, captured_at, url):
    if dt(requested_at) > dt(captured_at):
        raise ValueError(f"{record['id']}: invalid scoreboard request chronology")
    games = parse_schedule(
        {"payload": {"events": [event]}, "requested_at": requested_at, "captured_at": captured_at, "url": url}
    )
    if len(games) != 1:
        raise ValueError(f"{record['id']}: source event does not identify one game")
    return games[0][0]


def replay_fits(root, records, versions):
    for v in versions:
        at = v["training_cutoff"]
        history = known_history(records, at)
        if v["kind"] == "matchup_calibration":
            cfg = {"methodology": v["methodology"], "matchup": v["settings"]}
            obs = matchup.eligible_observations(history, root, cfg, v["stage"], v["season"], at)
            check(v, "team training history", obs, v["manifest"])
            check(v, "team training count", v["training_count"], len(obs))
            continue
        cfg = v["config"]
        previous = active_calibration(
            [p for p in versions if dt(p["created_at"]) < dt(v["created_at"])],
            v["stage"],
            at,
            cfg["methodology"],
            cfg,
        )
        check(v, "prior calibration", v["previous_id"], previous["id"])
        obs = confidence_history(history, root, cfg, v["stage"], at)
        w, neff = weights(len(obs), cfg["half_life"])
        manifest = [dict(o, age=len(obs) - 1 - i, weight=float(w[i])) for i, o in enumerate(obs)]
        check(v, "confidence training history", v["manifest"], manifest)
        params, statuses, _ = fit_parameters(obs, previous["params"], cfg)
        # Optimizers across supported SciPy builds can differ by a few parts in 1e8.
        if v["params"].keys() != params.keys() or any(
            not math.isclose(v["params"][k], value, rel_tol=5e-8, abs_tol=1e-12)
            for k, value in params.items()
        ):
            raise ValueError(f"{v['id']}: confidence fit differs from cutoff replay")
        original_obs = [
            {k: value for k, value in row.items() if k not in ("age", "weight")} for row in v["manifest"]
        ]
        check(v, "confidence manifest hash", v["manifest_hash"], digest(original_obs))
        check(v, "confidence statuses", v["statuses"], statuses)
        check(v, "training count", v["training_count"], len(obs))
        check(v, "effective sample size", v["n_eff"], neff)


def replay_forecasts(records, versions):
    forecasts = {}
    for f in (r for r in records if r["kind"] == "forecast"):
        key = (f["instance_id"], f["config"]["methodology"], f["stage"])
        if key in forecasts:
            raise ValueError(f"Duplicate frozen stage: {key}")
        forecasts[key] = f

    candidates = sorted(
        (r for r in records if r["kind"] == "snapshot_candidate"),
        key=lambda r: (dt(r["captured_at"]), r["id"]),
    )
    selected = {}
    for c in candidates:
        check(
            c, "candidate identity", c["id"], "candidate:" + digest({k: v for k, v in c.items() if k != "id"})
        )
        game, cfg, at = c["game"], c["config"], c["captured_at"]
        source = event_game(
            c, c["event"], game["source_requested_at"], game["source_captured_at"], game["source_url"]
        )
        check(c, "ESPN source event", {k: v for k, v in game.items() if k != "instance_id"}, source)
        if (
            dt(game["source_captured_at"]) > dt(at)
            or not c["requests"]
            or any(dt(e["requested_at"]) > dt(e["captured_at"]) for e in c["requests"])
            or max(dt(e["captured_at"]) for e in c["requests"]) != dt(at)
        ):
            raise ValueError(f"{c['id']}: candidate inputs unavailable at cutoff")
        raw = aggregate(game, c["raw_rows"], at, cfg)
        quotes = espn_quotes(game, c["event"])
        for stage in eligible_stages(game, at, set(), cfg):
            key = (game["instance_id"], cfg["methodology"], stage)
            if key in selected:
                continue
            f = forecasts.get(key)
            if f and "candidate_id" not in f and dt(f["captured_at"]) < dt(at):
                continue  # A stage already frozen before candidate journaling began.
            variants = build_variants(raw, game, versions, cfg, at, stage, quotes)
            if stage == "first" and not first_window_open(game, cfg, at, first_is_priced(variants)):
                continue
            if not f or f.get("candidate_id") != c["id"]:
                raise ValueError(f"{c['id']}: missing or incorrect earliest eligible {stage} forecast")
            check(f, "candidate game", f["game"], game)
            check(f, "candidate config", f["config"], cfg)
            check(f, "candidate timestamp", dt(f["captured_at"]), dt(at))
            check(f, "candidate source requests", f["source"]["requests"], c["requests"])
            check(f, "candidate projection", f["raw"], raw)
            selected[key] = variants

    for key, f in forecasts.items():
        cfg, at = f["config"], f["captured_at"]
        schedule = latest_schedules(
            r for r in records if r["kind"] == "schedule" and dt(r["at"]) <= dt(at)
        ).get(f["game"]["game_id"])
        if not schedule or schedule["instance_id"] != f["instance_id"]:
            raise ValueError(f"{f['id']}: game instance unavailable at snapshot")
        check(f, "schedule revision", f["game"]["schedule_revision"], schedule["game"]["schedule_revision"])
        check(f, "game instance", f["instance_id"], f["game"]["instance_id"])
        check(f, "lead time", f["lead_hours"], (dt(f["game"]["tip_utc"]) - dt(at)).total_seconds() / 3600)
        requests = f["source"]["requests"]
        if not requests or max(dt(e["captured_at"]) for e in requests) != dt(at):
            raise ValueError(f"{f['id']}: snapshot source timestamp mismatch")
        if any(dt(e["requested_at"]) > dt(e["captured_at"]) for e in requests):
            raise ValueError(f"{f['id']}: invalid request chronology")
        if dt(f["game"]["source_captured_at"]) > dt(at):
            raise ValueError(f"{f['id']}: future scoreboard")
        if "candidate_id" in f:
            if key not in selected:
                raise ValueError(f"{f['id']}: missing eligible snapshot candidate")
            variants = selected[key]
        else:
            raw = aggregate(f["game"], f["raw"]["raw_rows"], at, cfg)
            variants = build_variants(raw, f["game"], versions, cfg, at, f["stage"], legacy_quotes(f))
        for name, expected in variants.items():
            stored = f["variants"][name]
            if not matchup.enabled(cfg) and "scoring" not in stored:
                for k in (
                    "scoring",
                    "matchup_id",
                    "matchup_adjustment",
                    "matchup_training_count",
                    "model_tag",
                ):
                    expected.pop(k)
            if "candidate_id" not in f and "selection_markets" not in cfg:
                for m in expected["markets"]:
                    m.pop("selects", None)
            check(f, name + " forecast and price", stored, expected)

    return forecasts


def replay_grades(records, forecasts):
    by_id = {r["id"]: r for r in records}
    grades = [r for r in records if r["kind"] == "grade"]
    graded_revisions = {(g["forecast_id"], g["revision_id"]) for g in grades}
    results = [r for r in records if r["kind"] == "result"]
    schedules = {r["instance_id"]: r["game"]["game_id"] for r in records if r["kind"] == "schedule"}
    scoreboards = {r["game"]["source_hash"]: r["game"] for r in records if r["kind"] == "schedule"}
    teams = {}
    for f in forecasts.values():
        teams.setdefault(f["instance_id"], set()).add((f["game"]["home"], f["game"]["away"]))
    for r in results:
        archived = scoreboards.get(r["source_hash"])
        if archived:
            check(
                r,
                "frozen final scores",
                (r["home_score"], r["away_score"]),
                frozen_final(archived, teams[r["instance_id"]]),
            )
        if "evidence_id" in r:
            e = by_id[r["evidence_id"]]
            source = event_game(e, e["event"], e["requested_at"], e["captured_at"], e["url"])
            check(r, "final event game", source["game_id"], schedules[r["instance_id"]])
            check(r, "completed final", (source["completed"], source["status"]), (True, "post"))
            check(r, "result timestamp", r["verified_at"], e["captured_at"])
            check(r, "source hash", r["source_hash"], source["source_hash"])
            check(
                r,
                "frozen final scores",
                (r["home_score"], r["away_score"]),
                frozen_final(source, teams[r["instance_id"]]),
            )
    retirements = {r["instance_id"]: r for r in records if r["kind"] == "retired"}
    for f in forecasts.values():
        for r in results:
            if r["instance_id"] != f["instance_id"]:
                continue
            retirement = retirements.get(f["instance_id"])
            if retirement and dt(retirement["at"]) <= dt(r["verified_at"]):
                continue
            if (f["id"], r["id"]) not in graded_revisions:
                raise ValueError(f"{f['id']}: missing grade for {r['id']}")
    for g in grades:
        f = by_id[g["forecast_id"]]
        history = [r for r in records if r.get("instance_id") == f["instance_id"]]
        known = [r for r in history if r["kind"] == "result" and dt(r["verified_at"]) <= dt(g["at"])]
        retired = next((r for r in history if r["kind"] == "retired" and dt(r["at"]) <= dt(g["at"])), None)
        result = max(known, key=lambda r: dt(r["verified_at"]), default=None)
        revision = retired or result
        if not revision or g["revision_id"] != revision["id"]:
            raise ValueError(f"{g['id']}: grade revision unavailable or stale")
        check(g, "instance", g["instance_id"], f["instance_id"])
        check(g, "void", g["void"], bool(retired))
        check(g, "result", g["result"], None if retired else result)
        check(g, "grade", g["variants"], grade_variants(f, result, retired))
    return sum("evidence_id" not in r for r in results)


def replay_ledger(root, records):
    versions = [r for r in records if r["kind"] in ("matchup_calibration", "calibration")]
    replay_fits(root, records, versions)
    forecasts = replay_forecasts(records, versions)
    legacy_results = replay_grades(records, forecasts)
    return dict(
        Counter(
            r["kind"]
            for r in records
            if r["kind"] in ("grade", "forecast", "snapshot_candidate", "matchup_calibration", "calibration")
        ),
        legacy_results_without_raw_event=legacy_results,
        legacy_without_candidate_history=sum("candidate_id" not in f for f in forecasts.values()),
    )
