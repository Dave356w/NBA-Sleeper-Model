"""Reproduce the fixed 9 November 2026 v1.20 market study, offline.

This research-only command reads hash-pinned legacy inputs. It never calls a
live source or writes native forecasts, selections, grades or model parameters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

import numpy as np

from sleeper_nba import matchup
from sleeper_nba.common import ET, digest, dt, iso, load_config, team
from sleeper_nba.evaluation import binary_scores, error_scores
from sleeper_nba.markets import evaluate, expected_value, implied, settle
from sleeper_nba.models import DEFAULTS, fit_parameters, probabilities

ANALYSIS_DATE = "2026-11-09"
INPUT = "research/inputs/v1_20_market_quotes_2026-11-09.json"
OUTPUT = "v1_20_market_analysis_2026-11-09.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(root):
    archive = json.loads((root / INPUT).read_text())
    require(
        digest({k: v for k, v in archive.items() if k != "sha256"}) == archive["sha256"],
        "Research market input checksum mismatch",
    )
    config = load_config(root)
    require(config["methodology"] == matchup.METHOD, "This study requires the archived v1.20 configuration")
    require(digest(config) == archive["config_sha256"], "v1.20 research configuration drift")
    for path, expected in archive["implementation_sha256"].items():
        require(file_sha256(root / path) == expected, "Implementation drift: " + path)
        module = import_module("sleeper_nba." + Path(path).stem)
        require(
            file_sha256(Path(module.__file__)) == expected,
            "Imported package differs from the archived implementation: " + path,
        )
    require(
        file_sha256(root / "research/output/defense_selection_2025-26.json")
        == archive["reference_summary_sha256"],
        "Archived reference summary drift",
    )
    seed = json.loads((root / config["matchup"]["seed_file"]).read_text())
    require(
        digest({k: v for k, v in seed.items() if k != "sha256"})
        == seed["sha256"]
        == archive["seed_payload_sha256"]
        == config["matchup"]["seed_sha256"],
        "Seed integrity failure",
    )
    ids = [r["game_id"] for r in archive["rows"]]
    require(len(ids) == len(set(ids)) == len(seed["observations"]), "Duplicate or missing market rows")
    require(set(ids) == {o["game_id"] for o in seed["observations"]}, "Market and seed game IDs differ")
    return config, seed, archive


def point_replay(seed, config):
    observations = [dict(o) for o in seed["observations"]]
    for o in observations:
        day = dt(o["tip_utc"]).astimezone(ET).date() + timedelta(days=1)
        o["available_at"] = datetime.combine(day, datetime.min.time(), UTC).replace(hour=12).isoformat()
        require(
            dt(o["snapshot_utc"]) < dt(o["tip_utc"]) < dt(o["available_at"]),
            "Invalid archived forecast chronology",
        )
    rows = []
    for o in sorted(observations, key=lambda x: (dt(x["snapshot_utc"]), x["game_id"])):
        cutoff = dt(o["snapshot_utc"]).replace(hour=12, minute=0, second=0, microsecond=0)
        if cutoff > dt(o["snapshot_utc"]):
            cutoff -= timedelta(days=1)
        prior = [x for x in observations if dt(x["available_at"]) <= cutoff]
        fitted = matchup.fit(prior, config["matchup"], iso(cutoff))
        raw = {"home_score": o["projected_home_score"], "away_score": o["projected_away_score"]}
        raw.update(margin=raw["home_score"] - raw["away_score"], total=raw["home_score"] + raw["away_score"])
        scoring, adjustment = matchup.apply(raw, o, fitted)
        actual_margin = o["actual_home_score"] - o["actual_away_score"]
        rows.append(
            {
                **o,
                "cutoff": iso(cutoff),
                "training_games": len(prior),
                "raw": raw,
                "scoring": scoring,
                "adjustment": adjustment,
                "margin_error": actual_margin - scoring["margin"],
                "total_error": o["actual_home_score"] + o["actual_away_score"] - scoring["total"],
                "correct": (scoring["margin"] > 0) == (actual_margin > 0),
            }
        )
    return rows


def slate_bootstrap(values, replicates=10000):
    clusters = defaultdict(list)
    for row, value in values:
        clusters[row["date"]].append(value)
    groups = [np.asarray(v) for _, v in sorted(clusters.items())]
    if not groups:
        return None
    rng = np.random.default_rng(120)
    sums = np.array([g.sum() for g in groups])
    sizes = np.array([len(g) for g in groups])
    chosen = rng.integers(0, len(groups), size=(replicates, len(groups)))
    means = sums[chosen].sum(axis=1) / sizes[chosen].sum(axis=1)
    return {
        "n": len(values),
        "slates": len(groups),
        "mean": float(sum(sums) / sum(sizes)),
        "ci95": np.quantile(means, [0.025, 0.975]).tolist(),
        "replicates": replicates,
        "seed": 120,
        "exploratory": True,
    }


def favorite_reversals(rows):
    games = []
    for r in rows:
        ml = next((m for m in r["markets"] if m["market"] == "moneyline" and m["eligible"]), None)
        if ml is None:
            continue
        ats = next((m for m in r["markets"] if m["market"] == "ats" and m["eligible"]), None)
        for s in r["selections"]:
            if s["market"] != "moneyline" or s["series"] != "value" or ml["no_vig"][s["side"]] >= 0.5:
                continue
            side = "away" if s["side"] == "home" else "home"
            choice = {"market": "moneyline", "side": side, "line": None, "odds": ml["prices"][side]}
            favorite_ml = {
                **choice,
                **settle(choice, r["actual_home_score"], r["actual_away_score"]),
                "market_probability": ml["no_vig"][side],
                "model_probability": ml["probabilities"][side],
                "breakeven_probability": implied(choice["odds"]),
            }
            favorite_ats = None
            if ats:
                choice = {"market": "ats", "side": side, "line": ats["line"], "odds": ats["prices"][side]}
                favorite_ats = {
                    **choice,
                    **settle(choice, r["actual_home_score"], r["actual_away_score"]),
                    "favorite_line": ats["line"] if side == "home" else -ats["line"],
                }
            games.append(
                {
                    "game_id": r["game_id"],
                    "date": r["date"],
                    "snapshot_utc": r["snapshot_utc"],
                    "market_snapshot_utc": ml["captured_at"],
                    "favorite": r[side],
                    "underdog": r[s["side"]],
                    "model_dog_probability": s["probability"],
                    "market_dog_probability": ml["no_vig"][s["side"]],
                    "dog_odds": s["odds"],
                    "dog_probability_edge": s["probability_edge"],
                    "dog_ev": s["ev"],
                    "dog_units": s["units"],
                    "model_dog_below_50": s["probability"] < 0.5,
                    "favorite_moneyline": favorite_ml,
                    "favorite_ats": favorite_ats,
                }
            )
    groups = {
        "all_value_underdogs": games,
        "model_dog_below_50": [g for g in games if g["model_dog_below_50"]],
        "model_dog_at_least_50": [g for g in games if not g["model_dog_below_50"]],
    }
    output = {}
    for name, selected in groups.items():
        output[name] = {}
        for market in ["favorite_moneyline", "favorite_ats"]:
            pairs = [(g, g[market]) for g in selected if g[market] is not None]
            n = len(pairs)
            output[name][market] = {
                "n": n,
                "wins": sum(b["outcome"] == "win" for g, b in pairs),
                "losses": sum(b["outcome"] == "loss" for g, b in pairs),
                "pushes": sum(b["outcome"] == "push" for g, b in pairs),
                "units": sum(b["units"] for g, b in pairs),
                "roi": sum(b["units"] for g, b in pairs) / n if n else None,
                "roi_slate_bootstrap": slate_bootstrap([(g, b["units"]) for g, b in pairs], 20000),
            }
        m = output[name]["favorite_moneyline"]
        m["mean_market_probability"] = float(
            np.mean([g["favorite_moneyline"]["market_probability"] for g in selected])
        )
        m["market_expected_wins"] = sum(g["favorite_moneyline"]["market_probability"] for g in selected)
        m["mean_breakeven_probability"] = float(
            np.mean([g["favorite_moneyline"]["breakeven_probability"] for g in selected])
        )
        m["mean_model_probability"] = float(
            np.mean([g["favorite_moneyline"]["model_probability"] for g in selected])
        )
        m["median_odds"] = float(np.median([g["favorite_moneyline"]["odds"] for g in selected]))
    return {
        "status": "retrospective exploratory hypothesis; not an active selection rule",
        "groups": output,
        "games": games,
    }


def analyze(root):
    config, seed, input_archive = load_inputs(root)
    rows = point_replay(seed, config)
    ledger = {r["game_id"]: r for r in input_archive["rows"]}
    confidence, last_cutoff, last_ids = dict(DEFAULTS), None, []
    frozen = []
    for r in rows:
        prior = sorted(
            (x for x in frozen if dt(x["available_at"]) <= dt(r["cutoff"])),
            key=lambda x: (dt(x["available_at"]), x["game_id"]),
        )
        ids = [x["game_id"] for x in prior]
        if len(prior) >= config["minimum_games"] and r["cutoff"] != last_cutoff and ids != last_ids:
            observations = [
                {
                    "margin": x["scoring"]["margin"],
                    "home_won": x["actual_home_score"] > x["actual_away_score"],
                    "ats_residual": x["margin_error"],
                    "totals_residual": x["total_error"],
                }
                for x in prior
            ]
            confidence, _, _ = fit_parameters(observations, confidence, config)
            last_cutoff, last_ids = r["cutoff"], ids
        r["confidence"] = dict(confidence)
        r["output"] = probabilities(r["scoring"], confidence)
        r["date"] = dt(r["tip_utc"]).astimezone(ET).date().isoformat()
        r["home_won"] = r["actual_home_score"] > r["actual_away_score"]
        raw_quote = ledger[r["game_id"]]
        require(
            (team(raw_quote["home"]), team(raw_quote["away"])) == (r["home"], r["away"]),
            "Market and seed team identities differ: " + r["game_id"],
        )
        require(
            (float(raw_quote["home_pts"]), float(raw_quote["away_pts"]))
            == (r["actual_home_score"], r["actual_away_score"]),
            "Market and seed final scores differ: " + r["game_id"],
        )
        quotes = []
        policies = [
            (
                "moneyline",
                "first_snapshot_utc",
                "first_book",
                None,
                {"home": "first_home_ml", "away": "first_away_ml"},
            ),
            (
                "ats",
                "first_snapshot_utc",
                "first_book",
                "first_spread",
                {"home": "first_home_spread_odds", "away": "first_away_spread_odds"},
            ),
            (
                "totals",
                "first_ou_snapshot_utc",
                "first_ou_book",
                "first_ou_line",
                {"over": "first_ou_over", "under": "first_ou_under"},
            ),
        ]
        r["quote_availability"] = {}
        for market, timestamp, book, line, prices in policies:
            if not raw_quote.get(timestamp) or not all(raw_quote.get(k) for k in prices.values()):
                r["quote_availability"][market] = "missing"
                continue
            quote = {
                "market": market,
                "book": "draftkings" if raw_quote[book] == "dk" else raw_quote[book],
                "captured_at": raw_quote[timestamp],
                "updated_at": None,
                "line": float(raw_quote[line]) if line and raw_quote[line] else None,
                "prices": {side: float(raw_quote[k]) for side, k in prices.items()},
                "quote_id": "legacy:" + r["game_id"] + ":" + market,
            }
            quotes.append(quote)
        markets, selections = evaluate(r["output"], confidence, quotes, r["snapshot_utc"], r, config, "first")
        r["markets"] = markets
        for m in markets:
            r["quote_availability"][m["market"]] = "eligible" if m["eligible"] else m["unavailable_reason"]
        r["selections"] = [
            dict(s, **settle(s, r["actual_home_score"], r["actual_away_score"])) for s in selections
        ]
        frozen.append(r)

    comparison = {}
    series = defaultdict(list)
    for kind in ["moneyline", "ats", "totals"]:
        pairs = [
            (r, next((m for m in r["markets"] if m["market"] == kind and m["eligible"]), None)) for r in rows
        ]
        pairs = [(r, m) for r, m in pairs if m is not None]
        if not pairs:
            continue
        pside = "over" if kind == "totals" else "home"

        def target(r, m):
            if kind == "moneyline":
                return r["home_won"]
            actual = (
                r["actual_home_score"] - r["actual_away_score"] + m["line"]
                if kind == "ats"
                else r["actual_home_score"] + r["actual_away_score"] - m["line"]
            )
            require(actual != 0, "Unexpected push in a supported half-point market")
            return actual > 0

        y = [target(r, m) for r, m in pairs]
        ps = [m["probabilities"][pside] for _, m in pairs]
        qs = [m["no_vig"][pside] for _, m in pairs]
        current = {
            "n": len(pairs),
            "slates": len(set(r["date"] for r, _ in pairs)),
            "model": binary_scores(ps, y),
            "market": binary_scores(qs, y),
            "paired_log_loss": slate_bootstrap(
                [
                    (r, binary_scores([p], [v])["log_loss"] - binary_scores([q], [v])["log_loss"])
                    for (r, m), p, q, v in zip(pairs, ps, qs, y)
                ]
            ),
        }
        if kind == "moneyline":
            directional = [
                (r, m) for r, m in pairs if m["no_vig"]["home"] != 0.5 and m["probabilities"]["home"] != 0.5
            ]
            for label, field in [("model", "probabilities"), ("market", "no_vig")]:
                correct = sum((m[field]["home"] > 0.5) == r["home_won"] for r, m in directional)
                current[label].update(
                    directional_n=len(directional), correct=correct, accuracy=correct / len(directional)
                )
            current["ties"] = {"model": sum(p == 0.5 for p in ps), "market": sum(q == 0.5 for q in qs)}
            agree = [
                (r, m)
                for r, m in directional
                if (m["probabilities"]["home"] > 0.5) == (m["no_vig"]["home"] > 0.5)
            ]
            disagree = [
                (r, m)
                for r, m in directional
                if (m["probabilities"]["home"] > 0.5) != (m["no_vig"]["home"] > 0.5)
            ]
            current["agreement"] = {
                "agree_n": len(agree),
                "disagree_n": len(disagree),
                "model_wins_when_disagree": sum(
                    (m["probabilities"]["home"] > 0.5) == r["home_won"] for r, m in disagree
                ),
            }
            current["probability_bins"] = []
            for lo, hi in [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.000001)]:
                rr = [(r, m) for r, m in pairs if lo <= max(m["probabilities"].values()) < hi]
                if rr:
                    current["probability_bins"].append(
                        {
                            "band": [lo, min(hi, 1)],
                            "n": len(rr),
                            "expected_accuracy": float(
                                np.mean([max(m["probabilities"].values()) for r, m in rr])
                            ),
                            "accuracy": float(np.mean([r["correct"] for r, m in rr])),
                        }
                    )
        else:
            field = "margin_error" if kind == "ats" else "total_error"
            market_residuals = [
                (r["actual_home_score"] - r["actual_away_score"] + m["line"])
                if kind == "ats"
                else r["actual_home_score"] + r["actual_away_score"] - m["line"]
                for r, m in pairs
            ]
            current["model"]["point_error"] = error_scores([r[field] for r, m in pairs])
            current["market"]["point_error"] = error_scores(market_residuals)
            current["paired_absolute_error"] = slate_bootstrap(
                [(r, abs(r[field]) - abs(e)) for (r, m), e in zip(pairs, market_residuals)]
            )
        comparison[kind] = current

    for r in rows:
        for s in r["selections"]:
            series[s["market"] + ":" + s["series"]].append((r, s))

    def bet_metrics(values):
        if not values:
            return {"n": 0}
        return {
            "n": len(values),
            "wins": sum(s["outcome"] == "win" for r, s in values),
            "losses": sum(s["outcome"] == "loss" for r, s in values),
            "pushes": sum(s["outcome"] == "push" for r, s in values),
            "units": sum(s["units"] for r, s in values),
            "roi": sum(s["units"] for r, s in values) / len(values),
            "mean_model_probability": float(np.mean([s["probability"] for r, s in values])),
            "mean_model_ev": float(np.mean([s["ev"] for r, s in values])),
            "mean_probability_edge": float(np.mean([s["probability_edge"] for r, s in values])),
        }

    bets = {}
    for key, values in series.items():
        bets[key] = bet_metrics(values)
        if key.startswith("moneyline"):
            for role in ["favorite", "underdog", "pickem"]:
                subset = []
                for r, s in values:
                    market = next(m for m in r["markets"] if m["market"] == "moneyline")
                    q = market["no_vig"][s["side"]]
                    if ("favorite" if q > 0.5 else "underdog" if q < 0.5 else "pickem") == role:
                        subset.append((r, s))
                bets[key][role] = bet_metrics(subset)
        if key.startswith("ats:directional") or key.startswith("totals:directional"):
            bands = []
            for lo, hi in [(0, 3), (3, 6), (6, 10), (10, float("inf"))]:
                subset = [
                    (r, s)
                    for r, s in values
                    if lo
                    <= abs(next(m for m in r["markets"] if m["market"] == s["market"])["edge_points"])
                    < hi
                ]
                bands.append({"edge_band": [lo, hi if np.isfinite(hi) else None], **bet_metrics(subset)})
            bets[key]["point_edge_bands"] = bands

    favorite_bets = []
    comparable_model = []
    ml_directions = series["moneyline:directional"]
    for r, s in ml_directions:
        market = next(m for m in r["markets"] if m["market"] == "moneyline")
        if market["no_vig"]["home"] == 0.5:
            continue
        side = "home" if market["no_vig"]["home"] > 0.5 else "away"
        choice = {
            "market": "moneyline",
            "side": side,
            "line": None,
            "odds": market["prices"][side],
            "probability": market["no_vig"][side],
            "probability_edge": 0,
            "ev": expected_value(market["no_vig"][side], market["prices"][side]),
        }
        favorite_bets.append(
            (r, dict(choice, **settle(choice, r["actual_home_score"], r["actual_away_score"])))
        )
        comparable_model.append((r, s))
    comparison["moneyline"]["flat_stake_same_decisive_quotes"] = {
        "model": bet_metrics(comparable_model),
        "market_favorite": bet_metrics(favorite_bets),
        "paired_roi_difference": slate_bootstrap(
            [(r, s["units"] - f["units"]) for (r, s), (_, f) in zip(comparable_model, favorite_bets)]
        ),
    }
    for key, values in series.items():
        bets[key]["roi_slate_bootstrap"] = slate_bootstrap([(r, s["units"]) for r, s in values])

    result = {
        "schema_version": 1,
        "analysis_date": ANALYSIS_DATE,
        "provenance": "historical_replay",
        "stage": "first",
        "methodology": matchup.METHOD,
        "model_definition_commit": input_archive["model_definition_commit"],
        "market_source": input_archive["source"],
        "input_payload_sha256": input_archive["sha256"],
        "seed_payload_sha256": input_archive["seed_payload_sha256"],
        "implementation_sha256": input_archive["implementation_sha256"],
        "script_sha256": file_sha256(Path(__file__)),
        "config_sha256": input_archive["config_sha256"],
        "dependency_versions": {p: version(p) for p in ["numpy", "scipy", "PyYAML"]},
        "research_timing": {
            "result_availability": "12:00 UTC on the day after the Eastern tip date; assumed",
            "fit_cutoff": "Latest 12:00 UTC at or before the saved Sleeper capture",
            "quote_evidence": input_archive["timestamp_evidence"],
            "bootstrap": "Resample entire Eastern slate dates; seed 120; 10000 draws for market comparisons and 20000 for favorite reversals",
        },
        "full_replay": {
            "n": len(rows),
            "correct": sum(r["correct"] for r in rows),
            "margin": error_scores([r["margin_error"] for r in rows]),
            "total": error_scores([r["total_error"] for r in rows]),
            **binary_scores([r["output"]["wp_home"] for r in rows], [r["home_won"] for r in rows]),
            "wp_scale_range": [
                min(r["confidence"]["wp_a"] for r in rows),
                max(r["confidence"]["wp_a"] for r in rows),
            ],
        },
        "availability": {
            m: dict(Counter(r["quote_availability"][m] for r in rows)) for m in ["moneyline", "ats", "totals"]
        },
        "comparison": comparison,
        "betting": bets,
        "favorite_reversal": favorite_reversals(rows),
        "limitations": [
            "Historical replay with assumed result availability; pregame-as-of integrity cannot be certified from the retained legacy objects.",
            "Legacy quote timestamps are recorded first-snapshot times, not independently retained HTTP completion or sportsbook update timestamps.",
            "The favorite-reversal rule and below-50-percent split were proposed after inspecting these outcomes; no prospective registration is created.",
            "Small samples span only 14 moneyline/spread slates, 7 totals slates and 8 dates in the 17-game favorite subset.",
            "Reported bootstrap intervals are exploratory and unadjusted for model/strategy selection and multiple comparisons.",
            "Flat one-unit risk uses recorded offered-price payouts without execution, slippage or limit evidence. Series overlap and are never pooled.",
            "No native forecast, bet, grade, parameter or public headline is created or changed by this script.",
        ],
    }
    archive = json.loads((root / "research/output/defense_selection_2025-26.json").read_text())
    target = archive["selected_confidence_calibration"]["2026-27_legacy_first"]["current"]
    point = archive["current_season_check"][archive["selected_iteration"]]
    checks = {
        "log_loss": abs(result["full_replay"]["log_loss"] - target["log_loss"]),
        "brier": abs(result["full_replay"]["brier"] - target["brier"]),
        "margin_mae": abs(result["full_replay"]["margin"]["mae"] - point["margin"]["mae"]),
        "total_mae": abs(result["full_replay"]["total"]["mae"] - point["total"]["mae"]),
    }
    require(
        all(d < 1e-8 for d in checks.values()), "Replay does not reproduce the archived selected candidate"
    )
    result["reference_check_absolute_differences"] = checks
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    result = analyze(root)
    output = root / "research" / "output" / OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "report": str(output),
                "replayed_games": result["full_replay"]["n"],
                "paired_moneylines": result["comparison"]["moneyline"]["n"],
                "favorite_subset": result["favorite_reversal"]["groups"]["model_dog_below_50"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
