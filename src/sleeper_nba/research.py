"""Replay native captures chronologically without leaking later verified results."""

from __future__ import annotations

import json
from collections import defaultdict

from .common import dt
from .evaluation import binary_scores, latest_grades
from .models import DEFAULTS, fit_parameters, probabilities, training_observations
from .storage import Ledger


def walk_forward(root, config):
    records = Ledger(root).records()
    grades = latest_grades(records)
    results = []
    params = {"first": dict(DEFAULTS), "lock": dict(DEFAULTS)}
    last_day, manifests = {}, {}
    for f in sorted((r for r in records if r["kind"] == "forecast"), key=lambda r: dt(r["captured_at"])):
        stage = f["stage"]
        obs = training_observations(records, stage, f["captured_at"])
        manifest = [o["result_id"] for o in obs]
        day = dt(f["captured_at"]).date().isoformat()
        if (
            manifest != manifests.get(stage)
            and last_day.get(stage) != day
            and len(obs) >= config["minimum_games"]
        ):
            params[stage], _, _ = fit_parameters(obs, params[stage], config)
            last_day[stage], manifests[stage] = day, manifest
        g = grades.get(f["id"])
        if not g or g["void"]:
            continue
        y = g["variants"]["fixed"]["home_won"]
        for variant, p in [("fixed", DEFAULTS), ("adaptive_replay", params[stage])]:
            probability = probabilities(f["raw"], p)["wp_home"]
            results.append(
                {
                    "forecast_id": f["id"],
                    "stage": stage,
                    "variant": variant,
                    "probability": probability,
                    "home_won": y,
                    "available_training_games": len(obs),
                    "params": dict(p),
                }
            )
    groups = defaultdict(list)
    for r in results:
        groups[r["stage"] + ":" + r["variant"]].append(r)
    report = {
        "label": "internal chronological replay; may refit after newly available games rather than full slates; not native forward evidence",
        "rows": results,
        "metrics": {
            k: {"n": len(v), **binary_scores([r["probability"] for r in v], [r["home_won"] for r in v])}
            for k, v in groups.items()
        },
    }
    Ledger._atomic(root / "research" / "output" / "walk_forward.json", json.dumps(report, indent=2))
    return {"replay_rows": len(results), "metrics": report["metrics"], "label": report["label"]}
