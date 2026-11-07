from __future__ import annotations

from collections import defaultdict

import numpy as np

from .common import dt
from .markets import no_vig


def latest_grades(records):
    latest = {}
    for r in records:
        if r["kind"] == "grade" and (
            r["forecast_id"] not in latest or dt(r["at"]) > dt(latest[r["forecast_id"]]["at"])
        ):
            latest[r["forecast_id"]] = r
    return latest


def binary_scores(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    y = np.asarray(y, dtype=float)
    return {
        "log_loss": float(np.mean(-(y * np.log(p) + (1 - y) * np.log1p(-p)))),
        "brier": float(np.mean((p - y) ** 2)),
    }


def error_scores(values):
    x = np.asarray(values, dtype=float)
    return {"mae": float(np.mean(abs(x))), "rmse": float(np.sqrt(np.mean(x * x))), "bias": float(np.mean(x))}


def summarize(records):
    grades = latest_grades(records)
    groups = defaultdict(list)
    bets = defaultdict(list)
    errors = defaultdict(list)
    forecasts = sorted((r for r in records if r["kind"] == "forecast"), key=lambda r: dt(r["captured_at"]))
    for f in forecasts:
        if f["id"] not in grades:
            continue
        g = grades[f["id"]]
        for variant, output in f["variants"].items():
            key = f["stage"] + ":" + variant
            for selection in g["variants"][variant]["selections"]:
                bets[
                    key + ":" + selection["book"] + ":" + selection["market"] + ":" + selection["series"]
                ].append(selection)
            if g["void"]:
                continue
            y = int(g["variants"][variant]["home_won"])
            groups[key].append((f, output, g["variants"][variant], y))
            for m in output["markets"]:
                if not m["eligible"] or m["market"] == "moneyline":
                    continue
                actual = (
                    g["result"]["home_score"] - g["result"]["away_score"] + m["line"]
                    if m["market"] == "ats"
                    else g["result"]["home_score"] + g["result"]["away_score"] - m["line"]
                )
                if actual:
                    first = "home" if m["market"] == "ats" else "over"
                    errors[key + ":" + m["market"]].append((m["probabilities"][first], int(actual > 0)))
    performance = {}
    for key, rows in groups.items():
        ps, ys = [r[1]["wp_home"] for r in rows], [r[3] for r in rows]
        correct = [r[2]["winner_correct"] for r in rows if r[2]["winner_correct"] is not None]
        reliability = []
        for i in range(10):
            indices = [j for j, p in enumerate(ps) if i / 10 <= p < (i + 1) / 10 or (i == 9 and p == 1)]
            if indices:
                reliability.append(
                    {
                        "bin": i,
                        "n": len(indices),
                        "predicted": float(np.mean([ps[j] for j in indices])),
                        "actual": float(np.mean([ys[j] for j in indices])),
                    }
                )
        bands = []
        for low, high in [(0, 3), (3, 7), (7, 12), (12, float("inf"))]:
            subset = [
                r
                for r in rows
                if low <= abs(r[0]["raw"]["margin"]) < high and r[2]["winner_correct"] is not None
            ]
            if subset:
                bands.append(
                    {
                        "band": f"{low}–{high if high != float('inf') else '+'}",
                        "n": len(subset),
                        "accuracy": float(np.mean([r[2]["winner_correct"] for r in subset])),
                    }
                )
        monthly = defaultdict(list)
        for r in rows:
            monthly[r[0]["game"]["slate_date"][:7]].append(r)
        performance[key] = {
            "n": len(rows),
            "winner_count": len(correct),
            "correct": sum(correct),
            "accuracy": float(np.mean(correct)) if correct else None,
            **binary_scores(ps, ys),
            "expected_home_wins": sum(ps),
            "actual_home_wins": sum(ys),
            "margin": error_scores([r[2]["ats_margin_error"] for r in rows]),
            "total": error_scores([r[2]["adjusted_total_error"] for r in rows]),
            "raw_margin": error_scores([r[2]["raw_margin_error"] for r in rows]),
            "raw_total": error_scores([r[2]["raw_total_error"] for r in rows]),
            "reliability": reliability,
            "margin_bands": bands,
            "monthly": {
                month: {"n": len(v), **binary_scores([r[1]["wp_home"] for r in v], [r[3] for r in v])}
                for month, v in monthly.items()
            },
        }
    betting = {}
    for key, selections in bets.items():
        settled = [s for s in selections if s["outcome"] != "void"]
        units = sum(s["units"] for s in settled)
        betting[key] = {
            "n": len(settled),
            "units": units,
            "roi": units / len(settled) if settled else None,
            "wins": sum(s["outcome"] == "win" for s in settled),
            "losses": sum(s["outcome"] == "loss" for s in settled),
            "pushes": sum(s["outcome"] == "push" for s in settled),
            "voids": sum(s["outcome"] == "void" for s in selections),
            "cumulative_units": np.cumsum([s["units"] for s in settled]).tolist(),
            "sides": {
                side: {
                    "n": sum(s["side"] == side for s in settled),
                    "units": sum(s["units"] for s in settled if s["side"] == side),
                }
                for side in sorted({s["side"] for s in settled})
            },
            "favorites": {
                str(fav): {
                    "n": sum(s["favorite"] == fav for s in settled),
                    "units": sum(s["units"] for s in settled if s["favorite"] == fav),
                }
                for fav in [True, False]
            },
        }
    for key, pairs in errors.items():
        performance.setdefault(key, {}).update(
            n=len(pairs), **binary_scores([p for p, _ in pairs], [y for _, y in pairs])
        )
    paired = paired_comparisons(groups)
    return {"forecast": performance, "betting": betting, "paired": paired}


def paired_comparisons(groups):
    result = {}
    for stage in ["first", "lock"]:
        fixed, adaptive = groups.get(stage + ":fixed", []), groups.get(stage + ":adaptive", [])
        by_id = {r[0]["id"]: r for r in adaptive}
        clusters = defaultdict(list)
        for f in fixed:
            a = by_id.get(f[0]["id"])
            if a is None:
                continue
            fl = binary_scores([f[1]["wp_home"]], [f[3]])["log_loss"]
            al = binary_scores([a[1]["wp_home"]], [a[3]])["log_loss"]
            clusters[f[0]["game"]["slate_date"]].append(al - fl)
        if not clusters:
            continue
        arrays = list(clusters.values())
        values = [x for xs in arrays for x in xs]
        interval = None
        if len(arrays) > 1:
            rng = np.random.default_rng(1919)
            samples = []
            for _ in range(2000):
                chosen = rng.integers(0, len(arrays), len(arrays))
                samples.append(np.mean([x for i in chosen for x in arrays[i]]))
            interval = np.quantile(samples, [0.025, 0.975]).tolist()
        result[stage] = {
            "n": len(values),
            "slates": len(arrays),
            "adaptive_minus_fixed_log_loss": float(np.mean(values)),
            "confidence_interval_95": interval,
            "exploratory": len(arrays) < 30,
        }
    return result


def closing_line_values(records):
    """Observed same-book pre-tip close, timestamped at ESPN fetch."""
    quotes = [r for r in records if r["kind"] == "market_observation"]
    output = []
    for forecast in (r for r in records if r["kind"] == "forecast"):
        tip = dt(forecast["game"]["tip_utc"])
        for variant, model in forecast["variants"].items():
            for s in model["selections"]:
                eligible = [
                    q
                    for q in quotes
                    if q["instance_id"] == forecast["instance_id"]
                    and q["book"] == s["book"]
                    and q["market"] == s["market"]
                    and 0 < (tip - dt(q["captured_at"])).total_seconds() <= 900
                    and -60
                    <= (dt(q["captured_at"]) - dt(q.get("updated_at") or q["captured_at"])).total_seconds()
                    <= 900
                    and (tip - dt(q.get("updated_at") or q["captured_at"])).total_seconds() <= 900
                ]
                if not eligible:
                    continue
                close = max(eligible, key=lambda x: dt(x["captured_at"]))
                line_clv = None
                if s["market"] == "ats":
                    line_clv = (s["line"] - close["line"]) * (1 if s["side"] == "home" else -1)
                elif s["market"] == "totals":
                    line_clv = (close["line"] - s["line"]) * (1 if s["side"] == "over" else -1)
                sides = ("over", "under") if s["market"] == "totals" else ("home", "away")
                q = no_vig(*(close["prices"][side] for side in sides))
                capture = next(m for m in model["markets"] if m["quote_id"] == s["quote_id"])
                probability_clv = (
                    q[sides.index(s["side"])] - capture["no_vig"][s["side"]]
                    if s["market"] == "moneyline" or close["line"] == s["line"]
                    else None
                )
                output.append(
                    {
                        "forecast_id": forecast["id"],
                        "variant": variant,
                        "series": s["series"],
                        "market": s["market"],
                        "side": s["side"],
                        "line_clv": line_clv,
                        "probability_clv": probability_clv,
                    }
                )
    return output
