from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import matchup
from .common import ET, digest, dt, iso, load_config, utcnow
from .evaluation import closing_line_values, latest_grades, summarize
from .models import active_calibration
from .pipeline import latest_schedules
from .storage import Ledger

PUBLIC_METHOD = matchup.METHOD


def methodology_of(record):
    return record.get("methodology", record.get("config", {}).get("methodology"))


def historical_replay(root, config):
    """Publish only the selected current-season research summary, never native grades."""
    path = root / "research" / "output" / "defense_selection_2025-26.json"
    if not path.exists():
        return None
    archive = json.loads(path.read_text())
    candidate = "defense_offense_control_prior5"
    if archive["selected_iteration"] != candidate or archive["settings"]["half_life_days"] != 25:
        raise ValueError("Historical summary does not describe the registered v1.20 candidate")
    seed = json.loads((root / config["matchup"]["seed_file"]).read_text())
    body = {k: v for k, v in seed.items() if k != "sha256"}
    if digest(body) != seed["sha256"] or seed["sha256"] != config["matchup"]["seed_sha256"]:
        raise ValueError("Historical summary seed integrity failure")
    point = archive["current_season_check"][candidate]
    confidence = archive["selected_confidence_calibration"]["2026-27_legacy_first"]["current"]
    n = point["n"]
    if n != len(seed["observations"]) or confidence["n"] != n:
        raise ValueError("Historical summary and archived sample counts differ")
    dates = sorted(dt(o["tip_utc"]).astimezone(ET).date().isoformat() for o in seed["observations"])
    correct = round(n * point["accuracy"])
    return {
        "methodology": PUBLIC_METHOD,
        "label": "Historical replay",
        "provenance": "legacy-first",
        "stage": "first",
        "season": "2026–27",
        "start_date": dates[0],
        "end_date": dates[-1],
        "generated_at": archive["generated_at"],
        "source_hash": digest(archive),
        "source_url": "https://github.com/Dave356w/NBA-Sleeper-Model/blob/main/research/output/defense_selection_2025-26.json",
        "outcome_availability": archive["settings"]["outcome_availability"],
        "metrics": {
            "n": n,
            "correct": correct,
            "incorrect": n - correct,
            "accuracy": point["accuracy"],
            "margin": point["margin"],
            "total": point["total"],
            "log_loss": confidence["log_loss"],
            "brier": confidence["brier"],
        },
    }


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
    data = {
        "updated_at": iso(),
        "today": utcnow().astimezone(ET).date().isoformat(),
        "games": [r["game"] for r in latest_schedules(records).values()],
        "forecasts": [
            {k: v for k, v in f.items() if k not in {"config", "source"}}
            | {
                "raw": {k: v for k, v in f["raw"].items() if k not in {"players", "raw_rows"}},
                "retired": f["instance_id"] in retired,
                "methodology": PUBLIC_METHOD,
            }
            for f in forecasts
        ],
        "grades": grades,
        "performance": summarize(public_records),
        "methodology": PUBLIC_METHOD,
        "historical_replay": historical_replay(root, config),
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
            stage: active_calibration(records, stage, iso(), PUBLIC_METHOD) for stage in ["first", "lock"]
        },
        "missed_locks": [r for r in records if r["kind"] == "quality" and r["code"] == "missed_lock"],
        "quality": [
            {k: r[k] for k in ["code", "context", "at"] if k in r} for r in records if r["kind"] == "quality"
        ],
        "methodology_version": "1.20",
    }
    output = root / "site"
    output.mkdir(exist_ok=True)
    Ledger._atomic(output / "data.json", json.dumps(data, ensure_ascii=False, allow_nan=False))
    env = Environment(loader=FileSystemLoader(root / "web"), autoescape=select_autoescape(["html"]))
    for page in ["index", "results", "performance", "methodology"]:
        html = env.get_template("page.html").render(
            page=page, updated_at=data["updated_at"], methodology_version=data["methodology_version"]
        )
        Ledger._atomic(output / (page + ".html"), html)
    for asset in ["style.css", "app.js"]:
        shutil.copyfile(root / "web" / asset, output / asset)
    shutil.copyfile(root / "MODEL.md", output / "MODEL.md")
    (output / ".nojekyll").touch()
    Ledger._atomic(
        root / "research" / "output" / "performance.json", json.dumps(summarize(records), indent=2)
    )
    # Keep the full internal export; the public download shares the v1.20 cohort.
    Ledger._atomic(root / "data" / "predictions.csv", forecast_csv(all_forecasts, retired))
    Ledger._atomic(output / "predictions.csv", forecast_csv(forecasts, retired))
    return {"pages": 4, "forecasts": len(forecasts), "output": str(output)}


def forecast_csv(forecasts, retired):
    """Auditable export, one row per stage and model variant."""
    stream = io.StringIO()
    fields = [
        "forecast_id",
        "game_id",
        "instance_id",
        "slate_date",
        "stage",
        "variant",
        "captured_at",
        "home",
        "away",
        "home_score",
        "away_score",
        "margin",
        "total",
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
        "adjusted_home_score",
        "adjusted_away_score",
        "matchup_id",
        "retired",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for f in forecasts:
        for variant, out in f["variants"].items():
            writer.writerow(
                {
                    "forecast_id": f["id"],
                    "game_id": f["game"]["game_id"],
                    "instance_id": f["instance_id"],
                    "slate_date": f["game"]["slate_date"],
                    "stage": f["stage"],
                    "variant": variant,
                    "captured_at": f["captured_at"],
                    "home": f["game"]["home"],
                    "away": f["game"]["away"],
                    **{k: f["raw"][k] for k in ["home_score", "away_score", "margin", "total"]},
                    **{k: out[k] for k in ["wp_home", "ats_margin", "adjusted_total", "calibration_id"]},
                    **out["params"],
                    "retired": f["instance_id"] in retired,
                    "methodology": f.get(
                        "methodology", f.get("config", {}).get("methodology", "sleeper_only_v1_19")
                    ),
                    "adjusted_home_score": out.get("scoring", f["raw"])["home_score"],
                    "adjusted_away_score": out.get("scoring", f["raw"])["away_score"],
                    "matchup_id": out.get("matchup_id"),
                }
            )
    return stream.getvalue()
