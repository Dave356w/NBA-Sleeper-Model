from __future__ import annotations

import math

from scipy.special import ndtr

from .common import dt


def valid_odds(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (value <= -100 or value >= 100)
    )


def implied(odds):
    if not valid_odds(odds):
        raise ValueError("Invalid American odds")
    return 100 / (100 + odds) if odds > 0 else abs(odds) / (100 + abs(odds))


def payout(odds):
    implied(odds)
    return odds / 100 if odds > 0 else 100 / abs(odds)


def no_vig(a, b):
    q1, q2 = implied(a), implied(b)
    return q1 / (q1 + q2), q2 / (q1 + q2)


def expected_value(probability, odds, push=0.0):
    if not 0 <= probability <= 1 or not 0 <= push <= 1 or probability + push > 1:
        raise ValueError("Invalid probability")
    return probability * payout(odds) - (1 - probability - push)


def half_point(value):
    return (
        isinstance(value, (int, float))
        and math.isfinite(value)
        and math.isclose(value % 1, 0.5, abs_tol=1e-9)
    )


def freshness(quote, captured_at, config):
    if quote.get("book") != config["book"]:
        return "different_book"
    try:
        pair = abs((dt(quote["captured_at"]) - dt(captured_at)).total_seconds())
        age = (dt(quote["captured_at"]) - dt(quote.get("updated_at") or quote["captured_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return "invalid_timestamp"
    if pair > config["market_pair_minutes"] * 60:
        return "unpaired_quote"
    if age < -config["market_clock_tolerance_seconds"]:
        return "future_timestamp"
    if age > config["market_max_age_minutes"] * 60:
        return "stale_quote"
    return None


def evaluate(output, params, quotes, captured_at, game, config, stage):
    markets, selections = [], []
    for quote in quotes:
        market = quote["market"]
        reason = freshness(quote, captured_at, config)
        if dt(quote["captured_at"]) >= dt(game["tip_utc"]):
            reason = "at_or_after_tip_quote"
        if stage == "lock":
            from datetime import timedelta

            target = dt(game["tip_utc"]) - timedelta(hours=config["lock_hours"])
            if (
                not target
                <= dt(quote["captured_at"])
                <= target + timedelta(minutes=config["lock_tolerance_minutes"])
            ):
                reason = "outside_lock_window_quote"
        prices = quote.get("prices", {})
        sides = ("home", "away") if market in ("moneyline", "ats") else ("over", "under")
        if set(prices) != set(sides) or not all(valid_odds(v) for v in prices.values()):
            reason = "invalid_two_sided_prices"
        if market != "moneyline":
            line = quote.get("line")
            if not half_point(line):
                reason = "unsupported_line_increment"
            if reason != "invalid_two_sided_prices" and not all(
                config["price_bounds"][0] <= v <= config["price_bounds"][1] for v in prices.values()
            ):
                reason = "outside_price_range"
        record = {**quote, "eligible": reason is None, "unavailable_reason": reason}
        if reason in {"invalid_two_sided_prices", "unsupported_line_increment", "outside_price_range"}:
            markets.append(record)
            continue
        q = no_vig(prices[sides[0]], prices[sides[1]])
        if market == "moneyline":
            p = output["wp_home"]
            direction = output["winner"]
            edge_points = None
        elif market == "ats":
            edge_points = output["ats_margin"] + quote["line"]
            p = float(ndtr(edge_points / params["ats_sigma"]))
            direction = "home" if edge_points > 0 else "away" if edge_points < 0 else None
        else:
            edge_points = output["adjusted_total"] - quote["line"]
            p = float(ndtr(edge_points / params["totals_sigma"]))
            direction = "over" if edge_points > 0 else "under" if edge_points < 0 else None
        probabilities = {sides[0]: p, sides[1]: 1 - p}
        edges = {s: probabilities[s] - q[i] for i, s in enumerate(sides)}
        evs = {s: expected_value(probabilities[s], prices[s]) for s in sides}
        record.update(
            probabilities=probabilities,
            no_vig=dict(zip(sides, q, strict=True)),
            edges=edges,
            ev=evs,
            edge_points=edge_points,
            direction=direction,
        )
        markets.append(record)
        if reason:
            continue

        def selection(side, series):
            return {
                "market": market,
                "side": side,
                "series": series,
                "book": quote["book"],
                "line": quote.get("line"),
                "odds": prices[side],
                "probability": probabilities[side],
                "probability_edge": edges[side],
                "ev": evs[side],
                "quote_id": quote["quote_id"],
                "favorite": prices[side] < 0,
            }

        if direction:
            selections.append(selection(direction, "directional"))
        if market == "ats":
            if direction and abs(edge_points) >= config["ats_subset_points"]:
                selections.append(selection(direction, "edge_3plus"))
        else:
            for side in sides:
                if edges[side] >= config["value_probability_edge"] and (
                    market != "moneyline" or evs[side] > 0
                ):
                    selections.append(selection(side, "value"))
    return markets, selections


def settle(selection, home_score, away_score):
    side, market = selection["side"], selection["market"]
    if market == "moneyline":
        value = home_score - away_score
        value *= 1 if side == "home" else -1
    elif market == "ats":
        value = home_score - away_score + selection["line"]
        value *= 1 if side == "home" else -1
    else:
        value = home_score + away_score - selection["line"]
        value *= 1 if side == "over" else -1
    result = "win" if value > 0 else "loss" if value < 0 else "push"
    units = payout(selection["odds"]) if value > 0 else -1.0 if value < 0 else 0.0
    return {"outcome": result, "units": units}
