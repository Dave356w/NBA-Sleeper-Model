from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .common import ET, iso, utcnow
from .evaluation import closing_line_values, latest_grades, summarize
from .models import active_calibration
from .pipeline import latest_schedules
from .storage import Ledger


def build_site(root):
    root = Path(root)
    records = Ledger(root).records()
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    forecasts = [r for r in records if r["kind"] == "forecast"]
    grades = latest_grades(records)
    data = {
        "updated_at": iso(),
        "today": utcnow().astimezone(ET).date().isoformat(),
        "games": [r["game"] for r in latest_schedules(records).values()],
        "forecasts": [
            {k: v for k, v in f.items() if k not in {"config", "source"}}
            | {
                "raw": {k: v for k, v in f["raw"].items() if k not in {"players", "raw_rows"}},
                "retired": f["instance_id"] in retired,
            }
            for f in forecasts
        ],
        "grades": grades,
        "performance": summarize(records),
        "clv": closing_line_values(records),
        "calibrations": {stage: active_calibration(records, stage, iso()) for stage in ["first", "lock"]},
        "missed_locks": [r for r in records if r["kind"] == "quality" and r["code"] == "missed_lock"],
        "quality": [
            {k: r[k] for k in ["code", "context", "at"] if k in r} for r in records if r["kind"] == "quality"
        ],
        "methodology_version": "1.19",
    }
    output = root / "site"
    output.mkdir(exist_ok=True)
    Ledger._atomic(output / "data.json", json.dumps(data, ensure_ascii=False, allow_nan=False))
    env = Environment(loader=FileSystemLoader(root / "web"), autoescape=select_autoescape(["html"]))
    for page in ["index", "results", "performance", "methodology"]:
        html = env.get_template("page.html").render(page=page, updated_at=data["updated_at"])
        Ledger._atomic(output / (page + ".html"), html)
    for asset in ["style.css", "app.js"]:
        shutil.copyfile(root / "web" / asset, output / asset)
    shutil.copyfile(root / "MODEL.md", output / "MODEL.md")
    (output / ".nojekyll").touch()
    Ledger._atomic(
        root / "research" / "output" / "performance.json", json.dumps(data["performance"], indent=2)
    )
    # Auditable forecast export, one row per stage and model variant.
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
        "retired",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
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
                }
            )
    Ledger._atomic(root / "data" / "predictions.csv", stream.getvalue())
    Ledger._atomic(output / "predictions.csv", stream.getvalue())
    return {"pages": 4, "forecasts": len(forecasts), "output": str(output)}
