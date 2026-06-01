import os
import re
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from db_manager import get_master_cache, get_today_bets, load_tracker_state, save_tracker_state
from services.alerts import send_discord_alert
from services.bet_logic import outcome_matches, parse_selection
from services.discord_channels import RESULTS_WEBHOOK_URL
from utils.config import env_flag
from utils.odds import american_to_decimal, decimal_to_american, parse_float
from utils.thresholds import env_float
from utils.time import get_local_date_str


STATE_KEY = "bet_lifecycle_monitor"
STATE_FILE = "bet_lifecycle_state.json"
PLAYABLE_EDGE = env_float("BET_LIFECYCLE_PLAYABLE_EDGE", 0.005)
MOVEMENT_NOTIFY_PCT = env_float("BET_LIFECYCLE_MOVEMENT_NOTIFY_PCT", 1.0)


def _notes_value(notes: str, key: str) -> Optional[str]:
    if not isinstance(notes, str):
        return None
    match = re.search(rf"(?:^|;){re.escape(key)}=([^;]+)", notes)
    return match.group(1).strip() if match else None


def _candidate_market_keys(market_key: str) -> list[str]:
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
        candidates.extend(["h2h_1st_5_innings", "h2h_1st_half"])
    return candidates


def _find_current_quote(bet: dict, cache: Dict[str, list]) -> Optional[Tuple[float, str]]:
    events = cache.get(bet.get("sport")) or []
    event = next((item for item in events if str(item.get("id")) == str(bet.get("event_id"))), None)
    if not event:
        return None

    book_key = _notes_value(bet.get("notes", ""), "book_key")
    if not book_key:
        return None
    book = next((item for item in event.get("bookmakers", []) if str(item.get("key")).lower() == book_key.lower()), None)
    if not book:
        return None

    candidate_keys = set(_candidate_market_keys(bet.get("market", "")))
    market = next((item for item in book.get("markets", []) if str(item.get("key", "")).lower() in candidate_keys), None)
    if not market:
        return None

    selection_spec = parse_selection(bet.get("market", ""), bet.get("selection", ""))
    outcome = next((item for item in market.get("outcomes", []) if outcome_matches(selection_spec, item)), None)
    if not outcome:
        return None

    current_decimal = float(outcome["price"])
    book_title = book.get("title") or book.get("key") or book_key
    return current_decimal, str(book_title)


def _load_state() -> dict:
    state = load_tracker_state(STATE_KEY, STATE_FILE)
    if not isinstance(state, dict) or state.get("date") != get_local_date_str():
        return {"date": get_local_date_str(), "bets": {}}
    state.setdefault("bets", {})
    return state


def _save_state(state: dict) -> None:
    state["date"] = get_local_date_str()
    save_tracker_state(STATE_KEY, state, STATE_FILE)


def _decimal_from_values(decimal_value, american_value) -> Optional[float]:
    parsed = parse_float(decimal_value)
    if parsed:
        return parsed
    try:
        return american_to_decimal(american_value)
    except (TypeError, ValueError):
        return None


def _send_lifecycle_alert(bet: dict, status: str, book: str, current_decimal: float, edge_now: float, line_move_pct: float) -> bool:
    if not RESULTS_WEBHOOK_URL:
        return False
    status_title = "STILL PLAYABLE" if status == "playable" else "NO LONGER PLAYABLE"
    color = 5763719 if status == "playable" else 15158332
    matchup = bet.get("matchup", "Unknown matchup")
    payload = {
        "embeds": [
            {
                "description": (
                    f"**BET LIFECYCLE: {status_title}**\n\n"
                    f"**Match:** {matchup}\n"
                    f"**Bet:** {bet.get('selection')}\n"
                    f"**Market:** {bet.get('market')}\n"
                    f"**Book:** {book} @ {decimal_to_american(current_decimal)}\n"
                    f"**Current Edge:** {edge_now * 100:.2f}%\n"
                    f"**Line Move vs Alert:** {line_move_pct:+.2f}%"
                ),
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }
    return send_discord_alert(
        payload,
        source="bet_lifecycle",
        alert_type=f"bet_{status}",
        dedupe_key=f"{bet.get('id')}:{status}:{decimal_to_american(current_decimal)}",
        webhook_url=RESULTS_WEBHOOK_URL,
    )


def run_bet_lifecycle_monitor() -> dict:
    cache = get_master_cache() or {}
    bets = get_today_bets()
    if not cache or not bets:
        return {"detail": "no cache or no today bets", "count": 0, "label": "updates"}

    state = _load_state()
    sent = 0
    tracked = 0

    for bet in bets:
        if str(bet.get("result", "")).strip():
            continue
        quote = _find_current_quote(bet, cache)
        if not quote:
            continue
        current_decimal, book = quote
        fair_decimal = _decimal_from_values(bet.get("fair_price_decimal"), bet.get("fair_price"))
        placed_decimal = _decimal_from_values(bet.get("odds_decimal"), bet.get("odds"))
        if not fair_decimal or not placed_decimal:
            continue

        edge_now = (current_decimal / fair_decimal) - 1.0
        line_move_pct = ((current_decimal / placed_decimal) - 1.0) * 100.0
        status = "playable" if edge_now >= PLAYABLE_EDGE else "closed"
        bet_id = str(bet.get("id"))
        previous = state["bets"].get(bet_id, {})
        previous_move = previous.get("line_move_pct")

        state["bets"][bet_id] = {
            "status": status,
            "current_decimal": current_decimal,
            "edge_now": edge_now,
            "line_move_pct": line_move_pct,
        }
        tracked += 1

        changed_status = previous and previous.get("status") != status
        moved_materially = previous_move is not None and abs(float(previous_move) - line_move_pct) >= MOVEMENT_NOTIFY_PCT
        should_notify_initial = not previous and env_flag("BET_LIFECYCLE_NOTIFY_INITIAL", False)
        if changed_status or moved_materially or should_notify_initial:
            if _send_lifecycle_alert(bet, status, book, current_decimal, edge_now, line_move_pct):
                sent += 1

    _save_state(state)
    return {"detail": f"lifecycle monitor complete | tracked={tracked}", "count": sent, "label": "updates"}


if __name__ == "__main__":
    run_bet_lifecycle_monitor()
