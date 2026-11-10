"""Read-only evaluation of the registered forward cohort against paired baselines."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

import numpy as np
from scipy.special import ndtr

from .common import digest, dt, lock_window_end
from .evaluation import latest_grades
from .markets import freshness, no_vig, valid_odds
from .matchup import season_of


def _losses(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    y = np.asarray(y, dtype=float)
    return np.column_stack((-(y * np.log(p) + (1 - y) * np.log1p(-p)), (p - y) ** 2))


def paired_scores(rows, plan):
    """Resample whole slates, preserving game pairs and unequal slate sizes.

    Each row is (slate, model_probability, baseline_probability, home_won).
    Intervals are descriptive, pointwise bootstrap intervals, not a sequential
    stopping rule or a correction for repeatedly checking the dashboard.
    """
    if not rows:
        return {"n": 0, "slates": 0, "scores": {}, "exploratory": True}
    model = _losses([r[1] for r in rows], [r[3] for r in rows])
    baseline = _losses([r[2] for r in rows], [r[3] for r in rows])
    difference = model - baseline
    clusters = defaultdict(list)
    for i, row in enumerate(rows):
        clusters[row[0]].append(i)
    indices = [clusters[key] for key in sorted(clusters)]
    intervals = [None, None]
    if len(indices) > 1:
        rng = np.random.default_rng(plan["bootstrap_seed"])
        # Cluster sums/counts give the game-weighted mean in each replicate.
        sums = np.array([difference[idx].sum(axis=0) for idx in indices])
        counts = np.array([len(idx) for idx in indices])
        chosen = rng.integers(0, len(indices), (plan["bootstrap_replicates"], len(indices)))
        samples = sums[chosen].sum(axis=1) / counts[chosen].sum(axis=1)[:, None]
        intervals = np.quantile(samples, [0.025, 0.975], axis=0).T.tolist()
    return {
        "n": len(rows),
        "slates": len(indices),
        "exploratory": len(indices) < plan["exploratory_slates"],
        "scores": {
            metric: {
                "model": float(model[:, i].mean()),
                "baseline": float(baseline[:, i].mean()),
                "model_minus_baseline": float(difference[:, i].mean()),
                "confidence_interval_95": intervals[i],
            }
            for i, metric in enumerate(["log_loss", "brier"])
        },
    }


def market_probability(forecast, plan):
    """Only its own frozen, contemporaneous, eligible two-sided moneyline."""
    for quote in forecast["variants"]["adaptive"].get("markets", []):
        if quote["market"] != "moneyline" or not quote.get("eligible"):
            continue
        if freshness(quote, forecast["captured_at"], plan["config"]):
            continue
        if dt(quote["captured_at"]) >= dt(forecast["game"]["tip_utc"]):
            continue
        if quote.get("provider_event_id") != forecast["game"]["game_id"]:
            continue
        if forecast["stage"] == "lock":
            target = dt(forecast["game"]["tip_utc"]) - timedelta(hours=plan["config"]["lock_hours"])
            if not target <= dt(quote["captured_at"]) <= lock_window_end(forecast["game"], plan["config"]):
                continue
        prices = quote.get("prices", {})
        if set(prices) != {"home", "away"} or not all(valid_odds(v) for v in prices.values()):
            continue
        return no_vig(prices["home"], prices["away"])[0]
    return None


def forward_evaluation(records, plan):
    if plan is None:
        return None
    grades = latest_grades(records)
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    stages = {}
    for stage in ["first", "lock"]:
        raw_pairs, market_pairs = [], []
        pending = excluded_config = voids = 0
        seen = set()
        forecasts = sorted(
            (r for r in records if r["kind"] == "forecast"),
            key=lambda r: (dt(r["captured_at"]), r["id"]),
        )
        for f in forecasts:
            if (
                f.get("provenance") != "native"
                or f["stage"] != stage
                or f.get("methodology", f.get("config", {}).get("methodology")) != plan["methodology"]
                or season_of(f["game"]) != plan["season"]
                or dt(f["captured_at"]) < dt(plan["starts_at"])
                or not dt(f["captured_at"]) < dt(f["game"]["tip_utc"])
                or f["game"]["status"] != "pre"
                or f["game"]["game_id"] in seen
            ):
                continue
            if digest(f["config"]) != plan["config_hash"] or f.get("config_hash") != plan["config_hash"]:
                excluded_config += 1
                continue
            grade = grades.get(f["id"])
            if f["instance_id"] in retired or (grade and grade["void"]):
                voids += 1
                continue
            seen.add(f["game"]["game_id"])
            if grade is None:
                pending += 1
                continue
            p = f["variants"]["adaptive"]["wp_home"]
            y = int(grade["result"]["home_score"] > grade["result"]["away_score"])
            raw = float(ndtr(f["raw"]["margin"] / plan["config"]["wp_initial"]))
            slate = f["game"]["slate_date"]
            raw_pairs.append((slate, p, raw, y))
            market = market_probability(f, plan)
            if market is not None:
                market_pairs.append((slate, p, market, y))
        stages[stage] = {
            "settled": len(raw_pairs),
            "pending": pending,
            "voids": voids,
            "excluded_config": excluded_config,
            "missing_market": len(raw_pairs) - len(market_pairs),
            "raw_sleeper": paired_scores(raw_pairs, plan),
            "market": paired_scores(market_pairs, plan),
        }
    return {
        "methodology": plan["methodology"],
        "starts_at": plan["starts_at"],
        "season": plan["season"],
        "config_hash": plan["config_hash"],
        "bootstrap_replicates": plan["bootstrap_replicates"],
        "exploratory_slates": plan["exploratory_slates"],
        "stages": stages,
    }
