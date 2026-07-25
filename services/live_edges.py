import os
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from services.alerts import send_discord_alert
from services.discord_channels import LIVE_HAMMER_WEBHOOK_URL, WATCHLIST_WEBHOOK_URL
from utils.odds import decimal_to_american, fair_probabilities_from_prices
from utils.stale_line import StalenessSignal, detect_stale_line
from utils.thresholds import env_float


Cache = Dict[str, List[dict]]
OutcomeKey = Tuple[str, str]
PriceIndex = Dict[Tuple[str, str, OutcomeKey], float]


LIVE_HAMMER_EDGE_THRESHOLD = env_float("LIVE_HAMMER_EDGE_THRESHOLD", 0.03)
WATCHLIST_EDGE_THRESHOLD = env_float("WATCHLIST_EDGE_THRESHOLD", 0.01)
LIVE_STALE_MIN_SCORE = env_float("LIVE_STALE_MIN_SCORE", 0.50)
LIVE_DEVIG_METHOD = os.getenv("LIVE_DEVIG_METHOD") or os.getenv("DEVIG_METHOD", "power")
LIVE_SHARP_BOOKS = {
    item.strip().lower()
    for item in os.getenv("LIVE_SHARP_BOOKS", "pinnacle,bookmaker,circa,cris").split(",")
    if item.strip()
}
LIVE_SOFT_BOOKS = {
    item.strip().lower()
    for item in os.getenv("LIVE_SOFT_BOOKS", "fanduel,draftkings,betmgm,bet365,caesars,bovada,prizepicks").split(",")
    if item.strip()
}


def _outcome_key(outcome: dict) -> OutcomeKey:
    return (str(outcome.get("name", "")).lower().strip(), str(outcome.get("point", "")).strip())


def _selection(outcome: dict) -> str:
    return f"{outcome.get('name', '')} {outcome.get('point', '')}".strip()


def _event_index(cache: Cache) -> Dict[Tuple[str, str], dict]:
    indexed = {}
    for sport, events in (cache or {}).items():
        for event in events or []:
            event_id = str(event.get("id") or event.get("event_id") or event.get("game_id") or "")
            if event_id:
                indexed[(str(sport), event_id)] = event
    return indexed


def _price_index(event: Optional[dict]) -> PriceIndex:
    prices: PriceIndex = {}
    if not event:
        return prices
    for book in event.get("bookmakers", []):
        book_key = str(book.get("key") or book.get("title") or "").lower().strip()
        for market in book.get("markets", []):
            market_key = str(market.get("key", "")).strip()
            for outcome in market.get("outcomes", []):
                try:
                    prices[(book_key, market_key, _outcome_key(outcome))] = float(outcome["price"])
                except (KeyError, TypeError, ValueError):
                    continue
    return prices


def _market_quotes(event: dict) -> Dict[str, dict]:
    markets: Dict[str, dict] = {}
    for book in event.get("bookmakers", []):
        book_key = str(book.get("key") or book.get("title") or "").lower().strip()
        book_title = book.get("title") or book_key
        for market in book.get("markets", []):
            market_key = str(market.get("key", "")).strip()
            bucket = markets.setdefault(market_key, {"sharp": {}, "soft": []})
            for outcome in market.get("outcomes", []):
                try:
                    price = float(outcome["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                item = {
                    "book_key": book_key,
                    "book": book_title,
                    "market": market_key,
                    "outcome": outcome,
                    "outcome_key": _outcome_key(outcome),
                    "price": price,
                }
                if book_key in LIVE_SHARP_BOOKS:
                    bucket["sharp"][item["outcome_key"]] = price
                elif not LIVE_SOFT_BOOKS or book_key in LIVE_SOFT_BOOKS:
                    bucket["soft"].append(item)
    return markets


def _best_previous_sharp(old_prices: PriceIndex, market: str, outcome_key: OutcomeKey) -> Optional[float]:
    for book_key in LIVE_SHARP_BOOKS:
        price = old_prices.get((book_key, market, outcome_key))
        if price is not None:
            return price
    return None


def _build_alert(
    lane: str,
    sport: str,
    event: dict,
    quote: dict,
    edge: float,
    fair_price: float,
    sharp_price: Optional[float],
    staleness: Optional[StalenessSignal],
) -> dict:
    matchup = f"{event.get('away_team', 'Away')} @ {event.get('home_team', 'Home')}"
    stale_text = ""
    if staleness:
        stale_text = (
            f"\n**Stale Score:** {staleness.staleness_score:.0%}"
            f"\n**Sharp Move:** {staleness.sharp_implied_move * 100:.2f}pp"
        )
    title = "LIVE HAMMER" if lane == "hammer" else "WATCHLIST"
    return {
        "lane": lane,
        "sport": sport,
        "market": str(quote["market"]),
        "book": str(quote["book"]),
        "price": float(quote["price"]),
        "edge": edge,
        "stale_score": staleness.staleness_score if staleness else None,
        "sharp_move": staleness.sharp_implied_move if staleness else None,
        "dedupe_key": ":".join(
            [
                lane,
                str(sport),
                str(event.get("id", "")),
                str(quote["market"]),
                _selection(quote["outcome"]).lower(),
                str(quote["book_key"]),
                str(quote["price"]),
            ]
        ),
        "payload": {
            "embeds": [
                {
                    "description": (
                        f"**{title}**\n\n"
                        f"**Match:** {matchup}\n"
                        f"**Market:** {quote['market'].upper()}\n"
                        f"**Bet:** {_selection(quote['outcome'])}\n"
                        f"**Book:** {quote['book']} @ {decimal_to_american(quote['price'])}\n"
                        f"**Pinnacle:** {decimal_to_american(sharp_price) if sharp_price else 'unavailable'}\n"
                        f"**Fair:** {decimal_to_american(fair_price)}\n"
                        f"**Edge:** {edge * 100:.2f}%"
                        f"{stale_text}"
                    ),
                    "color": 15158332 if lane == "hammer" else 16776960,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ]
        },
    }


def summarize_live_edge_alerts(alerts: Iterable[dict], limit: int = 3) -> str:
    alerts = list(alerts or [])
    if not alerts:
        return "No live-edge candidates crossed the watch threshold."

    lane_counts = Counter(str(alert.get("lane", "watchlist")) for alert in alerts)
    sport_counts = Counter(str(alert.get("sport", "unknown")) for alert in alerts)
    market_counts = Counter(str(alert.get("market", "unknown")).upper() for alert in alerts)
    top_alerts = sorted(alerts, key=lambda item: (-float(item.get("edge", 0.0)), item.get("lane") != "hammer"))[:limit]

    top_lines = []
    for alert in top_alerts:
        stale_score = alert.get("stale_score")
        sharp_move = alert.get("sharp_move")
        stale_bits = []
        if isinstance(stale_score, (int, float)):
            stale_bits.append(f"stale={stale_score:.0%}")
        if isinstance(sharp_move, (int, float)):
            stale_bits.append(f"move={sharp_move * 100:.2f}pp")
        stale_text = f" ({', '.join(stale_bits)})" if stale_bits else ""
        top_lines.append(
            f"{str(alert.get('lane', 'watchlist')).upper()} {alert.get('sport', 'unknown')} "
            f"{alert.get('market', 'unknown').upper()} {alert.get('book', 'unknown')} "
            f"@ {alert.get('edge', 0.0) * 100:.2f}%{stale_text}"
        )

    return (
        f"alerts={len(alerts)} | "
        f"hammer={lane_counts.get('hammer', 0)} | "
        f"watchlist={lane_counts.get('watchlist', 0)} | "
        f"sports={', '.join(f'{sport}:{count}' for sport, count in sport_counts.most_common(4)) or 'none'} | "
        f"markets={', '.join(f'{market}:{count}' for market, count in market_counts.most_common(4)) or 'none'} | "
        f"top={'; '.join(top_lines)}"
    )


def find_live_edge_alerts(previous_cache: Cache, current_cache: Cache, updated_cache: Cache) -> List[dict]:
    previous_events = _event_index(previous_cache)
    current_events = _event_index(current_cache)
    alerts = []

    for sport, updated_events in (updated_cache or {}).items():
        for updated_event in updated_events:
            event_id = str(updated_event.get("id") or updated_event.get("event_id") or updated_event.get("game_id") or "")
            current_event = current_events.get((str(sport), event_id))
            if not current_event:
                continue

            old_prices = _price_index(previous_events.get((str(sport), event_id)))
            for market_key, quotes in _market_quotes(current_event).items():
                fair_probs = fair_probabilities_from_prices(quotes["sharp"], method=LIVE_DEVIG_METHOD)
                if not fair_probs:
                    continue
                for soft_quote in quotes["soft"]:
                    fair_probability = fair_probs.get(soft_quote["outcome_key"])
                    if not fair_probability:
                        continue
                    edge = (soft_quote["price"] * fair_probability) - 1.0
                    if edge < WATCHLIST_EDGE_THRESHOLD:
                        continue

                    previous_sharp = _best_previous_sharp(old_prices, market_key, soft_quote["outcome_key"])
                    previous_soft = old_prices.get((soft_quote["book_key"], market_key, soft_quote["outcome_key"]))
                    staleness = None
                    if previous_sharp:
                        staleness = detect_stale_line(
                            sharp_price=quotes["sharp"][soft_quote["outcome_key"]],
                            sharp_opening_price=previous_sharp,
                            soft_price=soft_quote["price"],
                            soft_opening_price=previous_soft,
                        )

                    is_stale_hammer = bool(staleness and staleness.is_stale and staleness.staleness_score >= LIVE_STALE_MIN_SCORE)
                    lane = "hammer" if edge >= LIVE_HAMMER_EDGE_THRESHOLD and is_stale_hammer else "watchlist"
                    alerts.append(
                        _build_alert(
                            lane=lane,
                            sport=str(sport),
                            event=current_event,
                            quote=soft_quote,
                            edge=edge,
                            fair_price=1.0 / fair_probability,
                            sharp_price=quotes["sharp"].get(soft_quote["outcome_key"]),
                            staleness=staleness,
                        )
                    )
    return sorted(alerts, key=lambda item: (item["lane"] != "hammer", -item["edge"]))


def send_live_edge_alerts(alerts: Iterable[dict], sent_dedupe_keys: Optional[set[str]] = None) -> int:
    sent = 0
    for alert in alerts:
        dedupe_key = alert["dedupe_key"]
        if sent_dedupe_keys is not None and dedupe_key in sent_dedupe_keys:
            continue
        webhook = LIVE_HAMMER_WEBHOOK_URL if alert["lane"] == "hammer" else WATCHLIST_WEBHOOK_URL
        if send_discord_alert(
            alert["payload"],
            source="odds_push",
            alert_type=f"live_{alert['lane']}",
            dedupe_key=dedupe_key,
            webhook_url=webhook,
            add_bee_image=False,
        ):
            sent += 1
            if sent_dedupe_keys is not None:
                sent_dedupe_keys.add(dedupe_key)
    return sent
