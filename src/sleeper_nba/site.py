from __future__ import annotations

import csv
import io
import json
import math
import shutil
from pathlib import Path
from statistics import median

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import matchup
from .common import ET, dt, iso, load_config, utcnow
from .evaluation import closing_line_values, latest_grades, summarize
from .markets import ALL_MARKETS
from .models import active_calibration
from .pipeline import latest_schedules, lock_outcomes, missed_lock_event
from .projections import public_player_snapshot
from .reporting import live_market_comparisons
from .season import season_record
from .storage import Ledger

PUBLIC_METHOD = matchup.METHOD
PUBLIC_VERSION = PUBLIC_METHOD.removeprefix("sleeper_only_v").replace("_", ".")
# Below these counts the public pages label scores exploratory.
SAMPLE_GATES = {"forecast_games": 100, "settled_bets": 250}


def methodology_of(record):
    return record.get("methodology", record.get("config", {}).get("methodology"))


def summarize_lock_outcomes(outcomes):
    slates = {}
    for outcome in outcomes.values():
        if not outcome["elapsed"]:
            continue
        row = slates.setdefault(
            outcome["slate_date"], {"slate_date": outcome["slate_date"], "leads": [], "missed": 0, "late": 0}
        )
        if outcome["status"] == "locked":
            row["leads"].append(outcome["lead_hours"])
            row["late"] += bool(outcome.get("late"))
        else:
            row["missed"] += 1
    result = []
    for row in sorted(slates.values(), key=lambda r: r["slate_date"]):
        leads = sorted(row.pop("leads"))
        result.append(
            {
                **row,
                "locked": len(leads),
                "games": len(leads) + row["missed"],
                "median_lead_hours": median(leads) if leads else None,
                "min_lead_hours": leads[0] if leads else None,
            }
        )
    return result


def lock_coverage(records, config, as_of):
    return summarize_lock_outcomes(lock_outcomes(records, config, as_of))


def live_win_fit(root):
    """Display-only in-game win chance coefficients (web/live_wp.json), or None."""
    try:
        fit = json.loads((Path(root) / "web" / "live_wp.json").read_text())
        knots, a, b = fit["knots"], fit["a"], fit["b"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    numbers = [x for values in (knots, a, b) for x in values]
    if (
        len(knots) < 2
        or not len(knots) == len(a) == len(b)
        or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in numbers)
        or any(knots[i] >= knots[i + 1] for i in range(len(knots) - 1))
    ):
        return None
    return {"knots": knots, "a": a, "b": b}


def build_site(root):
    root = Path(root)
    records = Ledger(root).records()
    config = load_config(root)
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    all_forecasts = [r for r in records if r["kind"] == "forecast"]
    forecasts = [
        f for f in all_forecasts if methodology_of(f) == PUBLIC_METHOD and f.get("provenance") == "native"
    ]
    forecast_ids = {f["id"] for f in forecasts}
    public_records = [r for r in records if r["kind"] != "forecast" or r["id"] in forecast_ids]
    grades = {k: v for k, v in latest_grades(records).items() if k in forecast_ids}
    defense_versions = [r for r in records if r["kind"] == "matchup_calibration"]
    season = max(
        (r["season"] for r in defense_versions), default=config.get("matchup", {}).get("seed_season", 2026)
    )
    as_of = utcnow()
    outcomes = lock_outcomes(records, config, as_of)
    recorded_misses = {r["instance_id"]: r for r in records if r.get("code") == "missed_lock"}
    misses = [
        recorded_misses.get(i) or {**missed_lock_event(o, o["window_end"]), "derived": True}
        for i, o in outcomes.items()
        if o["status"] == "missed"
    ]
    quality = [r for r in records if r["kind"] == "quality" and r["code"] != "missed_lock"] + misses
    data = {
        "updated_at": iso(),
        "today": utcnow().astimezone(ET).date().isoformat(),
        "games": [r["game"] for r in latest_schedules(records).values()],
        "forecasts": [
            {k: v for k, v in f.items() if k not in {"config", "source"}}
            | {
                "raw": {k: v for k, v in f["raw"].items() if k not in {"players", "raw_rows"}},
                "lineup": public_player_snapshot(f["raw"].get("players", [])),
                "retired": f["instance_id"] in retired,
                "methodology": PUBLIC_METHOD,
            }
            for f in forecasts
        ],
        "grades": grades,
        "performance": summarize(public_records),
        "market_comparisons": live_market_comparisons(public_records),
        "methodology": PUBLIC_METHOD,
        "season": season_record(records, root, config),
        "selection_markets": list(config.get("selection_markets", ALL_MARKETS)),
        "previews": [
            p
            for p in matchup.load_previews(root)["forecasts"]
            if methodology_of(p) == PUBLIC_METHOD
            and dt(p["captured_at"]) < dt(p["game"]["tip_utc"])
            and p["instance_id"] not in retired
        ],
        "matchup_calibrations": {
            stage: matchup.active(records, stage, iso(), season, config) for stage in ["first", "lock"]
        }
        if matchup.enabled(config)
        else {},
        "clv": closing_line_values(public_records),
        "calibrations": {
            stage: active_calibration(records, stage, iso(), PUBLIC_METHOD, config)
            for stage in ["first", "lock"]
        },
        "lock_outcomes": outcomes,
        "missed_locks": misses,
        "quality": [{k: r[k] for k in ["code", "context", "at"] if k in r} for r in quality],
        "methodology_version": PUBLIC_VERSION,
        "sample_gates": SAMPLE_GATES,
        "live_wp": live_win_fit(root),
        "lock_coverage": summarize_lock_outcomes(outcomes),
        "matchup_settings": {
            k: config["matchup"][k]
            for k in ["half_life_days", "minimum_games", "defense_prior", "offense_prior"]
        }
        | {"apply_offense": matchup.applies_offense({}, config["matchup"])}
        if matchup.enabled(config)
        else {},
    }
    output = root / "site"
    output.mkdir(exist_ok=True)
    Ledger._atomic(output / "data.json", json.dumps(data, ensure_ascii=False, allow_nan=False))
    env = Environment(loader=FileSystemLoader(root / "web"), autoescape=select_autoescape(["html"]))
    for page in ["index", "results", "performance", "methodology"]:
        html = env.get_template("page.html").render(
            page=page,
            page_title={
                "index": "Today's picks",
                "results": "Results",
                "performance": "Performance",
                "methodology": "Methodology",
            }[page],
            canonical_url=f"https://dave356w.github.io/NBA-Sleeper-Model/{page}.html",
            updated_at=data["updated_at"],
            methodology_version=data["methodology_version"],
        )
        Ledger._atomic(output / (page + ".html"), html)
    for asset in ["style.css", "app.js"]:
        shutil.copyfile(root / "web" / asset, output / asset)
    shutil.copytree(root / "web" / "assets", output / "assets", dirs_exist_ok=True)
    shutil.copyfile(root / "MODEL.md", output / "MODEL.md")
    (output / ".nojekyll").touch()
    Ledger._atomic(
        root / "research" / "output" / "performance.json", json.dumps(summarize(records), indent=2)
    )
    # Keep the full internal export; the public download shares the current cohort.
    Ledger._atomic(root / "data" / "predictions.csv", forecast_csv(all_forecasts, retired))
    Ledger._atomic(output / "predictions.csv", forecast_csv(forecasts, retired))
    return {"pages": 4, "forecasts": len(forecasts), "output": str(output)}


def _rounded(value):
    return round(value, 6) if isinstance(value, float) else value


def forecast_csv(forecasts, retired):
    """Auditable export, one row per stage and model variant, ordered by tip."""
    stream = io.StringIO()
    fields = [
        "forecast_id",
        "game_id",
        "instance_id",
        "slate_date",
        "tip_utc",
        "stage",
        "variant",
        "captured_at",
        "lead_hours",
        "home",
        "away",
        "home_score",
        "away_score",
        "margin",
        "total",
        "home_minutes",
        "away_minutes",
        "wp_home",
        "ats_margin",
        "adjusted_total",
        "wp_a",
        "ats_bias",
        "ats_sigma",
        "totals_bias",
        "totals_sigma",
        "calibration_id",
        "methodology",
        "model_tag",
        "adjusted_home_score",
        "adjusted_away_score",
        "home_correction",
        "away_correction",
        "matchup_id",
        "matchup_training_count",
        "retired",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    order = {"first": 0, "lock": 1}
    for f in sorted(
        forecasts, key=lambda f: (dt(f["game"]["tip_utc"]), f["game"]["game_id"], order.get(f["stage"], 2))
    ):
        game, minutes = f["game"], f["raw"].get("team_minutes", {})
        for variant in sorted(f["variants"]):
            out = f["variants"][variant]
            adjustment = out.get("matchup_adjustment") or {}
            row = {
                "forecast_id": f["id"],
                "game_id": game["game_id"],
                "instance_id": f["instance_id"],
                "slate_date": game["slate_date"],
                "tip_utc": game["tip_utc"],
                "stage": f["stage"],
                "variant": variant,
                "captured_at": f["captured_at"],
                "lead_hours": f.get("lead_hours"),
                "home": game["home"],
                "away": game["away"],
                **{k: f["raw"][k] for k in ["home_score", "away_score", "margin", "total"]},
                "home_minutes": minutes.get(game["home"]),
                "away_minutes": minutes.get(game["away"]),
                **{k: out[k] for k in ["wp_home", "ats_margin", "adjusted_total", "calibration_id"]},
                **out["params"],
                "retired": f["instance_id"] in retired,
                "methodology": f.get(
                    "methodology", f.get("config", {}).get("methodology", "sleeper_only_v1_19")
                ),
                "model_tag": out.get("model_tag"),
                "adjusted_home_score": out.get("scoring", f["raw"])["home_score"],
                "adjusted_away_score": out.get("scoring", f["raw"])["away_score"],
                "home_correction": adjustment.get("home_correction"),
                "away_correction": adjustment.get("away_correction"),
                "matchup_id": out.get("matchup_id"),
                "matchup_training_count": out.get("matchup_training_count"),
            }
            # Six decimals keeps the derived view readable; the ledger holds exact values.
            writer.writerow({k: _rounded(v) for k, v in row.items()})
    return stream.getvalue()
