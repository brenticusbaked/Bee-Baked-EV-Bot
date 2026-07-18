import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from db_manager import get_market_cache, load_tracker_state, save_tracker_state
from services.alerts import send_discord_alert
from services.discord_channels import ARBITRAGE_WEBHOOK_URL
from utils.odds import decimal_implied_probability, decimal_to_american
from utils.thresholds import env_float, env_int
from utils.time import get_local_date_str


Cache = Dict[str, List[dict]]
STATE_KEY = "arbitrage_alerts"
STATE_FILE = "arbitrage_alert_state.json"
ARBITRAGE_MIN_PROFIT = env_float("ARBITRAGE_MIN_PROFIT", 0.005)
ARBITRAGE_MAX_ALERTS = env_int("ARBITRAGE_MAX_ALERTS", 10)

# Arbitrage is only actionable between books we can actually bet at. Pinnacle is
# a fair-value benchmark only (no account access), so it is excluded here — both
# legs of every flagged arb must come from this American-book allowlist.
ARBITRAGE_US_BOOKS = {
    book.strip().lower()
    for book in os.getenv(
        "ARBITRAGE_US_BOOKS",
        "fanduel,draftkings,betmgm,caesars,bet365,bovada,novig,kalshi,"
        "polymarket,prophetx,espnbet,fanatics,betrivers,hardrockbet,fliff",
    ).split(",")
    if book.strip()
}


def _market_family(market_key: str) -> str:
    key = str(market_key).lower()
    if "spread" in key:
        return "spread"
    if "total" in key:
        return "total"
    if key.startswith("h2h"):
        return "h2h"
    return key


def _point_bucket(market_key: str, outcome: dict) -> str:
    point = outcome.get("point")
    if point in (None, ""):
        return ""
    try:
        numeric = float(point)
    except (TypeError, ValueError):
        return str(point)
    if _market_family(market_key) == "spread":
        return str(abs(numeric))
    return str(numeric)


def _outcome_key(market_key: str, outcome: dict) -> Tuple[str, str]:
    return (str(outcome.get("name", "")).strip().lower(), _point_bucket(market_key, outcome))


def _display_outcome(market_key: str, outcome: dict) -> str:
    name = str(outcome.get("name", "")).strip()
    point = outcome.get("point")
    if point in (None, ""):
        return name
    if _market_family(market_key) == "total":
        return f"{name} {point}".strip()
    return f"{name} {point}".strip()


def _group_key(event: dict, market_key: str, outcome: dict) -> Tuple[str, str, str]:
    event_id = str(event.get("id") or event.get("event_id") or event.get("game_id") or "")
    return (event_id, str(market_key), _point_bucket(market_key, outcome))


def find_arbitrage_opportunities(cache: Cache, min_profit: float = ARBITRAGE_MIN_PROFIT) -> List[dict]:
    groups: Dict[Tuple[str, str, str], dict] = {}

    for sport, events in (cache or {}).items():
        for event in events or []:
            matchup = f"{event.get('away_team', 'Away')} @ {event.get('home_team', 'Home')}"
            for book in event.get("bookmakers", []):
                book_key = str(book.get("key") or book.get("title") or "").lower()
                book_title = str(book.get("title") or book.get("key") or "unknown")
                if book_key not in ARBITRAGE_US_BOOKS:
                    continue
                for market in book.get("markets", []):
                    market_key = str(market.get("key", ""))
                    family = _market_family(market_key)
                    if family not in {"h2h", "spread", "total"}:
                        continue
                    for outcome in market.get("outcomes", []):
                        try:
                            price = float(outcome["price"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if price <= 1.0:
                            continue
                        key = _group_key(event, market_key, outcome)
                        outcome_key = _outcome_key(market_key, outcome)
                        group = groups.setdefault(
                            key,
                            {
                                "sport": sport,
                                "event_id": str(event.get("id") or ""),
                                "matchup": matchup,
                                "market": market_key,
                                "outcomes": {},
                            },
                        )
                        current = group["outcomes"].get(outcome_key)
                        if not current or price > current["price"]:
                            group["outcomes"][outcome_key] = {
                                "name": _display_outcome(market_key, outcome),
                                "book": book_title,
                                "book_key": book_key,
                                "price": price,
                            }

    opportunities = []
    for group in groups.values():
        outcomes = list(group["outcomes"].values())
        if len(outcomes) < 2:
            continue
        # Require the arb to span at least two distinct American books; a single
        # book pricing both sides is not a bettable two-book arbitrage.
        if len({item["book_key"] for item in outcomes}) < 2:
            continue
        implied_sum = sum(decimal_implied_probability(item["price"]) for item in outcomes)
        if implied_sum <= 0:
            continue
        profit = (1.0 / implied_sum) - 1.0
        if profit < min_profit:
            continue
        for item in outcomes:
            item["stake_pct"] = decimal_implied_probability(item["price"]) / implied_sum
        opportunities.append({**group, "profit": profit, "implied_sum": implied_sum, "outcomes": outcomes})

    return sorted(opportunities, key=lambda item: item["profit"], reverse=True)


def _dedupe_key(opportunity: dict) -> str:
    legs = [
        f"{item['name']}:{item['book_key']}:{item['price']:.4f}"
        for item in sorted(opportunity["outcomes"], key=lambda leg: leg["name"])
    ]
    return "|".join([opportunity["sport"], opportunity["event_id"], opportunity["market"], *legs])


def _load_sent_keys() -> set[str]:
    state = load_tracker_state(STATE_KEY, STATE_FILE)
    if not isinstance(state, dict) or state.get("date") != get_local_date_str():
        return set()
    return set(state.get("sent_keys", []))


def _save_sent_keys(keys: Iterable[str]) -> None:
    save_tracker_state(
        STATE_KEY,
        {"date": get_local_date_str(), "sent_keys": sorted(set(keys))[-500:]},
        STATE_FILE,
    )


def _build_payload(opportunity: dict) -> dict:
    leg_lines = []
    for leg in sorted(opportunity["outcomes"], key=lambda item: item["stake_pct"], reverse=True):
        leg_lines.append(
            f"`{leg['stake_pct'] * 100:5.1f}%` {leg['name']} - {leg['book']} {decimal_to_american(leg['price'])}"
        )
    return {
        "embeds": [
            {
                "description": (
                    "**ARBITRAGE ALERT**\n\n"
                    f"**Match:** {opportunity['matchup']}\n"
                    f"**Sport:** {opportunity['sport']}\n"
                    f"**Market:** {opportunity['market'].upper()}\n"
                    f"**Locked Profit:** {opportunity['profit'] * 100:.2f}%\n"
                    f"**Implied Sum:** {opportunity['implied_sum'] * 100:.2f}%\n\n"
                    "**Stake Split**\n"
                    + "\n".join(leg_lines)
                ),
                "color": 15844367,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }


def send_arbitrage_alerts(opportunities: List[dict], sent_keys: Optional[set[str]] = None) -> int:
    if sent_keys is None:
        sent_keys = _load_sent_keys()
    sent = 0
    for opportunity in opportunities[:ARBITRAGE_MAX_ALERTS]:
        key = _dedupe_key(opportunity)
        if key in sent_keys:
            continue
        if send_discord_alert(
            _build_payload(opportunity),
            source="arbitrage_scanner",
            alert_type="arbitrage_alert",
            dedupe_key=key,
            webhook_url=ARBITRAGE_WEBHOOK_URL,
        ):
            sent += 1
            sent_keys.add(key)
    _save_sent_keys(sent_keys)
    return sent


def run_arbitrage_scan() -> dict:
    cache = get_market_cache() or {}
    if not cache:
        return {"detail": "cache empty", "count": 0, "label": "alerts"}
    opportunities = find_arbitrage_opportunities(cache)
    sent = send_arbitrage_alerts(opportunities)
    return {
        "detail": f"arbitrage scan complete | opportunities={len(opportunities)}",
        "count": sent,
        "label": "alerts",
        "meta": {"opportunities": str(len(opportunities))},
    }


if __name__ == "__main__":
    result = run_arbitrage_scan()
    print(result)
