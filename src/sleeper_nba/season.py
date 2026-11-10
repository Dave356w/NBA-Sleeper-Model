"""Season-to-date reporting record: replayed earlier games pooled with live results.

Reporting only. Replay rows are computed at build time from the pinned seed and
from earlier-version native captures; nothing here is written to the ledger,
registered as a bet, graded or used to train live parameters.

Replay chronology: a game's result becomes usable at its recorded verification
time, or at 12:00 UTC on the day after its Eastern tip date for seed games,
whose original verification times were not retained. Each forecast uses the
latest noon-UTC fit at or before its capture. Team residuals and the
win-probability scale are fitted from earlier usable games only.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import numpy as np
from scipy.special import ndtr

from . import matchup
from .common import ET, digest, dt, iso
from .markets import expected_value, freshness, no_vig, payout, valid_odds
from .models import DEFAULTS, fit_parameters

QUOTES_FILE = "research/inputs/v1_20_market_quotes_2026-11-09.json"
SEGMENTS = [
    ("replay_legacy", "Replay · archived first snapshots"),
    ("replay_native", "Replay · earlier-version captures"),
    ("live", "Live"),
]
USABLE_LINE_REASONS = {None, "unsupported_line_increment", "outside_price_range"}


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _noon_cutoff(captured):
    noon = datetime.combine(captured.date(), time(12), UTC)
    return noon if noon <= captured else noon - timedelta(days=1)


def _seed_quotes(root, config):
    """Archived first-snapshot DraftKings quotes for the seed games, if retained."""
    path = Path(root) / QUOTES_FILE
    if not path.exists():
        return {}
    archive = json.loads(path.read_text())
    if digest({k: v for k, v in archive.items() if k != "sha256"}) != archive["sha256"]:
        raise ValueError("Season replay market input checksum mismatch")
    return {row["game_id"]: row for row in archive["rows"]}


def _paired(stamp, observation, config):
    if not stamp:
        return False
    captured = dt(stamp)
    gap = abs((captured - dt(observation["snapshot_utc"])).total_seconds())
    return gap <= config["market_pair_minutes"] * 60 and captured < dt(observation["tip_utc"])


def _seed_markets(row, observation, config):
    markets = {"ml": None, "spread": None, "total_line": None}
    if row is None:
        return markets
    if _paired(row.get("first_snapshot_utc"), observation, config):
        home, away = _number(row.get("first_home_ml")), _number(row.get("first_away_ml"))
        if valid_odds(home) and valid_odds(away):
            markets["ml"] = {"home": home, "away": away}
        markets["spread"] = _number(row.get("first_spread"))
    if _paired(row.get("first_ou_snapshot_utc"), observation, config):
        markets["total_line"] = _number(row.get("first_ou_line"))
    return markets


def _contemporaneous(quote, forecast):
    """Paired, fresh and pregame under the forecast's frozen timing rules.

    A stored reason such as an integer line can mask a timing failure, so the
    timestamps are rechecked here instead of trusting the final reason.
    """
    try:
        if freshness(quote, forecast["captured_at"], forecast["config"]) is not None:
            return False
        return dt(quote["captured_at"]) < dt(forecast["game"]["tip_utc"])
    except (KeyError, ValueError, TypeError):
        return False


def _forecast_markets(forecast, variant):
    markets = {"ml": None, "spread": None, "total_line": None}
    for m in forecast["variants"][variant].get("markets", []):
        if m["market"] == "moneyline":
            if m.get("eligible"):
                markets["ml"] = {"home": m["prices"]["home"], "away": m["prices"]["away"]}
        elif m.get("unavailable_reason") not in USABLE_LINE_REASONS or not _contemporaneous(m, forecast):
            continue
        elif m["market"] == "ats":
            markets["spread"] = _number(m.get("line"))
        elif m["market"] == "totals":
            markets["total_line"] = _number(m.get("line"))
    return markets


def _result_revisions(records):
    """Every verified result per game instance, oldest first."""
    revisions = {}
    for r in records:
        if r["kind"] == "result":
            revisions.setdefault(r["instance_id"], []).append(r)
    for values in revisions.values():
        values.sort(key=lambda r: dt(r["verified_at"]))
    return revisions


def _latest_results(records):
    return {instance: values[-1] for instance, values in _result_revisions(records).items()}


def _scores_at(row, cutoff):
    """The result as it was known at a cutoff; later corrections are not visible."""
    known = [r for r in row.get("revisions", []) if dt(r[0]) <= cutoff]
    return (known[-1][1], known[-1][2]) if known else (row["actual_home_score"], row["actual_away_score"])


def _method(record):
    return record.get("methodology", record.get("config", {}).get("methodology", "sleeper_only_v1_19"))


def replay_observations(records, root, config, season):
    """Seed games plus resolved earlier-version native first snapshots, one per game."""
    observations = {}
    seed_file = config.get("matchup", {}).get("seed_file")
    if seed_file and (Path(root) / seed_file).exists():
        seed = json.loads((Path(root) / seed_file).read_text())
        body = {k: v for k, v in seed.items() if k != "sha256"}
        if digest(body) != seed["sha256"] or seed["sha256"] != config["matchup"]["seed_sha256"]:
            raise ValueError("Season replay seed integrity failure")
        quotes = _seed_quotes(root, config)
        for o in seed["observations"]:
            if o["stage"] != "first" or o["season"] != season:
                continue
            day = dt(o["tip_utc"]).astimezone(ET).date() + timedelta(days=1)
            observations[o["game_id"]] = {
                **{k: o[k] for k in ["game_id", "home", "away", "tip_utc", "snapshot_utc"]},
                "projected_home_score": o["projected_home_score"],
                "projected_away_score": o["projected_away_score"],
                "actual_home_score": o["actual_home_score"],
                "actual_away_score": o["actual_away_score"],
                "available_at": iso(datetime.combine(day, time(12), UTC)),
                "segment": "replay_legacy",
                "provenance": "legacy-first",
                "markets": _seed_markets(quotes.get(o["game_id"]), o, config),
            }
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    results, revisions = _latest_results(records), _result_revisions(records)
    for f in records:
        if f["kind"] != "forecast" or f["stage"] != "first" or f.get("provenance") != "native":
            continue
        if _method(f) == config["methodology"] or f["instance_id"] in retired:
            continue
        result, game = results.get(f["instance_id"]), f["game"]
        if result is None or matchup.season_of(game) != season or "home_score" not in f["raw"]:
            continue
        if result["home_score"] == result["away_score"]:
            continue
        observations[game["game_id"]] = {
            "game_id": game["game_id"],
            "home": game["home"],
            "away": game["away"],
            "tip_utc": game["tip_utc"],
            "snapshot_utc": f["captured_at"],
            "projected_home_score": f["raw"]["home_score"],
            "projected_away_score": f["raw"]["away_score"],
            "actual_home_score": result["home_score"],
            "actual_away_score": result["away_score"],
            # Usable from its first verification; corrections apply from their own times.
            "available_at": revisions[f["instance_id"]][0]["verified_at"],
            "revisions": [
                (r["verified_at"], r["home_score"], r["away_score"]) for r in revisions[f["instance_id"]]
            ],
            "segment": "replay_native",
            "provenance": "native",
            "markets": _forecast_markets(f, "fixed"),
        }
    return sorted(observations.values(), key=lambda o: (dt(o["snapshot_utc"]), o["game_id"]))


def replay_rows(observations, config):
    """Score each replay game from earlier usable games only."""
    settings = config["matchup"]
    versions, params_at, rows = {}, {}, []
    params, last_version = dict(DEFAULTS), matchup.fit([], settings, iso())
    for o in observations:
        captured = dt(o["snapshot_utc"])
        cutoff = _noon_cutoff(captured)
        if cutoff not in versions:
            training = []
            for x in observations:
                if dt(x["available_at"]) <= cutoff and dt(x["tip_utc"]) <= cutoff:
                    home, away = _scores_at(x, cutoff)
                    if home != away:
                        training.append({**x, "actual_home_score": home, "actual_away_score": away})
            try:
                last_version = matchup.fit(training, settings, iso(cutoff))
            except (ValueError, np.linalg.LinAlgError):
                pass  # As in production, a failed fit keeps the previous version.
            versions[cutoff] = last_version
            usable = sorted(
                (r for r in rows if dt(r["available_at"]) <= cutoff),
                key=lambda r: (dt(r["available_at"]), r["game_id"]),
            )
            if len(usable) >= config["minimum_games"]:
                history = []
                for r in usable:
                    home, away = _scores_at(r, cutoff)
                    if home != away:
                        history.append(
                            {
                                "margin": r["margin"],
                                "home_won": home > away,
                                "ats_residual": home - away - r["margin"],
                                "totals_residual": home + away - r["total"],
                            }
                        )
                params, _, _ = fit_parameters(history, params, config)
            params_at[cutoff] = dict(params)
        raw = {"home_score": o["projected_home_score"], "away_score": o["projected_away_score"]}
        scoring, _ = matchup.apply(raw, o, versions[cutoff])
        scale = params_at[cutoff]["wp_a"]
        rows.append(
            {
                "game_id": o["game_id"],
                "segment": o["segment"],
                "slate_date": dt(o["tip_utc"]).astimezone(ET).date().isoformat(),
                "available_at": o["available_at"],
                "margin": scoring["margin"],
                "total": scoring["total"],
                "wp_home": float(ndtr(scoring["margin"] / scale)),
                "wp_a": scale,
                "actual_margin": o["actual_home_score"] - o["actual_away_score"],
                "actual_total": o["actual_home_score"] + o["actual_away_score"],
                "actual_home_score": o["actual_home_score"],
                "actual_away_score": o["actual_away_score"],
                "revisions": o.get("revisions", []),
                **o["markets"],
            }
        )
    return rows


def live_rows(records, config, season=None):
    """Graded native first snapshots of the current methodology, adaptive variant."""
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    results, rows = _latest_results(records), []
    for f in records:
        if f["kind"] != "forecast" or f["stage"] != "first" or f.get("provenance") != "native":
            continue
        if _method(f) != config["methodology"] or f["instance_id"] in retired:
            continue
        if season is not None and matchup.season_of(f["game"]) != season:
            continue
        result = results.get(f["instance_id"])
        if result is None or result["home_score"] == result["away_score"]:
            continue
        out = f["variants"]["adaptive"]
        scoring = out.get("scoring", f["raw"])
        rows.append(
            {
                "game_id": f["game"]["game_id"],
                "segment": "live",
                "slate_date": f["game"]["slate_date"],
                "available_at": result["verified_at"],
                "margin": scoring["margin"],
                "total": scoring["total"],
                "wp_home": out["wp_home"],
                "wp_a": out["params"]["wp_a"],
                "actual_margin": result["home_score"] - result["away_score"],
                "actual_total": result["home_score"] + result["away_score"],
                **_forecast_markets(f, "adaptive"),
            }
        )
    return rows


def _log_loss(p, won):
    p = min(max(p, 1e-12), 1 - 1e-12)
    return -math.log(p if won else 1 - p)


def _bets(rows, config):
    """Registered moneyline series at the recorded prices, one unit each."""
    series = {"directional": [], "value": []}
    for r in rows:
        if not r["ml"]:
            continue
        won_home = r["actual_margin"] > 0
        prices = r["ml"]
        q_home, q_away = no_vig(prices["home"], prices["away"])
        sides = [("home", r["wp_home"], q_home, won_home), ("away", 1 - r["wp_home"], q_away, not won_home)]
        if r["margin"] != 0:
            side = sides[0] if r["margin"] > 0 else sides[1]
            series["directional"].append(payout(prices[side[0]]) if side[3] else -1.0)
        for name, p, q, won in sides:
            if p - q >= config["value_probability_edge"] and expected_value(p, prices[name]) > 0:
                series["value"].append(payout(prices[name]) if won else -1.0)
    return {
        key: {
            "n": len(units),
            "wins": sum(u > 0 for u in units),
            "losses": sum(u < 0 for u in units),
            "units": float(sum(units)),
            "roi": float(np.mean(units)) if units else None,
        }
        for key, units in series.items()
    }


def _versus_market(rows):
    spread = [r for r in rows if r["spread"] is not None]
    total = [r for r in rows if r["total_line"] is not None]
    priced = [r for r in rows if r["ml"]]

    def mae(values):
        return float(np.mean(np.abs(values))) if len(values) else None

    return {
        "margin": {
            "n": len(spread),
            "model_mae": mae([r["actual_margin"] - r["margin"] for r in spread]),
            "market_mae": mae([r["actual_margin"] + r["spread"] for r in spread]),
        },
        "total": {
            "n": len(total),
            "model_mae": mae([r["actual_total"] - r["total"] for r in total]),
            "market_mae": mae([r["actual_total"] - r["total_line"] for r in total]),
        },
        "win_probability": {
            "n": len(priced),
            "model_log_loss": float(
                np.mean([_log_loss(r["wp_home"], r["actual_margin"] > 0) for r in priced])
            )
            if priced
            else None,
            "market_log_loss": float(
                np.mean(
                    [
                        _log_loss(no_vig(r["ml"]["home"], r["ml"]["away"])[0], r["actual_margin"] > 0)
                        for r in priced
                    ]
                )
            )
            if priced
            else None,
        },
    }


def metrics(rows, config):
    if not rows:
        return {"n": 0, "betting": _bets([], config), "versus_market": _versus_market([])}
    picks = [(r["margin"] > 0) == (r["actual_margin"] > 0) for r in rows if r["margin"] != 0]
    won = [r["actual_margin"] > 0 for r in rows]
    dates = sorted(r["slate_date"] for r in rows)
    return {
        "n": len(rows),
        "start_date": dates[0],
        "end_date": dates[-1],
        "winner_count": len(picks),
        "correct": int(sum(picks)),
        "accuracy": float(np.mean(picks)) if picks else None,
        "log_loss": float(np.mean([_log_loss(r["wp_home"], y) for r, y in zip(rows, won, strict=True)])),
        "brier": float(np.mean([(r["wp_home"] - y) ** 2 for r, y in zip(rows, won, strict=True)])),
        "margin_mae": float(np.mean([abs(r["actual_margin"] - r["margin"]) for r in rows])),
        "total_mae": float(np.mean([abs(r["actual_total"] - r["total"]) for r in rows])),
        "betting": _bets(rows, config),
        "versus_market": _versus_market(rows),
    }


def season_record(records, root, config):
    """Pooled first-snapshot record for the current season, with its parts kept visible."""
    seasons = [matchup.season_of(r["game"]) for r in records if r["kind"] in ("forecast", "schedule")]
    season = max(seasons, default=config.get("matchup", {}).get("seed_season"))
    if season is None or not matchup.enabled(config):
        return None
    rows = replay_rows(replay_observations(records, root, config, season), config)
    live = live_rows(records, config, season)
    seen = {r["game_id"] for r in live}
    rows = [r for r in rows if r["game_id"] not in seen] + live
    return {
        "season": f"{season}–{str(season + 1)[-2:]}",
        "stage": "first",
        "methodology": config["methodology"],
        "pooled": metrics(rows, config),
        "segments": [
            {"key": key, "label": label, **metrics([r for r in rows if r["segment"] == key], config)}
            for key, label in SEGMENTS
        ],
        "replay_clock": "Seed results usable at 12 UTC on the day after the Eastern tip date (assumed); "
        "earlier-version captures use their recorded verification times.",
    }
