import os
from typing import Dict, List

from db_manager import save_master_cache
from services.http_client import request
from utils.config import env_flag


ODDS_API_KEY = os.getenv("ODDS_API_KEY")
ODDS_API_KEY_2 = os.getenv("ODDS_API_KEY_2")
ODDS_API_KEY_3 = os.getenv("ODDS_API_KEY_3")

# Primary key stays on the low-cost core scan.
PRIMARY_CONFIG = {
    "basketball_nba": "spreads",
    "basketball_wnba": "spreads",
    "icehockey_nhl": "spreads",
    "baseball_mlb": "h2h",
}

# Secondary key is used only on markets that add the most useful upside.
# Cost: NBA 4 + NHL 2 = 6 credits/run, 12/day at two runs per day (360/month).
# WNBA removed to stay within the 500 credit/month free tier.
SECONDARY_CONFIG = {
    "basketball_nba": "h2h,totals",
    "icehockey_nhl": "h2h",
}

# Tertiary key expands the scan into totals and MLB run-line style markets
# that the unified scanner can actually alert on.
# Cost: NHL 2 + MLB 4 = 6 credits/run, 12/day at two runs per day.
TERTIARY_CONFIG = {
    "icehockey_nhl": "totals",
    "baseball_mlb": "spreads,totals",
}

REGIONS = os.getenv("ODDS_API_REGIONS", "us,eu")
TARGET_BOOKS = os.getenv(
    "ODDS_API_TARGET_BOOKS",
    "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks",
)
ENABLE_ODDS_SECONDARY_PULL = env_flag("ENABLE_ODDS_SECONDARY_PULL", True)
ENABLE_ODDS_TERTIARY_PULL = env_flag("ENABLE_ODDS_TERTIARY_PULL", True)


def _credits_for_config(config: Dict[str, str]) -> int:
    return sum(len(markets.split(",")) * 2 for markets in config.values())


def _merge_outcomes(existing_market: dict, incoming_market: dict) -> None:
    existing_outcomes = existing_market.setdefault("outcomes", [])
    outcome_index = {
        (str(outcome.get("name", "")).strip(), str(outcome.get("point", "")).strip()): outcome
        for outcome in existing_outcomes
    }
    for outcome in incoming_market.get("outcomes", []):
        key = (str(outcome.get("name", "")).strip(), str(outcome.get("point", "")).strip())
        outcome_index[key] = outcome
    existing_market["outcomes"] = list(outcome_index.values())


def _merge_bookmakers(existing_event: dict, incoming_event: dict) -> None:
    existing_books = existing_event.setdefault("bookmakers", [])
    book_index = {book.get("key"): book for book in existing_books}

    for incoming_book in incoming_event.get("bookmakers", []):
        incoming_key = incoming_book.get("key")
        if incoming_key not in book_index:
            existing_books.append(incoming_book)
            book_index[incoming_key] = incoming_book
            continue

        current_book = book_index[incoming_key]
        current_markets = current_book.setdefault("markets", [])
        market_index = {market.get("key"): market for market in current_markets}

        for incoming_market in incoming_book.get("markets", []):
            market_key = incoming_market.get("key")
            if market_key not in market_index:
                current_markets.append(incoming_market)
                market_index[market_key] = incoming_market
                continue
            _merge_outcomes(market_index[market_key], incoming_market)


def _merge_cache(cache: Dict[str, List[dict]], sport: str, events: List[dict]) -> None:
    if sport not in cache:
        cache[sport] = events
        return

    existing_events = cache[sport]
    event_index = {str(event.get("id")): event for event in existing_events}
    for event in events:
        event_id = str(event.get("id"))
        if event_id not in event_index:
            existing_events.append(event)
            event_index[event_id] = event
            continue
        _merge_bookmakers(event_index[event_id], event)


def _fetch_config(cache: Dict[str, List[dict]], api_key: str, config: Dict[str, str], label: str) -> int:
    success_count = 0
    for sport, markets in config.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            "apiKey": api_key,
            "regions": REGIONS,
            "markets": markets,
            "bookmakers": TARGET_BOOKS,
            "oddsFormat": "decimal",
        }

        try:
            response = request("GET", url, params=params, timeout=20)
            response.raise_for_status()
            events = response.json()
            _merge_cache(cache, sport, events)
            request_credits = len(markets.split(",")) * 2
            print(f"Cached {sport} ({markets}) via {label}. Credits used this request: {request_credits}")
            success_count += 1
        except Exception as exc:
            print(f"Error fetching {sport} via {label}: {exc}")
    return success_count


def run_fetcher():
    if not ODDS_API_KEY:
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    cache: Dict[str, List[dict]] = {}
    print(
        "BEE-BAKED FETCH: Running primary precision pull"
        f" ({_credits_for_config(PRIMARY_CONFIG)} credits/run)"
    )
    primary_success = _fetch_config(cache, ODDS_API_KEY, PRIMARY_CONFIG, "primary key")

    secondary_success = 0
    if ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL:
        print(
            "BEE-BAKED FETCH: Running secondary expansion pull"
            f" ({_credits_for_config(SECONDARY_CONFIG)} credits/run)"
        )
        secondary_success = _fetch_config(cache, ODDS_API_KEY_2, SECONDARY_CONFIG, "secondary key")
    elif not ENABLE_ODDS_SECONDARY_PULL:
        print("ENABLE_ODDS_SECONDARY_PULL=false. Skipping secondary expansion pull.")
    else:
        print("ODDS_API_KEY_2 not set. Skipping secondary expansion pull.")

    tertiary_success = 0
    if ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL:
        print(
            "BEE-BAKED FETCH: Running tertiary expansion pull"
            f" ({_credits_for_config(TERTIARY_CONFIG)} credits/run)"
        )
        tertiary_success = _fetch_config(cache, ODDS_API_KEY_3, TERTIARY_CONFIG, "tertiary key")
    elif not ENABLE_ODDS_TERTIARY_PULL:
        print("ENABLE_ODDS_TERTIARY_PULL=false. Skipping tertiary expansion pull.")
    else:
        print("ODDS_API_KEY_3 not set. Skipping tertiary expansion pull.")

    save_master_cache(cache)
    detail = (
        f"fetch complete | primary sports: {primary_success}/{len(PRIMARY_CONFIG)}"
        f" | secondary sports: {secondary_success}/{len(SECONDARY_CONFIG) if ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL else 0}"
        f" | tertiary sports: {tertiary_success}/{len(TERTIARY_CONFIG) if ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL else 0}"
    )
    return {"detail": detail, "count": len(cache), "label": "updates"}


if __name__ == "__main__":
    run_fetcher()
