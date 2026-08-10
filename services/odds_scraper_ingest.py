"""Shared scraper-to-master-cache ingestion helper.

Any bookmaker or odds scraper can call `ingest_events` with a list of events in
The Odds API master-cache format and have them merged into the Supabase
`odds_cache` blob. The existing +EV scanner (`execution_scanner`) and model
scripts then pick up the new lines without further wiring.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from db_manager import get_master_cache, save_master_cache
from master_odds_fetcher import _merge_cache
from utils.odds import american_to_decimal


def _dedupe_outcomes(outcomes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for outcome in outcomes:
        name = str(outcome.get("name") or "").strip()
        point = outcome.get("point")
        key = f"{name}:{point}"
        if key not in seen:
            seen[key] = outcome
    return list(seen.values())


def _coerce_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a scraper event into the canonical master-cache shape."""
    event_id = str(event.get("id") or event.get("event_id") or "").strip()
    home_team = str(
        event.get("home_team")
        or event.get("homeTeam")
        or event.get("home")
        or ""
    ).strip()
    away_team = str(
        event.get("away_team")
        or event.get("awayTeam")
        or event.get("away")
        or ""
    ).strip()
    commence_time = str(
        event.get("commence_time")
        or event.get("commenceTime")
        or event.get("start_time")
        or ""
    ).strip()

    bookmakers: List[Dict[str, Any]] = []
    for book in event.get("bookmakers") or []:
        book_key = str(book.get("key") or book.get("bookmaker") or "").strip().lower()
        if not book_key:
            continue
        book_title = str(book.get("title") or book.get("bookmaker_title") or book_key.title()).strip()
        markets: List[Dict[str, Any]] = []
        for market in book.get("markets") or []:
            market_key = str(market.get("key") or market.get("market_key") or "").strip()
            if not market_key:
                continue
            outcomes = _dedupe_outcomes(market.get("outcomes") or [])
            if not outcomes:
                continue
            markets.append({
                "key": market_key,
                "last_update": str(
                    market.get("last_update")
                    or datetime.now(timezone.utc).isoformat()
                ),
                "outcomes": outcomes,
            })
        if not markets:
            continue
        bookmakers.append({
            "key": book_key,
            "title": book_title,
            "markets": markets,
        })

    return {
        "id": event_id,
        "home_team": home_team,
        "away_team": away_team,
        "sport_key": str(event.get("sport_key") or "").strip(),
        "sport_title": str(event.get("sport_title") or "").strip(),
        "commence_time": commence_time,
        "bookmakers": bookmakers,
    }


def _coerce_outers(offer: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a minimal event from a flat offer list if a scraper sends offers instead."""
    event_id = str(offer.get("event_id") or "").strip()
    home_team = str(offer.get("home_team") or "").strip()
    away_team = str(offer.get("away_team") or "").strip()
    book_key = str(offer.get("bookmaker") or "").strip().lower()
    market_key = str(offer.get("market_key") or "").strip()
    outcome_name = str(offer.get("outcome_name") or "").strip()
    price = offer.get("price")
    point = offer.get("point")
    if not all((event_id, home_team, away_team, book_key, market_key, outcome_name, price)):
        return []

    return [{
        "id": event_id,
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": str(offer.get("commence_time") or "").strip(),
        "bookmakers": [{
            "key": book_key,
            "title": book_key.title(),
            "markets": [{
                "key": market_key,
                "last_update": datetime.now(timezone.utc).isoformat(),
                "outcomes": [{
                    "name": outcome_name,
                    "price": price,
                    "point": point,
                }],
            }],
        }],
    }]


def ingest_events(sport: str, events: List[Dict[str, Any]]) -> None:
    """Merge scraper events into the master cache and persist it."""
    if not events:
        return
    cache = get_master_cache() or {}
    coerced = [_coerce_event(event) for event in events if event]
    _merge_cache(cache, sport, coerced)
    save_master_cache(cache)
    print(f"[odds_scraper_ingest] {sport}: {len(coerced)} events merged into master cache")


def ingest_offers(sport: str, offers: List[Dict[str, Any]]) -> None:
    """Merge flat scraper offers into the master cache and persist it."""
    if not offers:
        return
    events: Dict[str, Dict[str, Any]] = {}
    for offer in offers:
        for event in _coerce_outers(offer):
            event_id = str(event["id"])
            if event_id not in events:
                events[event_id] = event
            else:
                # Merge bookmakers into existing event.
                existing_books = {b["key"]: b for b in events[event_id]["bookmakers"]}
                for book in event["bookmakers"]:
                    existing = existing_books.get(book["key"])
                    if not existing:
                        events[event_id]["bookmakers"].append(book)
                        existing_books[book["key"]] = book
                    else:
                        existing_markets = {m["key"]: m for m in existing["markets"]}
                        for market in book["markets"]:
                            if market["key"] not in existing_markets:
                                existing["markets"].append(market)
                                existing_markets[market["key"]] = market
                            else:
                                existing_markets[market["key"]]["outcomes"].extend(market["outcomes"])
    if events:
        ingest_events(sport, list(events.values()))


def extract_price(obj: Any) -> Optional[float]:
    """Best-effort extraction of decimal odds from a raw outcome/runner/fixture dict."""
    if not isinstance(obj, dict):
        return None

    # Direct decimal keys.
    for key in ("oddsDecimal", "decimalOdds", "decimal", "trueOdds", "trueOddsDecimal"):
        raw = obj.get(key)
        if raw is not None:
            try:
                return float(raw)
            except Exception:
                pass

    # Direct price/odds keys that may hold a number, decimal or American.
    for key in ("price", "odds", "americanOdds", "formattedOdds", "winRunnerOdds", "currentPrice"):
        raw = obj.get(key)
        if isinstance(raw, dict):
            for sub_key in ("decimal", "oddsDecimal", "price"):
                sub = raw.get(sub_key)
                if sub is not None:
                    try:
                        return float(sub)
                    except Exception:
                        pass
            for sub_key in ("american", "americanOdds", "price_american"):
                sub = raw.get(sub_key)
                if sub is not None:
                    try:
                        return float(american_to_decimal(sub))
                    except Exception:
                        pass
        else:
            try:
                value = float(raw)
                if value >= 100 or value <= -100:
                    return float(american_to_decimal(raw))
                if value > 1.0:
                    return value
            except Exception:
                pass
    return None


def _parse_matchup(matchup: str) -> Tuple[str, str]:
    away_team, home_team = matchup, matchup
    if " @ " in matchup:
        parts = matchup.split(" @ ", 1)
        away_team, home_team = parts[0].strip(), parts[1].strip()
    elif " at " in matchup.lower():
        import re as _re
        parts = _re.split(r"\s+at\s+", matchup, maxsplit=1, flags=_re.IGNORECASE)
        away_team, home_team = parts[0].strip(), parts[1].strip()
    elif " vs " in matchup.lower():
        import re as _re
        parts = _re.split(r"\s+vs\.?\s+", matchup, maxsplit=1, flags=_re.IGNORECASE)
        away_team, home_team = parts[0].strip(), parts[1].strip()
    return away_team, home_team


def ingest_current_lines(
    sport: str,
    book_key: str,
    market_key: str,
    current_lines: Dict[str, Dict[str, Any]],
) -> None:
    """Convert a {unique_key: line-dict} scraper map into master-cache events and merge."""
    if not current_lines:
        return

    by_event: Dict[str, Dict[str, Any]] = {}
    for line in current_lines.values():
        event_id = str(line.get("event_id") or "").strip()
        if not event_id:
            continue
        matchup = str(line.get("matchup") or "").strip()
        away_team, home_team = _parse_matchup(matchup)
        if event_id not in by_event:
            by_event[event_id] = {
                "id": event_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": str(line.get("commence_time") or "").strip(),
                "bookmakers": [
                    {
                        "key": book_key,
                        "title": book_key.title(),
                        "markets": [
                            {
                                "key": market_key,
                                "outcomes": [],
                            }
                        ],
                    }
                ],
            }
        price = line.get("price")
        if price is None:
            continue
        by_event[event_id]["bookmakers"][0]["markets"][0]["outcomes"].append(
            {
                "name": line.get("team"),
                "price": price,
                "point": line.get("line"),
            }
        )
    if by_event:
        ingest_events(sport, list(by_event.values()))
