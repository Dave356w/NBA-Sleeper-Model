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
from .models import adaptive_defaults, fit_parameters

QUOTES_FILE = "research/inputs/v1_20_market_quotes_2026-11-09.json"
# v1.30 (owner update, 19 November 2026): replay only snapshots captured on the game's Eastern date.
GAME_DAY_REPLAY = "game_day_et_v1"
SEGMENTS = [
    ("replay_legacy", "Replay · archived first snapshots"),
    ("replay_native", "Replay · earlier-version captures"),
    ("live", "Live"),
]
PUBLIC_ROW_FIELDS = (
    "game_id",
    "slate_date",
    "tip_utc",
    "home",
    "away",
    "segment",
    "snapshot_stage",
    "margin",
    "wp_home",
    "actual_home_score",
    "actual_away_score",
    "ml",
    "price_basis",
)
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
    """Seed games plus resolved earlier-version native snapshots, one per game."""
    return _replay(records, root, config, season)[0]


def _replay(records, root, config, season):
    """Replay observations and, under `replay_snapshot: game_day_et_v1`, the games it excludes.

    Without the key (through v1.29) a game replays its latest earlier-version native first
    snapshot, whenever it was captured. From v1.30 every replayed snapshot must be captured
    on the game's Eastern date: a game-day native first snapshot is preferred, then a
    game-day native lock; a game with neither keeps a game-day seed or backfill snapshot if
    it has one and is otherwise excluded from the replay and listed with the reason.
    """
    observations, game_day = {}, config.get("replay_snapshot") == GAME_DAY_REPLAY
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
            if game_day and not matchup.on_game_day(o["snapshot_utc"], o["tip_utc"]):
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
                "snapshot_stage": "first",
                "provenance": "legacy-first",
                "markets": _seed_markets(quotes.get(o["game_id"]), o, config),
            }
        # Owner-authorized outage backfill (17 November 2026): archived early-day snapshots for
        # games with no native forecast, scored and timed exactly like the legacy seed games.
        for backfill in matchup.backfill_files(root, config):
            for o in backfill["observations"]:
                if o["stage"] != "first" or o["season"] != season or o["game_id"] in observations:
                    continue
                if game_day and not matchup.on_game_day(o["snapshot_utc"], o["tip_utc"]):
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
                    "snapshot_stage": "first",
                    "provenance": o["provenance"],
                    "markets": _seed_markets(o.get("quotes"), o, config),
                }
    settings = config.get("matchup", {})
    if settings.get("team_minutes_cap") is not None:
        # v1.32: seed and backfill teams projected above a full game's minutes are scaled down,
        # from the pinned team minutes of those snapshots.
        observations = dict(
            zip(observations, matchup.cap_seed(list(observations.values()), root, settings), strict=True)
        )
    retired = {r["instance_id"] for r in records if r["kind"] == "retired"}
    results, revisions = _latest_results(records), _result_revisions(records)
    # A game forecast under several earlier tags replays its latest earlier-version first
    # snapshot. Ledger order follows transaction hashes, so the order is fixed explicitly.
    # From v1.30 only game-day snapshots qualify, and a game-day lock stands in for a
    # first snapshot captured on an earlier day.
    stages = ("first", "lock") if game_day else ("first",)
    chosen, skipped = {}, {}
    for f in sorted(
        (r for r in records if r["kind"] == "forecast"), key=lambda r: (dt(r["captured_at"]), r["id"])
    ):
        if f["stage"] not in stages or f.get("provenance") != "native":
            continue
        if _method(f) == config["methodology"] or f["instance_id"] in retired:
            continue
        result, game = results.get(f["instance_id"]), f["game"]
        if result is None or matchup.season_of(game) != season or "home_score" not in f["raw"]:
            continue
        if result["home_score"] == result["away_score"]:
            continue
        if game_day and not matchup.on_game_day(f["captured_at"], game["tip_utc"]):
            skipped[game["game_id"]] = f
            continue
        held = chosen.get(game["game_id"])
        if held is None or held["stage"] == f["stage"] or f["stage"] == "first":
            chosen[game["game_id"]] = f  # Latest of the preferred stage.
    for game_id, f in chosen.items():
        game = f["game"]
        result = results[f["instance_id"]]
        observations[game_id] = {
            "game_id": game_id,
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
            "snapshot_stage": f["stage"],
            "provenance": "native",
            "markets": _forecast_markets(f, "fixed"),
        }
        if settings.get("team_minutes_cap") is not None:
            minutes = f["raw"].get("team_minutes", {})
            observations[game_id] = matchup.capped(
                observations[game_id], minutes.get(game["home"]), minutes.get(game["away"]), settings
            )
    excluded = [
        {
            "game_id": game_id,
            "tip_utc": f["game"]["tip_utc"],
            "home": f["game"]["home"],
            "away": f["game"]["away"],
            "snapshot_utc": f["captured_at"],
            "reason": "no_game_day_capture",
        }
        for game_id, f in skipped.items()
        if game_id not in observations
    ]
    ordered = sorted(observations.values(), key=lambda o: (dt(o["snapshot_utc"]), o["game_id"]))
    return ordered, sorted(excluded, key=lambda e: (dt(e["tip_utc"]), e["game_id"]))


def replay_rows(observations, config):
    """Score each replay game from earlier usable games only."""
    settings = config["matchup"]
    versions, params_at, rows = {}, {}, []
    params, last_version = adaptive_defaults(config), matchup.fit([], settings, iso())
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
        scoring, _ = matchup.apply(raw, o, versions[cutoff], settings)
        scale = params_at[cutoff]["wp_a"]
        rows.append(
            {
                "game_id": o["game_id"],
                "home": o["home"],
                "away": o["away"],
                "tip_utc": o["tip_utc"],
                "segment": o["segment"],
                "snapshot_stage": o.get("snapshot_stage", "first"),
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


def calibration_observations(records, root, config, cutoff):
    """Replay games usable at `cutoff`, as confidence-calibration observations (v1.24).

    Each margin is the replayed forecast under the current methodology, scored
    from team fits that used earlier results only. Scores are read as they were
    known at the cutoff. Nothing is written to the ledger.
    """
    seasons = [matchup.season_of(r["game"]) for r in records if r["kind"] in ("forecast", "schedule")]
    season = max(seasons, default=config.get("matchup", {}).get("seed_season"))
    if season is None or not matchup.enabled(config):
        return []
    cutoff, observations = dt(cutoff), []
    for row in replay_rows(replay_observations(records, root, config, season), config):
        if dt(row["available_at"]) > cutoff:
            continue
        home, away = _scores_at(row, cutoff)
        if home == away:
            continue
        observations.append(
            {
                "game_id": row["game_id"],
                "provenance": "replay",
                "segment": row["segment"],
                "verified_at": row["available_at"],
                "margin": row["margin"],
                "home_won": home > away,
                "ats_residual": home - away - row["margin"],
                "totals_residual": home + away - row["total"],
            }
        )
    return sorted(observations, key=lambda o: (dt(o["verified_at"]), o["game_id"]))


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
                "home": f["game"]["home"],
                "away": f["game"]["away"],
                "tip_utc": f["game"]["tip_utc"],
                "actual_home_score": result["home_score"],
                "actual_away_score": result["away_score"],
                "segment": "live",
                "snapshot_stage": "first",
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
        return {
            "n": 0,
            "betting": _bets([], config),
            "versus_market": _versus_market([]),
            "favorite_benchmark": favorite_benchmark([]),
        }
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
        "favorite_benchmark": favorite_benchmark(rows),
    }


def favorite_benchmark(rows):
    """Report equal-stake model/favorite returns on the exact same priced games.

    A pick'em quote has no favorite. Exclude it from both sides of this paired
    benchmark, as well as tied model projections. This never registers a bet.
    """
    model, favorite, agreements, pickems = [], [], 0, 0
    for row in rows:
        if not row["ml"]:
            continue
        prices = row["ml"]
        q_home, q_away = no_vig(prices["home"], prices["away"])
        if q_home == q_away:
            pickems += 1
            continue
        winner = row.get("winner", "home" if row["margin"] > 0 else "away" if row["margin"] < 0 else None)
        if winner not in {"home", "away"}:
            continue
        side = "home" if q_home > q_away else "away"
        actual = "home" if row["actual_margin"] > 0 else "away"
        model.append(payout(prices[winner]) if winner == actual else -1.0)
        favorite.append(payout(prices[side]) if side == actual else -1.0)
        agreements += winner == side

    def returns(values):
        return {
            "wins": sum(v > 0 for v in values),
            "losses": sum(v < 0 for v in values),
            "units": float(sum(values)),
            "roi": float(np.mean(values)) if values else None,
        }

    return {
        "n": len(model),
        "model": returns(model),
        "favorite": returns(favorite),
        "agreements": agreements,
        "pickems_excluded": pickems,
    }


def closing_prices(root, config):
    """Owner-authorized closing-price backfill (v1.27): hash-pinned ESPN DraftKings closes by game."""
    entry = config.get("closing_backfill")
    if not entry:
        return {}
    path = Path(root) / entry["file"]
    data = json.loads(path.read_text())
    body = {k: v for k, v in data.items() if k != "sha256"}
    if digest(body) != data["sha256"] or data["sha256"] != entry["sha256"]:
        raise ValueError("Closing-price backfill integrity failure")
    return {r["game_id"]: r for r in data["rows"] if r.get("book") == config["book"]}


def with_closing_prices(rows, closes):
    """Price each market a game's frozen snapshot left unpriced at the DraftKings close.

    Reporting only: the season record's selections are re-derived at render time
    and never written, graded or used for training. `price_basis` names the
    moneyline's source (snapshot or closing); spread and total lines carry their
    own basis in `line_basis`.
    """
    out = []
    for r in rows:
        r = {**r, "price_basis": "snapshot" if r["ml"] else None, "line_basis": {}}
        close = (closes.get(r["game_id"]) or {}).get("close")
        same_teams = close is not None and (closes[r["game_id"]]["home"], closes[r["game_id"]]["away"]) == (
            r["home"],
            r["away"],
        )
        ml = (close or {}).get("moneyline", {})
        if not r["ml"] and same_teams and valid_odds(ml.get("home")) and valid_odds(ml.get("away")):
            r["ml"] = {"home": ml["home"], "away": ml["away"]}
            r["price_basis"] = "closing"
        for key, market, field in (("spread", "spread", "line"), ("total_line", "total", "line")):
            if r[key] is not None:
                r["line_basis"][market] = "snapshot"
            elif same_teams and _number(close[market].get(field)) is not None:
                r[key] = _number(close[market][field])
                r["line_basis"][market] = "closing"
        out.append(r)
    return out


def season_record(records, root, config):
    """Pooled first-snapshot record for the current season, with its parts kept visible."""
    seasons = [matchup.season_of(r["game"]) for r in records if r["kind"] in ("forecast", "schedule")]
    season = max(seasons, default=config.get("matchup", {}).get("seed_season"))
    if season is None or not matchup.enabled(config):
        return None
    observations, excluded = _replay(records, root, config, season)
    rows = replay_rows(observations, config)
    live = live_rows(records, config, season)
    seen = {r["game_id"] for r in live}
    rows = [r for r in rows if r["game_id"] not in seen] + live
    rows = with_closing_prices(rows, closing_prices(root, config))
    basis = [r["price_basis"] for r in rows]
    return {
        "season": f"{season}–{str(season + 1)[-2:]}",
        "stage": "first",
        "methodology": config["methodology"],
        "pooled": metrics(rows, config),
        # One public row per game of the pooled record, in tip order.
        "rows": [
            {k: r[k] for k in PUBLIC_ROW_FIELDS}
            for r in sorted(rows, key=lambda r: (dt(r["tip_utc"]), r["game_id"]))
        ],
        "segments": [
            {"key": key, "label": label, **metrics([r for r in rows if r["segment"] == key], config)}
            for key, label in SEGMENTS
        ],
        # v1.30: replayed snapshots are captured on the game's Eastern date; a game-day lock
        # stands in for an earlier-day first snapshot, and games with neither are listed here.
        "replay_snapshot": config.get("replay_snapshot"),
        "replay_excluded": [e for e in excluded if e["game_id"] not in seen],
        "replay_clock": "Seed results usable at 12 UTC on the day after the Eastern tip date (assumed); "
        "earlier-version captures use their recorded verification times.",
        # v1.27: where a frozen snapshot had no price, the moneyline is the DraftKings close.
        "price_basis": {
            "counts": {k or "none": basis.count(k) for k in ("snapshot", "closing", None) if basis.count(k)},
            "snapshot": metrics([r for r in rows if r["price_basis"] == "snapshot"], config),
            "closing": metrics([r for r in rows if r["price_basis"] == "closing"], config),
        },
    }
