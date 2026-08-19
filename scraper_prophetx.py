"""ProphetX exchange market-data ingest.

ProphetX runs a documented, key-authenticated partner API
(``/affiliate/get_tournaments`` -> ``get_sport_events`` -> ``v3/.../get_markets``),
so this is an API client rather than a scrape. Two consequences:

* It does **not** go through ScraperAPI. A partner key authenticates every
  request, so there is no WAF to bypass, and routing a credentialed call through
  a third-party residential proxy would hand our key to an extra hop for nothing.
* Without credentials there is nothing to fall back on. The task is skipped with
  a message instead of guessing at an unauthenticated endpoint.

Being an exchange, each market carries both sides with their own price and
available quantity, which is what ``_book_side`` preserves: the takeable price
plus the liquidity behind it. Backing one side of a two-way exchange market *is*
laying the other, so the two entries together are the bid/ask pair.
"""

from __future__ import annotations

import os
from typing import Any

from services.http_client import request
from services.odds_scraper_ingest import ingest_events

BOOK_KEY = "prophetx"
BOOK_TITLE = "ProphetX"
BASE_URL = os.getenv("PROPHETX_BASE_URL", "https://cash.api.prophetx.co/partner").rstrip("/")
MARKETS_VERSION = os.getenv("PROPHETX_MARKETS_VERSION", "v3")
# Comma-separated ProphetX tournament names to ingest, matched case-insensitively
# on a substring so "NFL" picks up "NFL — Regular Season".
TOURNAMENT_FILTER = os.getenv("PROPHETX_TOURNAMENTS", "MLB,NBA,WNBA,NFL,NHL")
MARKET_TYPES = os.getenv("PROPHETX_MARKET_TYPES", "moneyline,spread,total")
# Thinly-quoted lines move on a single order; ProphetX filters them server-side.
MIN_LIQUIDITY = os.getenv("PROPHETX_MIN_LIQUIDITY", "100")
EVENT_BATCH_SIZE = int(os.getenv("PROPHETX_EVENT_BATCH_SIZE", "20"))
TIMEOUT_SECONDS = int(os.getenv("PROPHETX_TIMEOUT_SECONDS", "20"))

MARKET_KEY_BY_TYPE = {
    "moneyline": "h2h",
    "money_line": "h2h",
    "spread": "spreads",
    "handicap": "spreads",
    "total": "totals",
    "totals": "totals",
    "over_under": "totals",
}
SPORT_KEY_BY_TOURNAMENT = {
    "mlb": "baseball_mlb",
    "nba": "basketball_nba",
    "wnba": "basketball_wnba",
    "nfl": "americanfootball_nfl",
    "nhl": "icehockey_nhl",
}


class ProphetXNotConfigured(RuntimeError):
    """No ProphetX partner credentials in the environment."""


def _auth_token() -> str:
    """Return the Authorization header value. ProphetX issues either a standing
    affiliate key or an access/secret pair exchanged for a token; both are sent
    raw, with no ``Bearer`` prefix."""
    api_key = (os.getenv("PROPHETX_API_KEY") or "").strip()
    if api_key:
        return api_key

    access_key = (os.getenv("PROPHETX_ACCESS_KEY") or "").strip()
    secret_key = (os.getenv("PROPHETX_SECRET_KEY") or "").strip()
    if not (access_key and secret_key):
        raise ProphetXNotConfigured(
            "set PROPHETX_API_KEY, or PROPHETX_ACCESS_KEY plus PROPHETX_SECRET_KEY"
        )

    response = request(
        "POST",
        f"{BASE_URL}/auth/login",
        json={"access_key": access_key, "secret_key": secret_key},
        headers={"Accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    token = str(((response.json() or {}).get("data") or {}).get("access_token") or "").strip()
    if not token:
        raise ProphetXNotConfigured("ProphetX /auth/login returned no access_token")
    return token


def _get(path: str, token: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    response = request(
        "GET",
        f"{BASE_URL}{path}",
        params=params,
        headers={"Authorization": token, "Accept": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    payload = response.json() or {}
    return payload if isinstance(payload, dict) else {}


def _wanted_tournaments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    wanted = [item.strip().lower() for item in TOURNAMENT_FILTER.split(",") if item.strip()]
    tournaments = (payload.get("data") or {}).get("tournaments") or []
    selected = []
    for tournament in tournaments:
        name = str(tournament.get("name") or "").lower()
        match = next((item for item in wanted if item in name), None)
        if match:
            selected.append({**tournament, "_sport_key": SPORT_KEY_BY_TOURNAMENT.get(match, "")})
    return selected


def _book_side(selection: dict[str, Any]) -> dict[str, Any] | None:
    """One side of an exchange market: the price on offer and the size behind it."""
    name = str(selection.get("name") or "").strip()
    try:
        price = float(selection.get("price"))
    except (TypeError, ValueError):
        return None
    if not name or price <= 1.0:
        return None

    outcome: dict[str, Any] = {"name": name, "price": round(price, 4)}
    line = selection.get("line")
    if line is not None:
        try:
            outcome["point"] = float(line)
        except (TypeError, ValueError):
            pass
    quantity = selection.get("quantity")
    if quantity is not None:
        try:
            # Depth at the quoted price; the desk uses it to size an order it can
            # actually fill rather than the top of a one-dollar book.
            outcome["liquidity"] = float(quantity)
        except (TypeError, ValueError):
            pass
    return outcome


def _iter_selections(market: dict[str, Any]) -> list[dict[str, Any]]:
    """v3 nests ``selections`` one list per side; v2 returns them flat."""
    flattened: list[dict[str, Any]] = []
    for entry in market.get("selections") or []:
        if isinstance(entry, list):
            flattened.extend(item for item in entry if isinstance(item, dict))
        elif isinstance(entry, dict):
            flattened.append(entry)
    return flattened


def _best_outcomes(market: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the best price per (side, line), summing the quantity at that price."""
    best: dict[tuple[str, Any], dict[str, Any]] = {}
    for selection in _iter_selections(market):
        outcome = _book_side(selection)
        if not outcome:
            continue
        key = (outcome["name"], outcome.get("point"))
        current = best.get(key)
        if current is None or outcome["price"] > current["price"]:
            best[key] = outcome
        elif outcome["price"] == current["price"] and "liquidity" in outcome:
            current["liquidity"] = current.get("liquidity", 0.0) + outcome["liquidity"]
    return list(best.values())


def build_event(sport_key: str, event: dict[str, Any], markets: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Assemble one master-cache event from a ProphetX event plus its markets."""
    event_id = str(event.get("event_id") or event.get("id") or "").strip()
    if not event_id:
        return None

    normalized: list[dict[str, Any]] = []
    for market in markets or []:
        if not isinstance(market, dict):
            continue
        market_key = MARKET_KEY_BY_TYPE.get(str(market.get("market_type") or "").strip().lower())
        if not market_key:
            continue
        outcomes = _best_outcomes(market)
        if not outcomes:
            continue
        normalized.append({"key": market_key, "outcomes": outcomes})

    if not normalized:
        return None
    return {
        "id": f"prophetx-{event_id}",
        "sport_key": sport_key,
        "home_team": str(event.get("home_team") or ""),
        "away_team": str(event.get("away_team") or ""),
        "commence_time": str(event.get("start_time") or ""),
        "bookmakers": [{"key": BOOK_KEY, "title": BOOK_TITLE, "markets": normalized}],
    }


def _markets_by_event(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """``get_multiple_markets`` answers with an event-keyed object, or sometimes a
    flat market list that has to be grouped by each market's ``event_id``."""
    data = payload.get("data")
    if isinstance(data, dict):
        return {str(key): value or [] for key, value in data.items()}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for market in data or []:
        if isinstance(market, dict):
            grouped.setdefault(str(market.get("event_id") or ""), []).append(market)
    return grouped


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    step = max(1, size)
    return [items[index : index + step] for index in range(0, len(items), step)]


def scrape_prophetx() -> dict[str, Any]:
    try:
        token = _auth_token()
    except ProphetXNotConfigured as exc:
        detail = f"prophetx skipped: {exc}"
        print(f"[prophetx] {detail}", flush=True)
        return {"detail": detail, "count": 0, "label": "events"}
    except Exception as exc:  # noqa: BLE001 - one dead venue must not fail the run
        detail = f"prophetx auth failed: {exc}"
        print(f"[prophetx] {detail}", flush=True)
        return {"detail": detail, "count": 0, "label": "events"}

    ingested = 0
    try:
        tournaments = _wanted_tournaments(
            _get("/affiliate/get_tournaments", token, [("has_active_events", "true")])
        )
        for tournament in tournaments:
            sport_key = tournament.get("_sport_key") or ""
            if not sport_key:
                continue
            events_payload = _get(
                "/affiliate/get_sport_events",
                token,
                [("tournament_id", str(tournament.get("id") or ""))],
            )
            events = (events_payload.get("data") or {}).get("sport_events") or []
            by_id = {str(item.get("event_id") or item.get("id") or ""): item for item in events}

            cache_events: list[dict[str, Any]] = []
            for batch in _batches([event_id for event_id in by_id if event_id], EVENT_BATCH_SIZE):
                params = [("event_ids", event_id) for event_id in batch]
                params.append(("market_types", MARKET_TYPES))
                params.append(("min_liquidity", MIN_LIQUIDITY))
                markets_payload = _get(
                    f"/{MARKETS_VERSION}/affiliate/get_multiple_markets", token, params
                )
                for event_id, markets in _markets_by_event(markets_payload).items():
                    source = by_id.get(event_id)
                    if not source:
                        continue
                    built = build_event(sport_key, source, markets)
                    if built:
                        cache_events.append(built)

            if cache_events:
                ingest_events(sport_key, cache_events)
                ingested += len(cache_events)
    except Exception as exc:  # noqa: BLE001 - see above
        detail = f"prophetx partial: {ingested} events ingested before {exc}"
        print(f"[prophetx] {detail}", flush=True)
        return {"detail": detail, "count": ingested, "label": "events"}

    detail = f"prophetx ingest complete | {ingested} events with two-sided prices"
    print(f"[prophetx] {detail}", flush=True)
    return {"detail": detail, "count": ingested, "label": "events"}


if __name__ == "__main__":
    scrape_prophetx()
