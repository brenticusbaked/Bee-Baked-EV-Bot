import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from db_manager import (
    get_master_cache,
    is_already_logged,
    load_tracker_state,
    log_bet_to_db,
    save_tracker_state,
)
from services.alerts import send_discord_alert
from services.book_weights import get_book_weights
from utils.links import sportsbook_search_link
from utils.odds import decimal_implied_probability, decimal_to_american, quarter_kelly_units
from utils.thresholds import env_float


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
UNIFIED_EV_THRESHOLD = env_float("UNIFIED_EV_THRESHOLD", 0.02)
UNIFIED_NEAR_MISS_THRESHOLD = env_float("UNIFIED_NEAR_MISS_THRESHOLD", 0.01)
LINE_MOVEMENT_MAX_BOOST = env_float("LINE_MOVEMENT_MAX_BOOST", 0.15)
LINE_MOVEMENT_MAX_PENALTY = env_float("LINE_MOVEMENT_MAX_PENALTY", 0.10)
LINE_MOVEMENT_STATE_KEY = "unified_market_scan_history"
LINE_MOVEMENT_FALLBACK_PATH = "unified_market_scan_history.json"


def get_mobile_app_link(book_key, selection_id, event_id, matchup):
    del selection_id, event_id
    return sportsbook_search_link(book_key, matchup)


def calculate_edge(offered_price: float, sharp_price: float) -> float:
    fair_probability = decimal_implied_probability(sharp_price)
    return (offered_price * fair_probability) - 1.0


def _normalize_name(value: str) -> str:
    return str(value).strip().lower()


def _normalize_point(value) -> str:
    return "" if value in (None, "") else str(value)


def _find_opposite_outcome(market_type: str, outcomes: List[Dict], target: Dict) -> Optional[Dict]:
    target_name = _normalize_name(target.get("name", ""))
    target_point = _normalize_point(target.get("point"))

    if market_type == "totals":
        for outcome in outcomes:
            if outcome is target:
                continue
            if _normalize_point(outcome.get("point")) != target_point:
                continue
            outcome_name = _normalize_name(outcome.get("name", ""))
            if {target_name, outcome_name} == {"over", "under"}:
                return outcome
        return None

    if market_type in {"spreads", "h2h"}:
        for outcome in outcomes:
            if outcome is target:
                continue
            if market_type == "spreads" and _normalize_point(outcome.get("point")) != target_point:
                continue
            return outcome
    return None


def _de_vig_fair_probability(market_type: str, sharp_outcomes: List[Dict], target: Dict) -> float:
    target_price = float(target["price"])
    opposite = _find_opposite_outcome(market_type, sharp_outcomes, target)
    if not opposite:
        return decimal_implied_probability(target_price)

    opposite_price = float(opposite["price"])
    target_raw = decimal_implied_probability(target_price)
    opposite_raw = decimal_implied_probability(opposite_price)
    vigged_total = target_raw + opposite_raw
    if vigged_total <= 0:
        return target_raw
    return target_raw / vigged_total


def _snapshot_key(event_id: str, market_type: str, soft_bet: Dict) -> str:
    return "|".join(
        [
            str(event_id),
            str(market_type),
            str(soft_bet.get("book_key", "")),
            _normalize_name(soft_bet.get("name", "")),
            _normalize_point(soft_bet.get("point")),
        ]
    )


def _line_movement_factor(previous_price: Optional[float], current_price: float, fair_decimal: float) -> float:
    if previous_price is None or previous_price <= 1.0 or fair_decimal <= 1.0:
        return 1.0

    previous_gap = previous_price - fair_decimal
    current_gap = current_price - fair_decimal

    if previous_gap <= 0 and current_gap <= 0:
        return 1.0

    if current_gap <= 0 < previous_gap:
        return 1.0 + LINE_MOVEMENT_MAX_BOOST

    gap_delta = previous_gap - current_gap
    if abs(gap_delta) < 1e-6:
        return 1.0

    baseline_gap = max(abs(previous_gap), 0.05)
    scaled_delta = max(min(gap_delta / baseline_gap, 1.0), -1.0)
    boost = scaled_delta * (LINE_MOVEMENT_MAX_BOOST if scaled_delta > 0 else LINE_MOVEMENT_MAX_PENALTY)
    return max(0.85, 1.0 + boost)


def scan_markets():
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return {"detail": "cache empty", "count": 0, "label": "alerts"}

    alerts = []
    near_misses = []
    book_weights = get_book_weights()
    previous_snapshot = load_tracker_state(LINE_MOVEMENT_STATE_KEY, LINE_MOVEMENT_FALLBACK_PATH)
    current_snapshot = {}
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
                    markets.setdefault(market_key, {"sharp": [], "soft": []})

                    if bookmaker["key"] == "pinnacle":
                        for outcome in market.get("outcomes", []):
                            markets[market_key]["sharp"].append(
                                {
                                    "name": outcome["name"],
                                    "point": outcome.get("point", ""),
                                    "price": float(outcome["price"]),
                                }
                            )
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
                sharp_outcomes = data["sharp"]
                if not sharp_outcomes:
                    continue

                best_edge = {
                    "edge": 0.0,
                    "score": 0.0,
                    "bet": None,
                    "sharp_price": None,
                    "book_weight": 1.0,
                    "movement_factor": 1.0,
                    "movement_note": "new snapshot",
                }
                for soft_bet in data["soft"]:
                    matching_sharp = next(
                        (
                            outcome
                            for outcome in sharp_outcomes
                            if _normalize_name(outcome["name"]) == _normalize_name(soft_bet["name"])
                            and _normalize_point(outcome.get("point")) == _normalize_point(soft_bet.get("point", ""))
                        ),
                        None,
                    )
                    if not matching_sharp:
                        continue

                    fair_probability = _de_vig_fair_probability(market_type, sharp_outcomes, matching_sharp)
                    if fair_probability <= 0 or fair_probability >= 1:
                        continue
                    fair_decimal = 1.0 / fair_probability
                    edge = (soft_bet["price"] * fair_probability) - 1.0
                    book_weight = book_weights.get(soft_bet["book"], 1.0)
                    snapshot_key = _snapshot_key(event["id"], market_type, soft_bet)
                    previous_price = previous_snapshot.get(snapshot_key)
                    movement_factor = _line_movement_factor(previous_price, soft_bet["price"], fair_decimal)
                    current_snapshot[snapshot_key] = soft_bet["price"]
                    weighted_score = edge * book_weight * movement_factor
                    if previous_price is None:
                        movement_note = "new market snapshot"
                    else:
                        direction = "toward fair" if movement_factor > 1.01 else "away from fair" if movement_factor < 0.99 else "flat"
                        movement_note = (
                            f"{decimal_to_american(previous_price)} -> {decimal_to_american(soft_bet['price'])} ({direction})"
                        )
                    if UNIFIED_NEAR_MISS_THRESHOLD <= edge < UNIFIED_EV_THRESHOLD:
                        near_misses.append(
                            {
                                "matchup": matchup,
                                "selection": f"{soft_bet['name']} {soft_bet['point']}".strip(),
                                "book": soft_bet["book"],
                                "edge": edge,
                                "weight": book_weight,
                                "movement_factor": movement_factor,
                            }
                        )
                    if edge >= UNIFIED_EV_THRESHOLD and weighted_score > best_edge["score"]:
                        best_edge = {
                            "edge": edge,
                            "score": weighted_score,
                            "bet": soft_bet,
                            "sharp_price": fair_decimal,
                            "book_weight": book_weight,
                            "movement_factor": movement_factor,
                            "movement_note": movement_note,
                        }

                final = best_edge["bet"]
                if not final:
                    continue

                selection = f"{final['name']} {final['point']}".strip()
                if is_already_logged(matchup, market_type, selection):
                    continue

                edge = best_edge["edge"]
                offered_price = final["price"]
                fair_price = best_edge["sharp_price"]
                book_weight = best_edge["book_weight"]
                movement_factor = best_edge["movement_factor"]
                movement_note = best_edge["movement_note"]
                units = quarter_kelly_units(edge, offered_price)
                fair_price_american = decimal_to_american(fair_price)

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
                    notes=f"book={final['book']};book_key={final['book_key']}",
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
                            f"**Line Movement:** {movement_note}\n"
                            f"**Movement Boost:** {movement_factor:.2f}x\n"
                            f"**Suggested:** {units:.2f} Units"
                        )
                    }
                )

    save_tracker_state(LINE_MOVEMENT_STATE_KEY, current_snapshot, LINE_MOVEMENT_FALLBACK_PATH)

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
            f"{item['matchup']} - {item['selection']} @ {item['book']} ({item['edge'] * 100:.2f}%, {item['weight']:.2f}x, move {item['movement_factor']:.2f}x)"
            for item in near_misses
        )
        near_miss_text = f"; near misses: {total_near_misses} total, top {len(near_misses)} -> {samples}"

    return {
        "detail": f"scan complete{near_miss_text}",
        "count": len(alerts),
        "label": "alerts",
        "meta": {"near_miss_summary": near_miss_text.lstrip("; ").strip()} if near_miss_text else {},
    }


if __name__ == "__main__":
    scan_markets()
