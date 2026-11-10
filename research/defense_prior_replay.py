"""Replay defense-prior strengths on the pinned current-season seed, offline.

Uses the production fit/apply code and the archived research clock: each
outcome is assumed available at 12:00 UTC on the day after its Eastern tip
date, and each forecast uses the latest noon-UTC fit at or before its
snapshot. Games with fewer than 30 available outcomes receive no correction.
Win probability uses the fixed default scale a=10 for every candidate, so no
confidence parameter is fitted here.

This is retrospective research on a previously inspected sample. It writes no
ledger record and is never native forward evidence.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import numpy as np
from scipy.special import ndtr

from sleeper_nba import matchup
from sleeper_nba.common import ET, digest, dt, iso, load_config
from sleeper_nba.storage import Ledger

PRIORS = [5.0, 10.0, 20.0, 40.0, 80.0, 160.0]
OUTPUT = "research/output/defense_prior_replay_2026-27.json"
WP_SCALE = 10.0


def available_at(observation):
    day = dt(observation["tip_utc"]).astimezone(ET).date() + timedelta(days=1)
    return datetime.combine(day, time(12), UTC)


def fit_cutoff(observation):
    snapshot = dt(observation["snapshot_utc"])
    noon = datetime.combine(snapshot.date(), time(12), UTC)
    return noon if noon <= snapshot else noon - timedelta(days=1)


def replay(observations, settings):
    ordered = sorted(observations, key=lambda o: (dt(o["tip_utc"]), o["game_id"]))
    versions, rows = {}, []
    for o in ordered:
        cutoff = fit_cutoff(o)
        if cutoff not in versions:
            training = [
                {**x, "verified_at": iso(available_at(x))} for x in ordered if available_at(x) <= cutoff
            ]
            versions[cutoff] = matchup.fit(training, settings, iso(cutoff))
        version = versions[cutoff]
        raw = {"home_score": o["projected_home_score"], "away_score": o["projected_away_score"]}
        scoring, _ = matchup.apply(raw, o, version)
        rows.append(
            {
                "game_id": o["game_id"],
                "slate": dt(o["tip_utc"]).astimezone(ET).date().isoformat(),
                "corrected": version["status"] == "fit",
                "margin": scoring["margin"],
                "total": scoring["total"],
                "raw_margin": raw["home_score"] - raw["away_score"],
                "raw_total": raw["home_score"] + raw["away_score"],
                "actual_margin": o["actual_home_score"] - o["actual_away_score"],
                "actual_total": o["actual_home_score"] + o["actual_away_score"],
            }
        )
    return rows


def game_losses(rows):
    margin = np.array([r["margin"] for r in rows])
    won = np.array([r["actual_margin"] > 0 for r in rows])
    p = np.clip(ndtr(margin / WP_SCALE), 1e-12, 1 - 1e-12)
    return -np.where(won, np.log(p), np.log1p(-p)), (p - won) ** 2, (margin > 0) == won


def metrics(rows):
    margin_error = np.array([r["actual_margin"] - r["margin"] for r in rows])
    total_error = np.array([r["actual_total"] - r["total"] for r in rows])
    log_loss, brier, correct = game_losses(rows)
    margin_mae, total_mae = float(np.mean(abs(margin_error))), float(np.mean(abs(total_error)))
    return {
        "n": len(rows),
        "corrected_games": sum(r["corrected"] for r in rows),
        "correct": int(correct.sum()),
        "accuracy": float(correct.mean()),
        "margin": {"mae": margin_mae, "bias": float(margin_error.mean())},
        "total": {"mae": total_mae, "bias": float(total_error.mean())},
        "objective": 0.5 * (margin_mae / 14 + total_mae / 19),
        "log_loss": float(log_loss.mean()),
        "brier": float(brier.mean()),
        "margin_correction_sd": float(
            np.std([r["margin"] - r["raw_margin"] for r in rows if r["corrected"]] or [0.0])
        ),
    }


def paired_log_loss(rows, baseline):
    """Candidate minus baseline log loss with a slate-clustered bootstrap."""
    difference = game_losses(rows)[0] - game_losses(baseline)[0]
    slates = sorted({r["slate"] for r in rows})
    clusters = [difference[[i for i, r in enumerate(rows) if r["slate"] == s]] for s in slates]
    rng = np.random.default_rng(1921)
    samples = [
        np.mean(np.concatenate([clusters[i] for i in rng.integers(0, len(clusters), len(clusters))]))
        for _ in range(4000)
    ]
    return {
        "mean_difference": float(difference.mean()),
        "ci95_slate_bootstrap": np.quantile(samples, [0.025, 0.975]).tolist(),
        "slates": len(slates),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    root = parser.parse_args(argv).root.resolve()
    config = load_config(root)
    seed = json.loads((root / config["matchup"]["seed_file"]).read_text())
    body = {k: v for k, v in seed.items() if k != "sha256"}
    if digest(body) != seed["sha256"] or seed["sha256"] != config["matchup"]["seed_sha256"]:
        raise ValueError("Seed integrity failure")
    base = {**config["matchup"], "coefficient_bound": float("inf")}
    replays = {
        f"prior{prior:g}": replay(seed["observations"], {**base, "defense_prior": prior}) for prior in PRIORS
    }
    # A very large prior removes team effects and keeps the global/venue terms.
    replays["global_venue"] = replay(seed["observations"], {**base, "defense_prior": 1e12})
    raw = [
        {**r, "margin": r["raw_margin"], "total": r["raw_total"], "corrected": False}
        for r in replays["global_venue"]
    ]
    selected = f"prior{config['matchup']['defense_prior']:g}"
    if selected not in replays:
        raise ValueError("Registered defense prior is not in the replay grid")
    report = {
        "generated_for": "v1.21 defense-prior review, regenerated under the v1.23 one-game minimum; "
        "retrospective and previously inspected",
        "seed_sha256": seed["sha256"],
        "methodology": config["methodology"],
        "selected": selected,
        "settings": {
            "half_life_days": config["matchup"]["half_life_days"],
            "offense_prior": config["matchup"]["offense_prior"],
            "global_prior": config["matchup"]["global_prior"],
            "venue_prior": config["matchup"]["venue_prior"],
            "minimum_training_games": config["matchup"]["minimum_games"],
            "wp_scale": WP_SCALE,
            "outcome_availability": "12 UTC on the following Eastern slate date; assumed, not original "
            "verification stamps.",
            "daily_fit": "Latest noon UTC <= prediction time",
        },
        "candidates": {"raw_sleeper": metrics(raw), **{k: metrics(v) for k, v in replays.items()}},
        "selected_versus": {
            "raw_sleeper": paired_log_loss(replays[selected], raw),
            "prior5": paired_log_loss(replays[selected], replays["prior5"]),
        },
        "limitations": [
            f"{len(raw)} games on {len({r['slate'] for r in raw})} slates; "
            f"{sum(not r['corrected'] for r in replays[selected])} precede the "
            f"{config['matchup']['minimum_games']}-game minimum and receive no correction.",
            "The sample was inspected before this review and is not untouched forward evidence.",
            "Result-availability times are assumed; the legacy seed has no original raw stat objects.",
            "No betting-profit search was performed.",
        ],
    }
    Ledger._atomic(root / OUTPUT, json.dumps(report, indent=2) + "\n")
    for name, m in report["candidates"].items():
        print(
            f"{name:>13}  margin MAE {m['margin']['mae']:.3f}  total MAE {m['total']['mae']:.3f}  "
            f"acc {m['accuracy']:.3f}  log loss {m['log_loss']:.4f}  corr SD {m['margin_correction_sd']:.2f}"
        )
    print(json.dumps(report["selected_versus"], indent=2))


if __name__ == "__main__":
    main()
