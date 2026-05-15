import os
from datetime import datetime, timezone
from typing import Dict, List

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.book_weights import get_book_weights
from utils.links import sportsbook_search_link
from utils.odds import decimal_implied_probability, decimal_to_american, fair_probabilities_from_prices, quarter_kelly_units
from utils.thresholds import env_float, env_int


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
UNIFIED_EV_THRESHOLD = env_float("UNIFIED_EV_THRESHOLD", 0.02)
UNIFIED_NEAR_MISS_THRESHOLD = env_float("UNIFIED_NEAR_MISS_THRESHOLD", 0.01)
UNIFIED_SPREAD_EV_THRESHOLD = env_float("UNIFIED_SPREAD_EV_THRESHOLD", UNIFIED_EV_THRESHOLD)
UNIFIED_H2H_EV_THRESHOLD = env_float("UNIFIED_H2H_EV_THRESHOLD", max(UNIFIED_EV_THRESHOLD, 0.03))
UNIFIED_TOTAL_EV_THRESHOLD = env_float("UNIFIED_TOTAL_EV_THRESHOLD", max(UNIFIED_EV_THRESHOLD, 0.0225))
ENABLE_MLB_H2H_ALERTS = os.getenv("ENABLE_MLB_H2H_ALERTS", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NBA_TOTAL_ALERTS = os.getenv("ENABLE_NBA_TOTAL_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NHL_TOTAL_ALERTS = os.getenv("ENABLE_NHL_TOTAL_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_MLB_SPREAD_ALERTS = os.getenv("ENABLE_MLB_SPREAD_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_MLB_TOTAL_ALERTS = os.getenv("ENABLE_MLB_TOTAL_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
UNIFIED_MAX_ALERTS_PER_EVENT_MARKET = max(1, env_int("UNIFIED_MAX_ALERTS_PER_EVENT_MARKET", 3))


def get_mobile_app_link(book_key, selection_id, event_id, matchup):
    del selection_id, event_id
    return sportsbook_search_link(book_key, matchup)


def calculate_edge(offered_price: float, sharp_price: float) -> float:
    fair_probability = decimal_implied_probability(sharp_price)
    return (offered_price * fair_probability) - 1.0


def calculate_edge_from_probability(offered_price: float, fair_probability: float) -> float:
    return (float(offered_price) * float(fair_probability)) - 1.0


def _market_allowed_for_sport(sport: str, market_type: str) -> bool:
    sport_key = str(sport).strip().lower()
    market_key = str(market_type).strip().lower()

    if sport_key == "basketball_nba":
        return market_key == "spreads" or (market_key == "totals" and ENABLE_NBA_TOTAL_ALERTS)
    if sport_key == "icehockey_nhl":
        return market_key == "spreads" or (market_key == "totals" and ENABLE_NHL_TOTAL_ALERTS)
    if sport_key == "baseball_mlb":
        return (
            (market_key == "h2h" and ENABLE_MLB_H2H_ALERTS)
            or (market_key == "spreads" and ENABLE_MLB_SPREAD_ALERTS)
            or (market_key == "totals" and ENABLE_MLB_TOTAL_ALERTS)
        )
    return market_key in {"spreads", "totals", "h2h"}


def _market_ev_threshold(market_type: str) -> float:
    market_key = str(market_type).strip().lower()
    if market_key == "spreads":
        return UNIFIED_SPREAD_EV_THRESHOLD
    if market_key == "h2h":
        return UNIFIED_H2H_EV_THRESHOLD
    if market_key == "totals":
        return UNIFIED_TOTAL_EV_THRESHOLD
    return UNIFIED_EV_THRESHOLD


def scan_markets():
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return {"detail": "cache empty", "count": 0, "label": "alerts"}

    alerts = []
    near_misses = []
    book_weights = get_book_weights()
    soft_books = ["fanduel", "draftkings", "betmgm", "bet365", "caesars", "bovada"]
    now = datetime.now(timezone.utc)

    for sport, events in cache.items():
        for event in events:
            commence_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
            if now > commence_time:
                continue

            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    market_key = market["key"]
                    markets.setdefault(market_key, {"sharp": {}, "soft": []})

                    if bookmaker["key"] == "pinnacle":
                        for outcome in market.get("outcomes", []):
                            outcome_key = (
                                str(outcome["name"]).lower().strip(),
                                str(outcome.get("point", "")),
                            )
                            markets[market_key]["sharp"][outcome_key] = float(outcome["price"])
                    elif bookmaker["key"] in soft_books:
                        for outcome in market.get("outcomes", []):
                            markets[market_key]["soft"].append(
                                {
                                    "book": bookmaker["title"],
                                    "book_key": bookmaker["key"],
                                    "name": outcome["name"],
                                    "price": float(outcome["price"]),
                                    "point": outcome.get("point", ""),
                                    "id": outcome.get("id"),
                                }
                            )

            for market_type, data in markets.items():
                if not _market_allowed_for_sport(sport, market_type):
                    continue

                market_threshold = _market_ev_threshold(market_type)

                sharp = data["sharp"]
                if not sharp:
                    continue

                fair_probabilities = fair_probabilities_from_prices(sharp)
                candidates = []
                for soft_bet in data["soft"]:
                    outcome_key = (
                        str(soft_bet["name"]).lower().strip(),
                        str(soft_bet.get("point", "")),
                    )
                    fair_probability = fair_probabilities.get(outcome_key)
                    if not fair_probability:
                        continue

                    fair_decimal = 1.0 / fair_probability
                    edge = calculate_edge_from_probability(soft_bet["price"], fair_probability)
                    book_weight = book_weights.get(soft_bet["book"], 1.0)
                    weighted_score = edge * book_weight

                    if UNIFIED_NEAR_MISS_THRESHOLD <= edge < market_threshold:
                        near_misses.append(
                            {
                                "matchup": matchup,
                                "selection": f"{soft_bet['name']} {soft_bet['point']}".strip(),
                                "book": soft_bet["book"],
                                "edge": edge,
                                "weight": book_weight,
                                "market": market_type,
                            }
                        )

                    if edge >= market_threshold:
                        candidates.append(
                            {
                                "edge": edge,
                                "score": weighted_score,
                                "bet": soft_bet,
                                "fair_decimal": fair_decimal,
                                "fair_probability": fair_probability,
                                "book_weight": book_weight,
                                "outcome_key": outcome_key,
                            }
                        )

                if not candidates:
                    continue

                selected_candidates = []
                seen_outcomes = set()
                for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
                    if candidate["outcome_key"] in seen_outcomes:
                        continue
                    seen_outcomes.add(candidate["outcome_key"])
                    selected_candidates.append(candidate)
                    if len(selected_candidates) >= UNIFIED_MAX_ALERTS_PER_EVENT_MARKET:
                        break

                for candidate in selected_candidates:
                    final = candidate["bet"]
                    selection = f"{final['name']} {final['point']}".strip()
                    if is_already_logged(matchup, market_type, selection):
                        continue

                    edge = candidate["edge"]
                    offered_price = final["price"]
                    fair_decimal = candidate["fair_decimal"]
                    fair_probability = candidate["fair_probability"]
                    book_weight = candidate["book_weight"]
                    units = quarter_kelly_units(edge, offered_price)
                    fair_price_american = decimal_to_american(fair_decimal)

                    was_logged = log_bet_to_db(
                        matchup,
                        market_type,
                        selection,
                        decimal_to_american(offered_price),
                        edge,
                        f"{units:.2f}",
                        fair_price_american,
                        sport,
                        event["id"],
                        notes=(
                            f"book={final['book']};book_key={final['book_key']};"
                            f"book_weight={book_weight:.4f};fair_probability={fair_probability:.4f};"
                            f"fair_decimal={fair_decimal:.4f}"
                        ),
                    )
                    if not was_logged:
                        print(f"Skipping alert because DB log failed for {selection}.")
                        continue

                    app_link = get_mobile_app_link(final["book_key"], final["id"], event["id"], matchup)
                    alerts.append(
                        {
                            "description": (
                                f"**+EV {market_type.upper()} ALERT**\n\n"
                                f"**Match:** {matchup}\n"
                                f"**Bet:** {selection}\n"
                                f"**Book:** [{final['book']}]({app_link}) @ {decimal_to_american(offered_price)}\n"
                                f"**Fair Value:** {fair_price_american}\n"
                                f"**Edge:** {edge * 100:.2f}%\n"
                                f"**Book Weight:** {book_weight:.2f}x\n"
                                f"**Suggested:** {units:.2f} Units"
                            )
                        }
                    )

    for index, alert in enumerate(alerts):
        send_discord_alert(
            {
                "embeds": [
                    {
                        "description": alert["description"],
                        "color": 3066993,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ]
            },
            source="unified_bot",
            alert_type="bet_alert",
            dedupe_key=alert["description"][:200],
            webhook_url=DISCORD_WEBHOOK_URL,
            add_bee_image=index == len(alerts) - 1,
        )

    near_miss_text = ""
    if near_misses:
        total_near_misses = len(near_misses)
        near_misses = sorted(near_misses, key=lambda item: item["edge"], reverse=True)[:3]
        samples = " | ".join(
            f"{item['matchup']} - {item['market'].upper()} - {item['selection']} @ {item['book']} ({item['edge'] * 100:.2f}%, {item['weight']:.2f}x)"
            for item in near_misses
        )
        near_miss_text = f"; near misses: {total_near_misses} total, top {len(near_misses)} -> {samples}"

    return {
        "detail": f"scan complete{near_miss_text}",
        "count": len(alerts),
        "label": "alerts",
        "meta": {"near_miss_summary": near_miss_text.lstrip('; ').strip()} if near_miss_text else {},
    }


if __name__ == "__main__":
    scan_markets()
