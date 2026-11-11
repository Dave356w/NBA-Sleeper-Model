"""Read-only public comparisons from frozen forecasts and their latest grades."""

from __future__ import annotations

from .evaluation import latest_grades
from .season import _forecast_markets, _versus_market, favorite_benchmark


def live_market_comparisons(records):
    """Keep stage and variant cohorts separate, using paired capture-time quotes."""
    groups = {f"{stage}:{variant}": [] for stage in ("first", "lock") for variant in ("adaptive", "fixed")}
    grades = latest_grades(records)
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    for forecast in records:
        if forecast["kind"] != "forecast" or forecast.get("provenance") != "native":
            continue
        grade = grades.get(forecast["id"])
        if not grade or grade["void"] or forecast["instance_id"] in retired:
            continue
        result = grade["result"]
        if result["home_score"] == result["away_score"]:
            continue
        for variant, output in forecast["variants"].items():
            key = f"{forecast['stage']}:{variant}"
            if key not in groups:
                continue
            groups[key].append(
                {
                    "margin": output["ats_margin"],
                    "total": output["adjusted_total"],
                    "winner": output["winner"],
                    "wp_home": output["wp_home"],
                    "actual_margin": result["home_score"] - result["away_score"],
                    "actual_total": result["home_score"] + result["away_score"],
                    **_forecast_markets(forecast, variant),
                }
            )
    return {
        key: {
            "n": len(rows),
            "versus_market": _versus_market(rows),
            "favorite_benchmark": favorite_benchmark(rows),
        }
        for key, rows in groups.items()
    }
