"""Walk-forward test of a fitted slope on the model's margin and total (owner request, 26 Nov 2026).

The model treats Sleeper's projection with slope 1: the team fit learns actual − projected, so a
projected margin or total is used one-for-one. This test asks whether a slope fitted on earlier
games (actual = c + beta * predicted) improves spreads and totals. Research only: nothing here
writes a ledger record or changes a forecast, selection, grade or the public record.

Pre-registered before this script was first run (26 November 2026):

- Games: the pooled v1.33 season record (`season.replay_rows` plus `season.live_rows`, live games
  replacing replayed ones; lines from the frozen snapshot, else the pinned DraftKings close).
- Margin variant: margin' = beta * margin, beta = sum(m * y) / sum(m^2) through the origin (level
  and venue are already in the team terms). Total variant: total' = total + (beta − 1)(total − c),
  c the history's mean predicted total, beta the history's least-squares slope.
- Each beta is fitted only on games of earlier slates finished before the target's tip, needs 30
  such games (otherwise beta = 1), and is shrunk toward 1 with k pseudo-games: k = 0 (unshrunk) and
  k = 100 (the confidence-scale prior strength).
- Measures: margin and total MAE; against the line, ATS and over/under hit rate and units at an
  assumed −110 (the record stores no spread or total prices), pushes and missing lines no bet.
  Paired differences, variant minus baseline, 95% slate-bootstrap intervals.
- A slope would be recommended for a market only if its MAE improves with the interval entirely
  below 0 and its hit rate against the line is not lower. Spread and total quotes are context only
  (v1.22), so even a pass changes no pick; it would be a recommendation, not a version.

    python research/slope_walk_forward.py --root .
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from sleeper_nba.common import dt, load_config
from sleeper_nba.season import (
    _replay,
    closing_prices,
    live_rows,
    replay_rows,
    with_closing_prices,
)
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/slope_walk_forward_2026-27.json"
MINIMUM = 30
SHRINK = (0, 100)
BOOT = 2000
WIN_110 = 100 / 110


def pooled(records, root, config):
    season = config["matchup"]["seed_season"]
    observations, _ = _replay(records, root, config, season)
    live = live_rows(records, config, season)
    seen = {r["game_id"] for r in live}
    rows = [r for r in replay_rows(observations, config) if r["game_id"] not in seen] + live
    rows = with_closing_prices(rows, closing_prices(root, config))
    return sorted(rows, key=lambda r: (dt(r["tip_utc"]), r["game_id"]))


def shrunk(sxy, sxx, n, k):
    """Least-squares slope with k pseudo-games at slope 1, each carrying the mean sxx."""
    prior = k * sxx / n
    return (sxy + prior) / (sxx + prior)


def margin_beta(history, k):
    m = np.array([h["margin"] for h in history])
    y = np.array([h["actual_margin"] for h in history])
    return shrunk(float(m @ y), float(m @ m), len(m), k)


def total_beta(history, k):
    t = np.array([h["total"] for h in history])
    y = np.array([h["actual_total"] for h in history])
    c = t.mean()
    return shrunk(float((t - c) @ (y - y.mean())), float((t - c) @ (t - c)), len(t), k), c


def against_line(predicted, actual, line):
    """(+1 win, 0 loss, None no bet) for the side the prediction takes against `line`."""
    if line is None or predicted == line or actual == line:
        return None
    return int((predicted > line) == (actual > line))


def graded(row, margin, total):
    spread = row.get("spread")
    # `spread` is the home line: home covers when margin + spread > 0, i.e. margin > −spread.
    ats = against_line(margin, row["actual_margin"], -spread if spread is not None else None)
    ou = against_line(total, row["actual_total"], row.get("total_line"))
    return {
        "margin_abs_error": abs(row["actual_margin"] - margin),
        "total_abs_error": abs(row["actual_total"] - total),
        "ats": ats,
        "ou": ou,
    }


def run(rows, k):
    out = []
    for r in rows:
        tip = dt(r["tip_utc"])
        history = [h for h in rows if h["slate_date"] < r["slate_date"] and dt(h["available_at"]) <= tip]
        if len(history) >= MINIMUM:
            bm = margin_beta(history, k)
            bt, c = total_beta(history, k)
        else:
            bm, bt, c = 1.0, 1.0, r["total"]
        margin = bm * r["margin"]
        total = r["total"] + (bt - 1) * (r["total"] - c)
        out.append(
            {
                "slate": r["slate_date"],
                "beta_margin": bm,
                "beta_total": bt,
                "base": graded(r, r["margin"], r["total"]),
                "variant": graded(r, margin, total),
            }
        )
    return out


def mean_or_nan(values):
    return float(np.mean(values)) if values else float("nan")


def stat(items, key):
    """Variant minus baseline for one measure."""
    if key in ("margin_abs_error", "total_abs_error"):
        return mean_or_nan([i["variant"][key] - i["base"][key] for i in items])
    market, kind = key.split("_")  # e.g. ats_hit, ou_units
    out = {}
    for side in ("base", "variant"):
        bets = [i[side][market] for i in items if i[side][market] is not None]
        wins = sum(bets)
        out[side] = (
            wins / len(bets) if kind == "hit" else (wins * WIN_110 - (len(bets) - wins)) / max(len(bets), 1)
        )
    return out["variant"] - out["base"]


def summary(items, rng):
    slates = sorted({i["slate"] for i in items})
    by = {s: [i for i in items if i["slate"] == s] for s in slates}
    draws = [rng.choice(len(slates), len(slates)) for _ in range(BOOT)]
    result = {"games": len(items), "slates": len(slates)}
    for key in ("margin_abs_error", "total_abs_error", "ats_hit", "ats_units", "ou_hit", "ou_units"):
        boot = [stat([i for j in d for i in by[slates[j]]], key) for d in draws]
        base = {
            "margin_abs_error": mean_or_nan([i["base"]["margin_abs_error"] for i in items]),
            "total_abs_error": mean_or_nan([i["base"]["total_abs_error"] for i in items]),
        }.get(key)
        result[key] = {
            "difference": stat(items, key),
            "ci95_slate_bootstrap": np.nanpercentile(boot, [2.5, 97.5]).tolist(),
            **({"baseline": base} if base is not None else {}),
        }
    for market in ("ats", "ou"):
        bets = [i["base"][market] for i in items if i["base"][market] is not None]
        result[f"{market}_baseline"] = {
            "bets": len(bets),
            "hit": sum(bets) / len(bets),
            "units_per_bet_at_minus_110": (sum(bets) * WIN_110 - (len(bets) - sum(bets))) / len(bets),
        }
        result[f"{market}_picks_flipped"] = sum(
            i["base"][market] is not None
            and i["variant"][market] is not None
            and i["base"][market] != i["variant"][market]
            for i in items
        )
    fitted = [i for i in items if i["beta_margin"] != 1.0]
    result["beta_margin_range"] = [
        min(i["beta_margin"] for i in fitted),
        max(i["beta_margin"] for i in fitted),
    ]
    result["beta_total_range"] = [min(i["beta_total"] for i in fitted), max(i["beta_total"] for i in fitted)]
    result["beta_final"] = {"margin": items[-1]["beta_margin"], "total": items[-1]["beta_total"]}
    result["games_with_fitted_beta"] = len(fitted)
    return result


def decision(s):
    out = {}
    for market, mae, hit in (
        ("spread", "margin_abs_error", "ats_hit"),
        ("total", "total_abs_error", "ou_hit"),
    ):
        out[market] = bool(s[mae]["ci95_slate_bootstrap"][1] < 0 and s[hit]["difference"] >= 0)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    rows = pooled(records, root, config)
    rng = np.random.default_rng(20261126)
    variants = {}
    for k in SHRINK:
        s = summary(run(rows, k), rng)
        s["recommend"] = decision(s)
        variants[f"shrink_{k}"] = s
    result = {
        "generated_for": "walk-forward slope test on margin and total (owner request, 26 November 2026); "
        "research only",
        "methodology": config.get("methodology"),
        "ledger_records": len(records),
        "games": len(rows),
        "segments": {
            seg: sum(r["segment"] == seg for r in rows) for seg in sorted({r["segment"] for r in rows})
        },
        "line_basis": {
            market: {
                basis: sum((r.get("line_basis") or {}).get(market) == basis for r in rows)
                for basis in ("snapshot", "closing")
            }
            for market in ("spread", "total")
        },
        "variants": variants,
    }
    (root / OUTPUT).write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
