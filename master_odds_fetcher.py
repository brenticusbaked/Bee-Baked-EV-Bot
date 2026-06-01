import os
from typing import Dict, List

from db_manager import save_master_cache
from services.http_client import request
from utils.config import env_flag
from utils.seasons import filter_config_in_season


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

# Secondary key is used only on full-game markets that add the most useful upside.
# WNBA H2H/totals add more bet candidates during summer without the cost of alternate ladders.
SECONDARY_CONFIG = {
    "basketball_nba": "h2h,totals",
    "basketball_wnba": "h2h,totals",
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
ENABLE_ODDS_PARTIAL_MARKET_PULL = env_flag("ENABLE_ODDS_PARTIAL_MARKET_PULL", False)

PARTIAL_GAME_CONFIG = {
    "basketball_nba": "alternate_spreads,alternate_totals,spreads_q1,totals_q1,h2h_q1,spreads_h1,totals_h1,h2h_h1",
    "icehockey_nhl": "alternate_spreads,alternate_totals,spreads_1st_period,totals_1st_period,h2h_1st_period",
    "baseball_mlb": "alternate_spreads,alternate_totals,h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings",
    "americanfootball_nfl": "alternate_spreads,alternate_totals,spreads_q1,totals_q1,h2h_q1,spreads_h1,totals_h1,h2h_h1",
}


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

    active_primary = filter_config_in_season(PRIMARY_CONFIG)
    skipped_primary = set(PRIMARY_CONFIG) - set(active_primary)
    if skipped_primary:
        print(f"BEE-BAKED FETCH: Skipping off-season sports (primary): {', '.join(sorted(skipped_primary))}")

    print(
        "BEE-BAKED FETCH: Running primary precision pull"
        f" ({_credits_for_config(active_primary)} credits/run)"
    )
    primary_success = _fetch_config(cache, ODDS_API_KEY, active_primary, "primary key")

    secondary_success = 0
    if ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL:
        active_secondary = filter_config_in_season(SECONDARY_CONFIG)
        skipped_secondary = set(SECONDARY_CONFIG) - set(active_secondary)
        if skipped_secondary:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (secondary): {', '.join(sorted(skipped_secondary))}")
        print(
            "BEE-BAKED FETCH: Running secondary expansion pull"
            f" ({_credits_for_config(active_secondary)} credits/run)"
        )
        secondary_success = _fetch_config(cache, ODDS_API_KEY_2, active_secondary, "secondary key")
    elif not ENABLE_ODDS_SECONDARY_PULL:
        print("ENABLE_ODDS_SECONDARY_PULL=false. Skipping secondary expansion pull.")
    else:
        print("ODDS_API_KEY_2 not set. Skipping secondary expansion pull.")

    tertiary_success = 0
    if ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL:
        active_tertiary = filter_config_in_season(TERTIARY_CONFIG)
        skipped_tertiary = set(TERTIARY_CONFIG) - set(active_tertiary)
        if skipped_tertiary:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (tertiary): {', '.join(sorted(skipped_tertiary))}")
        print(
            "BEE-BAKED FETCH: Running tertiary expansion pull"
            f" ({_credits_for_config(active_tertiary)} credits/run)"
        )
        tertiary_success = _fetch_config(cache, ODDS_API_KEY_3, active_tertiary, "tertiary key")
    elif not ENABLE_ODDS_TERTIARY_PULL:
        print("ENABLE_ODDS_TERTIARY_PULL=false. Skipping tertiary expansion pull.")
    else:
        print("ODDS_API_KEY_3 not set. Skipping tertiary expansion pull.")

    partial_success = 0
    if ODDS_API_KEY_3 and ENABLE_ODDS_PARTIAL_MARKET_PULL:
        active_partial = filter_config_in_season(PARTIAL_GAME_CONFIG)
        skipped_partial = set(PARTIAL_GAME_CONFIG) - set(active_partial)
        if skipped_partial:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (partial/alternate): {', '.join(sorted(skipped_partial))}")
        print(
            "BEE-BAKED FETCH: Running partial-game and alternate-market pull"
            f" ({_credits_for_config(active_partial)} credits/run)"
        )
        partial_success = _fetch_config(cache, ODDS_API_KEY_3, active_partial, "partial/alternate key")
    elif not ENABLE_ODDS_PARTIAL_MARKET_PULL:
        print("ENABLE_ODDS_PARTIAL_MARKET_PULL=false. Skipping partial-game and alternate-market pull.")
    else:
        print("ODDS_API_KEY_3 not set. Skipping partial-game and alternate-market pull.")

    save_master_cache(cache)
    detail = (
        f"fetch complete | primary sports: {primary_success}/{len(PRIMARY_CONFIG)}"
        f" | secondary sports: {secondary_success}/{len(SECONDARY_CONFIG) if ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL else 0}"
        f" | tertiary sports: {tertiary_success}/{len(TERTIARY_CONFIG) if ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL else 0}"
        f" | partial/alternate sports: {partial_success}/{len(PARTIAL_GAME_CONFIG) if ODDS_API_KEY_3 and ENABLE_ODDS_PARTIAL_MARKET_PULL else 0}"
    )
    return {"detail": detail, "count": len(cache), "label": "updates"}


if __name__ == "__main__":
    run_fetcher()
