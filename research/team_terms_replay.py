"""Walk-forward test of team offense/defense term variants on the current season (v1.25 evidence).

Uses the production fit code and the season replay's chronology: every game is
scored from a fit built at the noon-UTC cutoff before its snapshot, from results
usable by then (`season.replay_observations`), plus graded native first
snapshots of the tag current when this study was run (sleeper_only_v1_24).
Win probability uses a fixed scale a=10 so only the point model is compared.

Grid: defense prior 5/10/20/40/80/160 or none x offense prior 5/10/20/40/80/160
x offense applied to scores or used as an estimation control only, plus raw
Sleeper and global/venue-only controls. Paired differences against the v1.24
setting use a slate-clustered bootstrap.

Retrospective research on previously inspected data: 80 variants on about 200
games, so the best one is selected with optimism. It writes no ledger record
and is never native forward evidence. The owner selected `def 20 / off 20,
offense applied` from this table on 19 November 2026 (v1.25).

    python research/team_terms_replay.py --root .
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.special import ndtr

from sleeper_nba import matchup
from sleeper_nba import season as S
from sleeper_nba.common import ET, digest, dt, iso, load_config
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/team_terms_replay_2026-27.json"
STUDY_TAG = "sleeper_only_v1_24"
PRIORS = [5.0, 10.0, 20.0, 40.0, 80.0, 160.0]
NONE = 1e12
WP_SCALE = 10.0
BASELINE = "def 40 / off 20 control"
SELECTED = "def 20 / off 20 applied"


def observations(records, root, config, season):
    study = {**config, "methodology": STUDY_TAG}
    obs = S.replay_observations(records, root, study, season)
    markets = {o["game_id"]: o["markets"] for o in obs}
    have = {o["game_id"] for o in obs}
    forecasts = {
        f["game"]["game_id"]: f
        for f in records
        if f["kind"] == "forecast"
        and f["stage"] == "first"
        and f.get("provenance") == "native"
        and S._method(f) == STUDY_TAG
    }
    for r in S.live_rows(records, study, season):
        markets[r["game_id"]] = {k: r[k] for k in ("ml", "spread", "total_line")}
        if r["game_id"] in have:
            continue
        f = forecasts[r["game_id"]]
        obs.append(
            {k: r[k] for k in ("game_id", "home", "away", "tip_utc", "available_at")}
            | {k: r[k] for k in ("actual_home_score", "actual_away_score")}
            | {
                "snapshot_utc": f["captured_at"],
                "projected_home_score": f["raw"]["home_score"],
                "projected_away_score": f["raw"]["away_score"],
                "provenance": "native",
                "revisions": [],
            }
        )
    return sorted(obs, key=lambda o: (dt(o["tip_utc"]), o["game_id"])), markets


def training(obs, cutoff):
    out = []
    for x in obs:
        if dt(x["available_at"]) <= cutoff and dt(x["tip_utc"]) <= cutoff:
            home, away = S._scores_at(x, cutoff)
            if home != away:
                out.append({**x, "actual_home_score": home, "actual_away_score": away})
    return out


def run(obs, histories, cutoffs, settings, apply_offense, apply_defense):
    fits, margin, total = {}, [], []
    for o, cutoff in zip(obs, cutoffs, strict=True):
        if cutoff not in fits:
            fits[cutoff] = matchup.fit(histories[cutoff], settings, iso(cutoff))["params"]
        p = fits[cutoff]
        d = p["defense"] if apply_defense else dict.fromkeys(p["defense"], 0.0)
        f = p["offense_control"] if apply_offense else dict.fromkeys(p["offense_control"], 0.0)
        h = p["global"] + 0.5 * p["venue"] + d[o["away"]] + f[o["home"]]
        a = p["global"] - 0.5 * p["venue"] + d[o["home"]] + f[o["away"]]
        raw_h, raw_a = o["projected_home_score"], o["projected_away_score"]
        margin.append(raw_h - raw_a + h - a)
        total.append(raw_h + raw_a + h + a)
    return np.array(margin), np.array(total)


def score(obs, markets, margin, total):
    actual_m = np.array([o["actual_home_score"] - o["actual_away_score"] for o in obs])
    actual_t = np.array([o["actual_home_score"] + o["actual_away_score"] for o in obs])
    p = np.clip(ndtr(margin / WP_SCALE), 1e-12, 1 - 1e-12)
    won = actual_m > 0
    log_loss = -np.where(won, np.log(p), np.log1p(-p))
    ats, ou = [], []
    for i, o in enumerate(obs):
        m = markets.get(o["game_id"]) or {}
        if m.get("spread") is not None:
            edge, result = margin[i] + m["spread"], actual_m[i] + m["spread"]
            if edge and result:
                ats.append(bool((edge > 0) == (result > 0)))
        if m.get("total_line") is not None:
            edge, result = total[i] - m["total_line"], actual_t[i] - m["total_line"]
            if edge and result:
                ou.append(bool((edge > 0) == (result > 0)))
    margin_mae, total_mae = float(np.mean(abs(actual_m - margin))), float(np.mean(abs(actual_t - total)))
    summary = {
        "margin_mae": margin_mae,
        "total_mae": total_mae,
        "objective": 0.5 * (margin_mae / 14 + total_mae / 19),
        "log_loss": float(log_loss.mean()),
        "accuracy": float(np.mean((margin > 0) == won)),
        "ats": {"wins": sum(ats), "n": len(ats)},
        "totals": {"wins": sum(ou), "n": len(ou)},
    }
    return summary, {"margin": abs(actual_m - margin), "log_loss": log_loss}


def paired(a, b, slates, rng):
    diff = a - b
    groups = [np.flatnonzero(slates == s) for s in sorted(set(slates))]
    samples = [
        np.mean(np.concatenate([diff[groups[j]] for j in rng.integers(0, len(groups), len(groups))]))
        for _ in range(4000)
    ]
    return {"mean": float(diff.mean()), "ci95_slate_bootstrap": np.quantile(samples, [0.025, 0.975]).tolist()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    root = parser.parse_args(argv).root.resolve()
    config = load_config(root)
    records = Ledger(root).records()
    season = max(matchup.season_of(r["game"]) for r in records if r["kind"] in ("forecast", "schedule"))
    obs, markets = observations(records, root, config, season)
    cutoffs = [S._noon_cutoff(dt(o["snapshot_utc"])) for o in obs]
    histories = {c: training(obs, c) for c in sorted(set(cutoffs))}
    slates = np.array([dt(o["tip_utc"]).astimezone(ET).date().isoformat() for o in obs])
    base = {**config["matchup"], "coefficient_bound": float("inf")}
    raw_m = np.array([o["projected_home_score"] - o["projected_away_score"] for o in obs])
    raw_t = np.array([o["projected_home_score"] + o["projected_away_score"] for o in obs])

    results, losses = {}, {}
    results["raw Sleeper"], losses["raw Sleeper"] = score(obs, markets, raw_m, raw_t)
    gv = {**base, "defense_prior": NONE, "offense_prior": NONE}
    results["global+venue only"], losses["global+venue only"] = score(
        obs, markets, *run(obs, histories, cutoffs, gv, False, False)
    )
    for dp in [*PRIORS, NONE]:
        for op in PRIORS:
            for applied in (False, True):
                if dp == NONE and not applied:
                    continue
                name = f"def {'none' if dp == NONE else f'{dp:g}'} / off {op:g} {'applied' if applied else 'control'}"
                settings = {**base, "defense_prior": dp, "offense_prior": op}
                margin, total = run(obs, histories, cutoffs, settings, applied, dp != NONE)
                results[name], losses[name] = score(obs, markets, margin, total)

    rng = np.random.default_rng(2027)
    ranked = sorted(results, key=lambda k: results[k]["objective"])
    report = {
        "generated_for": "v1.25 team offense/defense review (owner request, 19 November 2026); "
        "retrospective and previously inspected",
        "season": season,
        "study_tag": STUDY_TAG,
        "games": len(obs),
        "slates": len(set(slates)),
        "ledger_records_sha256": digest(sorted(r["id"] for r in records)),
        "baseline": BASELINE,
        "selected": SELECTED,
        "ranking_by_objective": ranked,
        "variants": results,
        "versus_baseline": {
            k: {
                "margin_mae": paired(losses[k]["margin"], losses[BASELINE]["margin"], slates, rng),
                "log_loss": paired(losses[k]["log_loss"], losses[BASELINE]["log_loss"], slates, rng),
            }
            for k in dict.fromkeys([*ranked[:6], SELECTED, "raw Sleeper", "def 40 / off 20 applied"])
        },
        "limitations": [
            "One season of about 200 games, inspected before the choice; 80 variants were compared.",
            "Seed and backfill results use the assumed next-day 12:00 UTC availability.",
            "Last season's archive (docs/defense_selection_2025-26.md) found applied offense slightly worse.",
            "No betting-profit search was performed; spread and total records are context only.",
        ],
    }
    path = root / OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("games", "slates", "selected")} | {"best": ranked[0]}, indent=2))


if __name__ == "__main__":
    main()
