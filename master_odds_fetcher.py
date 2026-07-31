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

SHARP_REGION = os.getenv("ODDS_API_SHARP_REGION", "eu")
SOFT_REGION = os.getenv("ODDS_API_SOFT_REGION", "us")
SHARP_BOOKS = os.getenv("ODDS_API_SHARP_BOOKS", "pinnacle")
DEFAULT_SOFT_BOOKS = "fanduel,draftkings,betmgm,bet365,caesars,bovada"
SOFT_BOOKS = os.getenv("ODDS_API_SOFT_BOOKS", DEFAULT_SOFT_BOOKS)
ENABLE_ODDS_SECONDARY_PULL = env_flag("ENABLE_ODDS_SECONDARY_PULL", True)
ENABLE_ODDS_TERTIARY_PULL = env_flag("ENABLE_ODDS_TERTIARY_PULL", True)
ENABLE_ODDS_PARTIAL_MARKET_PULL = env_flag("ENABLE_ODDS_PARTIAL_MARKET_PULL", False)
ENABLE_MLB_F5_PULL = env_flag("ENABLE_MLB_F5_PULL", True)

PARTIAL_GAME_CONFIG = {
    "basketball_nba": "alternate_spreads,alternate_totals,spreads_q1,totals_q1,h2h_q1,spreads_h1,totals_h1,h2h_h1",
    "icehockey_nhl": "alternate_spreads,alternate_totals,spreads_1st_period,totals_1st_period,h2h_1st_period",
    "baseball_mlb": "alternate_spreads,alternate_totals",
    "americanfootball_nfl": "alternate_spreads,alternate_totals,spreads_q1,totals_q1,h2h_q1,spreads_h1,totals_h1,h2h_h1",
}

# Dedicated MLB first-five pull so the model can keep using F5 markets without
# reintroducing them into the shared main ingest defaults.
MLB_F5_CONFIG = {
    "baseball_mlb": "h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings",
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


def _fetch_config(
    cache: Dict[str, List[dict]],
    api_key: str,
    config: Dict[str, str],
    label: str,
    region: str,
    bookmakers: str,
) -> int:
    success_count = 0
    for sport, markets in config.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            "apiKey": api_key,
            "regions": region,
            "markets": markets,
            "bookmakers": bookmakers,
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

    primary_sharp = 0
    primary_soft = 0
    secondary_sharp = 0
    secondary_soft = 0
    tertiary_sharp = 0
    tertiary_soft = 0
    partial_sharp = 0
    partial_soft = 0

    active_primary = filter_config_in_season(PRIMARY_CONFIG)
    skipped_primary = set(PRIMARY_CONFIG) - set(active_primary)
    if skipped_primary:
        print(f"BEE-BAKED FETCH: Skipping off-season sports (primary): {', '.join(sorted(skipped_primary))}")

    print(
        "BEE-BAKED FETCH: Running primary precision pull"
        f" ({_credits_for_config(active_primary) * 2} credits/run)"
    )
    primary_sharp = _fetch_config(cache, ODDS_API_KEY, active_primary, "primary sharp eu", SHARP_REGION, SHARP_BOOKS)
    primary_soft = _fetch_config(cache, ODDS_API_KEY, active_primary, "primary soft us", SOFT_REGION, SOFT_BOOKS)

    active_secondary = filter_config_in_season(SECONDARY_CONFIG)
    secondary_sharp = 0
    secondary_soft = 0
    if ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL:
        skipped_secondary = set(SECONDARY_CONFIG) - set(active_secondary)
        if skipped_secondary:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (secondary): {', '.join(sorted(skipped_secondary))}")
        print(
            "BEE-BAKED FETCH: Running secondary expansion pull"
            f" ({_credits_for_config(active_secondary) * 2} credits/run)"
        )
        secondary_sharp = _fetch_config(cache, ODDS_API_KEY_2, active_secondary, "secondary sharp eu", SHARP_REGION, SHARP_BOOKS)
        secondary_soft = _fetch_config(cache, ODDS_API_KEY_2, active_secondary, "secondary soft us", SOFT_REGION, SOFT_BOOKS)
    elif not ENABLE_ODDS_SECONDARY_PULL:
        print("ENABLE_ODDS_SECONDARY_PULL=false. Skipping secondary expansion pull.")
    else:
        print("ODDS_API_KEY_2 not set. Skipping secondary expansion pull.")

    active_tertiary = filter_config_in_season(TERTIARY_CONFIG)
    tertiary_sharp = 0
    tertiary_soft = 0
    if ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL:
        skipped_tertiary = set(TERTIARY_CONFIG) - set(active_tertiary)
        if skipped_tertiary:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (tertiary): {', '.join(sorted(skipped_tertiary))}")
        print(
            "BEE-BAKED FETCH: Running tertiary expansion pull"
            f" ({_credits_for_config(active_tertiary) * 2} credits/run)"
        )
        tertiary_sharp = _fetch_config(cache, ODDS_API_KEY_3, active_tertiary, "tertiary sharp eu", SHARP_REGION, SHARP_BOOKS)
        tertiary_soft = _fetch_config(cache, ODDS_API_KEY_3, active_tertiary, "tertiary soft us", SOFT_REGION, SOFT_BOOKS)
    elif not ENABLE_ODDS_TERTIARY_PULL:
        print("ENABLE_ODDS_TERTIARY_PULL=false. Skipping tertiary expansion pull.")
    else:
        print("ODDS_API_KEY_3 not set. Skipping tertiary expansion pull.")

    active_partial = filter_config_in_season(PARTIAL_GAME_CONFIG)
    partial_sharp = 0
    partial_soft = 0
    if ODDS_API_KEY_3 and ENABLE_ODDS_PARTIAL_MARKET_PULL:
        skipped_partial = set(PARTIAL_GAME_CONFIG) - set(active_partial)
        if skipped_partial:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (partial/alternate): {', '.join(sorted(skipped_partial))}")
        print(
            "BEE-BAKED FETCH: Running partial-game and alternate-market pull"
            f" ({_credits_for_config(active_partial) * 2} credits/run)"
        )
        partial_sharp = _fetch_config(cache, ODDS_API_KEY_3, active_partial, "partial/alternate sharp eu", SHARP_REGION, SHARP_BOOKS)
        partial_soft = _fetch_config(cache, ODDS_API_KEY_3, active_partial, "partial/alternate soft us", SOFT_REGION, SOFT_BOOKS)
    elif not ENABLE_ODDS_PARTIAL_MARKET_PULL:
        print("ENABLE_ODDS_PARTIAL_MARKET_PULL=false. Skipping partial-game and alternate-market pull.")
    else:
        print("ODDS_API_KEY_3 not set. Skipping partial-game and alternate-market pull.")

    active_mlb_f5 = filter_config_in_season(MLB_F5_CONFIG)
    mlb_f5_sharp = 0
    mlb_f5_soft = 0
    if ODDS_API_KEY_4 and ENABLE_MLB_F5_PULL:
        skipped_mlb_f5 = set(MLB_F5_CONFIG) - set(active_mlb_f5)
        if skipped_mlb_f5:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (mlb_f5): {', '.join(sorted(skipped_mlb_f5))}")
        print(
            "BEE-BAKED FETCH: Running dedicated MLB first-five pull"
            f" ({_credits_for_config(active_mlb_f5) * 2} credits/run)"
        )
        mlb_f5_sharp = _fetch_config(cache, ODDS_API_KEY_4, active_mlb_f5, "mlb f5 sharp eu", SHARP_REGION, SHARP_BOOKS)
        mlb_f5_soft = _fetch_config(cache, ODDS_API_KEY_4, active_mlb_f5, "mlb f5 soft us", SOFT_REGION, SOFT_BOOKS)
    elif not ENABLE_MLB_F5_PULL:
        print("ENABLE_MLB_F5_PULL=false. Skipping dedicated MLB first-five pull.")
    else:
        print("ODDS_API_KEY_4 not set. Skipping dedicated MLB first-five pull.")

    save_master_cache(cache)

    primary_denom = len(active_primary)
    secondary_denom = len(active_secondary) if (ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL) else 0
    tertiary_denom = len(active_tertiary) if (ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL) else 0
    partial_denom = len(active_partial) if (ODDS_API_KEY_3 and ENABLE_ODDS_PARTIAL_MARKET_PULL) else 0
    mlb_f5_denom = len(active_mlb_f5) if (ODDS_API_KEY_4 and ENABLE_MLB_F5_PULL) else 0

    detail = (
        f"fetch complete"
        f" | primary sharp: {primary_sharp}/{primary_denom} soft: {primary_soft}/{primary_denom}"
        f" | secondary sharp: {secondary_sharp}/{secondary_denom} soft: {secondary_soft}/{secondary_denom}"
        f" | tertiary sharp: {tertiary_sharp}/{tertiary_denom} soft: {tertiary_soft}/{tertiary_denom}"
        f" | partial/alternate sharp: {partial_sharp}/{partial_denom} soft: {partial_soft}/{partial_denom}"
        f" | mlb_f5 sharp: {mlb_f5_sharp}/{mlb_f5_denom} soft: {mlb_f5_soft}/{mlb_f5_denom}"
    )
    return {"detail": detail, "count": len(cache), "label": "updates"}


if __name__ == "__main__":
    run_fetcher()
