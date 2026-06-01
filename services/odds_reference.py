from typing import Dict, Iterable, Optional, Tuple

from services.bet_logic import outcome_matches, parse_selection
from utils.odds import decimal_to_american


def candidate_market_keys(market_key: str) -> list[str]:
    key = str(market_key).lower()
    candidates = [key]
    aliases = {
        "moneyline": "h2h",
        "ml": "h2h",
        "spread": "spreads",
        "runline": "spreads",
        "puckline": "spreads",
        "total": "totals",
        "totals": "totals",
        "over/under": "totals",
        "o/u": "totals",
        "model_nba_spread": "spreads",
        "model_nhl_puckline": "spreads",
    }
    if key in aliases:
        candidates.append(aliases[key])
    if key == "model_mlb_f5":
        candidates.extend(["h2h_1st_5_innings", "h2h_1st_half", "h2h"])
    return candidates


def _matching_event(cache: Dict[str, list], sport: str, event_id) -> Optional[dict]:
    for event in cache.get(sport, []) or []:
        if str(event.get("id")) == str(event_id):
            return event
    return None


def find_book_reference(
    cache: Dict[str, list],
    sport: str,
    event_id,
    market_key: str,
    selection: str,
    book_keys: Iterable[str] = ("pinnacle",),
) -> Optional[Tuple[str, float]]:
    event = _matching_event(cache, sport, event_id)
    if not event:
        return None

    wanted_books = {str(item).lower() for item in book_keys}
    wanted_markets = set(candidate_market_keys(market_key))
    selection_spec = parse_selection(market_key, selection)

    for book in event.get("bookmakers", []):
        book_key = str(book.get("key") or book.get("title") or "").lower()
        if book_key not in wanted_books:
            continue
        for market in book.get("markets", []):
            if str(market.get("key", "")).lower() not in wanted_markets:
                continue
            for outcome in market.get("outcomes", []):
                if outcome_matches(selection_spec, outcome):
                    return str(book.get("title") or book.get("key") or "Pinnacle"), float(outcome["price"])
    return None


def format_pinnacle_reference(cache: Dict[str, list], sport: str, event_id, market_key: str, selection: str) -> str:
    reference = find_book_reference(cache, sport, event_id, market_key, selection, ("pinnacle",))
    if not reference:
        return "Pinnacle unavailable"
    book_title, price = reference
    return f"{book_title} {decimal_to_american(price)}"
