"""Independent, bounded HTTP adapters. No imported production-model code."""

from __future__ import annotations

import ssl
import time
from datetime import timedelta

import httpx

from .common import ET, digest, dt, iso, team

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SleeperNBAResearch/1.19)", "Accept": "application/json"}


class SourceError(RuntimeError):
    pass


class HTTP:
    def __init__(self):
        self.client = httpx.Client(
            headers=HEADERS, timeout=40, verify=ssl.create_default_context(), follow_redirects=True
        )
        self.last = 0.0

    def request(self, url, params=None):
        # Keep request-start capture local to each actual attempt.
        for attempt in range(3):
            time.sleep(max(0, 0.12 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            start = iso()
            try:
                response = self.client.get(url, params=params)
                response.raise_for_status()
                return {"url": url, "requested_at": start, "captured_at": iso(), "payload": response.json()}
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    raise SourceError(f"{url.split('?')[0]}: request failed after 3 attempts") from None
                time.sleep(attempt + 1)

    def close(self):
        self.client.close()


def sleeper_feed(http):
    state = http.request("https://api.sleeper.app/v1/state/nba")
    value = state["payload"]
    if not isinstance(value, dict) or not value.get("season") or not value.get("week"):
        raise SourceError("Invalid Sleeper season state")
    season, week = int(value["season"]), int(value["week"])
    envelopes, warnings = [], []
    for w in sorted({max(1, week - 1), week, week + 1}):
        try:
            envelope = http.request(
                f"https://api.sleeper.com/projections/nba/{season}/{w}", params={"season_type": "regular"}
            )
            if not isinstance(envelope["payload"], list):
                raise SourceError("Invalid Sleeper projection response")
            envelopes.append(envelope)
        except SourceError:
            if w == week:
                raise
            warnings.append(f"adjacent_week_{w}_unavailable")
    # Adjacent weeks represent distinct game dates. Conflicts are checked per game.
    rows = [r for e in envelopes for r in e["payload"]]
    return rows, envelopes, state, warnings


def schedule(http, slate_date):
    envelope = http.request(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
        params={"dates": slate_date.replace("-", ""), "limit": 100},
    )
    payload = envelope["payload"]
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise SourceError("Invalid schedule schema")
    games = []
    for event in payload["events"]:
        competitions = event.get("competitions") or []
        if len(competitions) != 1:
            continue
        comp = competitions[0]
        competitors = {r["homeAway"]: r for r in comp.get("competitors", [])}
        if set(competitors) != {"home", "away"}:
            continue
        status = event.get("status", comp.get("status", {})).get("type", {})
        season_type = event.get("season", {}).get("type")
        title = " ".join(str(n.get("headline", "")) for n in comp.get("notes", [])).lower()
        excluded = any(x in title for x in ["all-star", "exhibition", "cup championship", "cup final"])
        h, a = competitors["home"], competitors["away"]
        tip = dt(event["date"])
        game = {
            "game_id": str(event["id"]),
            "tip_utc": iso(tip),
            "slate_date": tip.astimezone(ET).date().isoformat(),
            "home": team(h["team"]["abbreviation"]),
            "away": team(a["team"]["abbreviation"]),
            "home_name": h["team"]["displayName"],
            "away_name": a["team"]["displayName"],
            "status": status.get("state", "unknown"),
            "status_name": status.get("name", ""),
            "completed": status.get("completed", False),
            "competition_class": "regular" if season_type == 2 and not excluded else "excluded",
            "season": int(event.get("season", {}).get("year", tip.year)) - 1,
            "home_score": int(h.get("score") or 0),
            "away_score": int(a.get("score") or 0),
            "source_captured_at": envelope["captured_at"],
            "source_requested_at": envelope["requested_at"],
            "source_url": envelope["url"],
            "source_hash": digest(event),
        }
        game["schedule_revision"] = digest(
            {
                k: game[k]
                for k in ["game_id", "tip_utc", "home", "away", "competition_class", "status", "status_name"]
            }
        )
        games.append((game, event))
    return games, envelope


def _number(value):
    try:
        return float(str(value).replace("−", "-"))
    except (ValueError, TypeError):
        return None


def espn_quotes(game, event):
    """ESPN current prices, timestamped on response completion per owner policy."""
    quotes = []
    for odds in event["competitions"][0].get("odds", []):
        name = odds.get("provider", {}).get("name", "").lower().replace(" ", "")
        if name not in {"draftkings", "draftking"}:
            continue
        money = odds.get("moneyline", {})
        spread = odds.get("pointSpread", {})
        total = odds.get("total", {})
        for market, structure, sides in [
            ("moneyline", money, ("home", "away")),
            ("ats", spread, ("home", "away")),
            ("totals", total, ("over", "under")),
        ]:
            pair = {s: structure.get(s, {}).get("close", {}) for s in sides}
            prices = {s: _number(pair[s].get("odds")) for s in sides}
            if any(v is None for v in prices.values()):
                continue
            line = None
            if market == "ats":
                line = _number(pair["home"].get("line"))
                other = _number(pair["away"].get("line"))
                if line is None or other is None or abs(line + other) > 1e-8:
                    continue
            elif market == "totals":
                line = _number(str(pair["over"].get("line", "")).lstrip("oOuU"))
                other = _number(str(pair["under"].get("line", "")).lstrip("oOuU"))
                if line is None or line != other:
                    continue
            quote = {
                "market": market,
                "book": "draftkings",
                "provider": "espn",
                "captured_at": game["source_captured_at"],
                "updated_at": None,
                "line": line,
                "prices": prices,
                "raw": structure,
                "provider_event_id": game["game_id"],
            }
            quote["quote_id"] = digest(quote)
            quotes.append(quote)
    return quotes


def dates_around(now):
    today = now.astimezone(ET).date()
    return [(today + timedelta(days=i)).isoformat() for i in (-1, 0, 1)]
