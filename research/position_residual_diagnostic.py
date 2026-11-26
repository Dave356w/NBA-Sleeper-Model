"""Do Sleeper's team-points errors carry a position signal the team-level fit misses? (diagnostic)

Owner request, 26 November 2026: before any position-specific fit is considered, check whether
team residuals relate to projected points by position. Research only: nothing here writes a
ledger record or changes a forecast, selection, grade or the public record.

Games: the native games of the live team fit (`matchup.eligible_observations` on the current
configuration, v1.31 game-day rule, v1.32 minutes cap). Seed and backfill games carry no player
rows and are not used. Positions: Sleeper's primary `position` from each forecast's raw rows,
grouped G (PG, SG), F (SF, PF) and C. Actual points by position come from the ESPN box-score
extract, matched within a team-game on a normalized name; a projected player absent from the box
score scored 0, and box-score points of a player Sleeper did not project are assigned through any
position Sleeper lists for him that day, otherwise to group U. Projections carry the team's v1.32
minutes-cap factor, so group projections sum to the capped team projection.

Questions (all intervals 95% bootstrap over slates, Eastern tip dates):

1. bias: is Sleeper's error per projected point different by position?
2. mix: does a team's share of projected points from guards or centers predict its team residual?
3. reliability: is an opponent's position-specific allowance a stable trait? Split each opponent's
   games into odd and even halves (repeated random splits) and correlate the halves' deviations,
   after removing the league position rate and the opponent's overall rate.
4. walk_forward: from games finished (tip + 4 h) before each snapshot, the opponent's shrunk
   position deviations predict (a) the position-group error and (b) the team residual beyond the
   opponent's overall rate. (b) is the test that matters for the model: an additive position term
   sums to the team term, so a position split can only change team scores through the interaction
   of an opponent's position rates with the current team's projected position mix.

    python research/position_residual_diagnostic.py --root . \\
        --box research/inputs/espn_box_2026-27_2026-11-26.csv
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

import numpy as np
from minutes_common import available, load_box, norm

from sleeper_nba.common import ET, dt, load_config, team, utcnow
from sleeper_nba.matchup import eligible_observations
from sleeper_nba.storage import Ledger

OUTPUT = "research/output/position_residual_diagnostic_2026-27.json"
GROUPS = ("G", "F", "C")
POSITION_GROUP = {"PG": "G", "SG": "G", "SF": "F", "PF": "F", "C": "C"}
SHRINK = (5, 20)  # opponent games of prior weight toward no deviation
BOOT = 2000
SPLITS = 200


def group_of(position):
    return POSITION_GROUP.get(position or "", "U")


def team_games(records, root, config, box):
    forecasts = {r["id"]: r for r in records if r["kind"] == "forecast"}
    season = config["matchup"]["seed_season"]
    observations = eligible_observations(records, root, config, "first", season, utcnow())
    rows, unmatched = [], 0
    for o in observations:
        if o["provenance"] != "native":
            continue
        f = forecasts[o["observation_id"]]
        positions = {}
        for raw in f["raw"].get("raw_rows", []):
            player = raw.get("player") or {}
            name = norm(f"{player.get('first_name', '')} {player.get('last_name', '')}")
            pos = player.get("position") or next(iter(player.get("fantasy_positions") or []), None)
            positions[str(raw.get("player_id"))] = pos
            positions[(team(player.get("team") or raw.get("team") or ""), name)] = pos
        slate = dt(o["tip_utc"]).astimezone(ET).date().isoformat()
        for code, opp, scale_key, proj_key, act_key in (
            (o["home"], o["away"], "home_minutes_scale", "projected_home_score", "actual_home_score"),
            (o["away"], o["home"], "away_minutes_scale", "projected_away_score", "actual_away_score"),
        ):
            key = (o["game_id"], code)
            if key not in box:
                unmatched += 1
                continue
            scale = o.get(scale_key, 1.0)
            proj = dict.fromkeys(GROUPS + ("U",), 0.0)
            act = dict.fromkeys(GROUPS + ("U",), 0.0)
            seen = set()
            for p in f["raw"]["players"]:
                if team(p["team"]) != code or p.get("projection_status") != "projected":
                    continue
                g = group_of(positions.get(str(p.get("player_id"))))
                name = norm(p["name"])
                proj[g] += float(p["points"] or 0) * scale
                act[g] += box[key].get(name, (0.0, 0.0))[1]
                seen.add(name)
            for name, (_, pts) in box[key].items():
                if name not in seen:
                    act[group_of(positions.get((code, name)))] += pts
            rows.append(
                {
                    "game_id": o["game_id"],
                    "slate": slate,
                    "team": code,
                    "opponent": opp,
                    "snapshot": dt(o["snapshot_utc"]),
                    "tip": dt(o["tip_utc"]),
                    "projected": o[proj_key],
                    "actual": o[act_key],
                    "proj": proj,
                    "act": act,
                    "box_total": sum(act.values()),
                }
            )
    return rows, unmatched


def boot(rows, stat, rng):
    slates = sorted({r["slate"] for r in rows})
    by = collections.defaultdict(list)
    for r in rows:
        by[r["slate"]].append(r)
    point = stat(rows)
    draws = []
    for _ in range(BOOT):
        pick = rng.choice(len(slates), len(slates))
        sample = [r for i in pick for r in by[slates[i]]]
        draws.append(stat(sample))
    draws = np.array(draws, dtype=float)
    lo, hi = np.nanpercentile(draws, [2.5, 97.5], axis=0)
    if np.ndim(point):
        return {"estimate": list(map(float, point)), "ci95": [list(map(float, lo)), list(map(float, hi))]}
    return {"estimate": float(point), "ci95": [float(lo), float(hi)]}


def rate(rows, g):
    p = sum(r["proj"][g] for r in rows)
    return (sum(r["act"][g] for r in rows) - p) / p if p else float("nan")


def bias(rows, rng):
    out = {}
    for g in GROUPS + ("U",):
        out[g] = {
            "projected_per_team_game": float(np.mean([r["proj"][g] for r in rows])),
            "actual_per_team_game": float(np.mean([r["act"][g] for r in rows])),
            "error_per_team_game": boot(
                rows, lambda s, g=g: np.mean([r["act"][g] - r["proj"][g] for r in s]), rng
            ),
        }
        if g != "U":
            out[g]["error_per_projected_point"] = boot(rows, lambda s, g=g: rate(s, g), rng)
    projected = sum(r["proj"][g] for r in rows for g in GROUPS)
    out["G_minus_C_rate"] = boot(rows, lambda s: rate(s, "G") - rate(s, "C"), rng)
    out["F_minus_C_rate"] = boot(rows, lambda s: rate(s, "F") - rate(s, "C"), rng)
    out["share_of_projection"] = {g: sum(r["proj"][g] for r in rows) / projected for g in GROUPS}
    return out


def mix(rows, rng):
    def fit(s):
        x = np.array([[1.0, r["proj"]["G"] / r["projected"], r["proj"]["C"] / r["projected"]] for r in s])
        y = np.array([r["actual"] - r["projected"] for r in s])
        return np.linalg.lstsq(x, y, rcond=None)[0][1:]

    result = boot(rows, fit, rng)
    return {
        "model": "team residual = b0 + bG * guard share + bC * center share (forward share is the baseline)",
        "slopes_points_per_unit_share": dict(zip(("guard_share", "center_share"), result["estimate"])),
        "ci95": {
            k: [result["ci95"][0][i], result["ci95"][1][i]]
            for i, k in enumerate(("guard_share", "center_share"))
        },
        "share_sd": {
            "guard": float(np.std([r["proj"]["G"] / r["projected"] for r in rows])),
            "center": float(np.std([r["proj"]["C"] / r["projected"] for r in rows])),
        },
    }


def deviations(rows, league):
    """Per opponent: position rate minus league rate, minus the opponent's own overall deviation."""
    total = sum(r["proj"][g] for r in rows for g in GROUPS)
    overall = sum(r["act"][g] - r["proj"][g] * (1 + league[g]) for r in rows for g in GROUPS) / total
    return {g: rate(rows, g) - league[g] - overall for g in GROUPS}


def reliability(rows, rng):
    league = {g: rate(rows, g) for g in GROUPS}
    by_opp = collections.defaultdict(list)
    for r in rows:
        by_opp[r["opponent"]].append(r)
    corrs = []
    for _ in range(SPLITS):
        a, b = [], []
        for games in by_opp.values():
            if len(games) < 4:
                continue
            order = rng.permutation(len(games))
            half = len(games) // 2
            da = deviations([games[i] for i in order[:half]], league)
            db = deviations([games[i] for i in order[half:]], league)
            a += [da[g] for g in GROUPS]
            b += [db[g] for g in GROUPS]
        corrs.append(np.corrcoef(a, b)[0, 1])
    corrs = np.array(corrs)
    # Null: the same statistic with team-games relabelled to random opponents.
    null = []
    opps = [r["opponent"] for r in rows]
    for _ in range(SPLITS):
        shuffled = rng.permutation(opps)
        fake = collections.defaultdict(list)
        for r, o in zip(rows, shuffled):
            fake[o].append(r)
        a, b = [], []
        for games in fake.values():
            if len(games) < 4:
                continue
            order = rng.permutation(len(games))
            half = len(games) // 2
            da = deviations([games[i] for i in order[:half]], league)
            db = deviations([games[i] for i in order[half:]], league)
            a += [da[g] for g in GROUPS]
            b += [db[g] for g in GROUPS]
        null.append(np.corrcoef(a, b)[0, 1])
    null = np.array(null)
    return {
        "opponents": sum(len(v) >= 4 for v in by_opp.values()),
        "team_games_per_opponent": [min(map(len, by_opp.values())), max(map(len, by_opp.values()))],
        "split_half_correlation_mean": float(corrs.mean()),
        "split_half_correlation_range95": np.percentile(corrs, [2.5, 97.5]).tolist(),
        "relabelled_null_mean": float(null.mean()),
        "relabelled_null_range95": np.percentile(null, [2.5, 97.5]).tolist(),
        "share_of_null_at_or_above_mean": float((null >= corrs.mean()).mean()),
    }


def walk_forward(rows, k):
    out = []
    for r in rows:
        history = [h for h in rows if available(h["tip"]) <= r["snapshot"]]
        if not history:
            continue
        league = {g: rate(history, g) for g in GROUPS}
        if any(np.isnan(v) for v in league.values()):
            continue
        opp = [h for h in history if h["opponent"] == r["opponent"]]
        n = len(opp)
        if n:
            raw = deviations(opp, league)
            total = sum(h["proj"][g] for h in opp for g in GROUPS)
            overall = sum(h["act"][g] - h["proj"][g] * (1 + league[g]) for h in opp for g in GROUPS) / total
        else:
            raw, overall = dict.fromkeys(GROUPS, 0.0), 0.0
        w = n / (n + k)
        dev = {g: w * raw[g] for g in GROUPS}
        overall *= w
        proj_total = sum(r["proj"][g] for g in GROUPS)
        # (a) group level: each position's error beyond league and the opponent's overall rate
        groups = [
            (r["proj"][g] * dev[g], r["act"][g] - r["proj"][g] * (1 + league[g] + overall)) for g in GROUPS
        ]
        # (b) team level: the interaction a position split adds to the team score
        added = sum(r["proj"][g] * dev[g] for g in GROUPS)
        base = (
            r["actual"]
            - r["projected"]
            - proj_total * (overall + sum(league[g] * r["proj"][g] for g in GROUPS) / proj_total)
        )
        out.append({"slate": r["slate"], "groups": groups, "added": added, "base": base, "opp_games": n})
    return out


def wf_summary(items, rng):
    def group_slope(s):
        x = np.array([a for i in s for a, _ in i["groups"]])
        y = np.array([b for i in s for _, b in i["groups"]])
        return float(np.dot(x, y) / np.dot(x, x)) if np.dot(x, x) else float("nan")

    def team_slope(s):
        x = np.array([i["added"] for i in s])
        y = np.array([i["base"] for i in s])
        return float(np.dot(x, y) / np.dot(x, x)) if np.dot(x, x) else float("nan")

    def mae_change(s):
        return float(np.mean([abs(i["base"] - i["added"]) - abs(i["base"]) for i in s]))

    return {
        "team_games": len(items),
        "group_level_slope": boot(items, group_slope, rng),
        "team_level_slope": boot(items, team_slope, rng),
        "team_mae_change_points": boot(items, mae_change, rng),
        "added_points_sd": float(np.std([i["added"] for i in items])),
        "note": "slope 1 = the position history is fully predictive; 0 = no signal. MAE change is the team "
        "absolute error with the position interaction added minus without (negative = better).",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--box", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    config = load_config(root)
    records = Ledger(root).records()
    box = load_box(args.box)
    rows, unmatched = team_games(records, root, config, box)
    rng = np.random.default_rng(26)
    box_gap = [abs(r["box_total"] - r["actual"]) for r in rows]
    known = sum(r["act"][g] for r in rows for g in GROUPS) / sum(r["box_total"] for r in rows)
    result = {
        "generated_for": "position residual diagnostic (owner request, 26 November 2026); retrospective, research only",
        "methodology": config.get("methodology"),
        "inputs": {
            "box_file": args.box,
            "box_sha256": hashlib.sha256(Path(args.box).read_bytes()).hexdigest(),
            "ledger_records": len(records),
        },
        "games": len({r["game_id"] for r in rows}),
        "team_games": len(rows),
        "slates": len({r["slate"] for r in rows}),
        "unmatched_team_games": unmatched,
        "box_vs_result_max_gap": max(box_gap),
        "actual_points_with_known_position": known,
        "bias": bias(rows, rng),
        "mix": mix(rows, rng),
        "reliability": reliability(rows, rng),
        "walk_forward": {f"shrink_{k}": wf_summary(walk_forward(rows, k), rng) for k in SHRINK},
    }
    Path(root / OUTPUT).write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
