import os
from collections import Counter
from datetime import timedelta

from db_manager import get_all_bets, get_market_cache, update_bet_clv
from services.alerts import send_discord_alert
from services.bet_logic import outcome_matches, parse_selection
from services.book_weights import _extract_book
from services.discord_channels import RESULTS_WEBHOOK_URL
from services.history_calibration import clv_baseline_for
from utils.odds import american_to_decimal, decimal_to_american, parse_float
from utils.config import env_flag
from utils.time import get_local_now


CLV_LOOKBACK_DAYS = int(os.getenv("CLV_LOOKBACK_DAYS", "2"))
CLV_NOTIFY_MIN_CHANGE_PCT = float(os.getenv("CLV_NOTIFY_MIN_CHANGE_PCT", "0.75"))
CLV_MAX_ALERTS = int(os.getenv("CLV_MAX_ALERTS", "10"))


def _bet_book(bet: dict) -> str:
    return str(bet.get("sportsbook") or _extract_book(bet.get("notes", "")))


def _send_clv_update(bet: dict, closing_price_american: str, clv_edge_pct: float, previous_clv) -> bool:
    if not RESULTS_WEBHOOK_URL or not env_flag("CLV_SEND_DISCORD_UPDATES", True):
        return False
    previous_numeric = parse_float(previous_clv)
    previous_text = "first track" if previous_numeric is None else f"was {previous_numeric:+.2f}%"

    baseline = clv_baseline_for(_bet_book(bet))
    baseline_line = ""
    if baseline is not None:
        verdict = "above" if clv_edge_pct >= baseline else "below"
        baseline_line = (
            f"\n**Your CLV baseline ({_bet_book(bet)}):** {baseline:+.2f}% "
            f"— this bet is {verdict} it"
        )

    payload = {
        "embeds": [
            {
                "description": (
                    "**CLV MOVEMENT UPDATE**\n\n"
                    f"**Match:** {bet.get('matchup')}\n"
                    f"**Bet:** {bet.get('selection')}\n"
                    f"**Market:** {bet.get('market')}\n"
                    f"**Alerted Odds:** {bet.get('odds')}\n"
                    f"**Pinnacle Now:** {closing_price_american}\n"
                    f"**CLV Edge:** {clv_edge_pct:+.2f}% ({previous_text})"
                    f"{baseline_line}"
                ),
                "color": 5763719 if clv_edge_pct >= 0 else 15158332,
            }
        ]
    }
    return send_discord_alert(
        payload,
        source="clv_tracker",
        alert_type="clv_movement",
        dedupe_key=f"{bet.get('id')}:{closing_price_american}:{clv_edge_pct:.2f}",
        webhook_url=RESULTS_WEBHOOK_URL,
    )


def _placed_decimal(bet: dict):
    parsed = parse_float(bet.get("odds_decimal"))
    if parsed:
        return parsed
    try:
        return american_to_decimal(bet.get("odds", 0))
    except (TypeError, ValueError):
        return None


def run_clv_tracker():
    bets = get_all_bets()
    cache = get_market_cache()
    tracked_count = 0
    alert_count = 0
    missing_sport_counts: Counter[str] = Counter()
    missing_event_counts: Counter[tuple[str, str]] = Counter()

    if not bets or not cache:
        print("CLV Audit: Nothing to track or cache empty.")
        return {"detail": "nothing to track", "count": 0, "label": "tracked"}

    cutoff_date = (get_local_now() - timedelta(days=CLV_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    eligible_bets = [
        bet
        for bet in bets
        if str(bet.get("date", "")) >= cutoff_date and not str(bet.get("result", "")).strip()
    ]

    if not eligible_bets:
        print("CLV Audit: No recent bets eligible for tracking.")
        return {"detail": "no recent bets to track", "count": 0, "label": "tracked"}

    print(f"Auditing CLV for {len(eligible_bets)} recent bets using Cloud Cache...")

    for bet in eligible_bets:
        sport = bet.get("sport")
        events = cache.get(sport)
        if not events:
            missing_sport_counts[str(sport)] += 1
            continue

        game_data = next(
            (game for game in events if str(game.get("id")) == str(bet.get("event_id"))),
            None,
        )
        if not game_data:
            missing_event_counts[(str(sport), str(bet.get("event_id")))] += 1
            continue

        pinnacle = next(
            (book for book in game_data.get("bookmakers", []) if book.get("key") == "pinnacle"),
            None,
        )
        if not pinnacle:
            print(f"CLV: Pinnacle line not found in cache for {bet['selection']}.")
            continue

        market_key = str(bet["market"]).lower()
        candidate_keys = [market_key]

        # Expanded API key translations for model markets
        if market_key in {"model_nba_spread", "model_nhl_puckline"}:
            candidate_keys.append("spreads")
        if market_key == "model_mlb_f5":
            candidate_keys.extend(["h2h_1st_5_innings", "h2h_1st_half"])

        # Standard market key aliases
        if market_key in {"moneyline", "ml"}:
            candidate_keys.append("h2h")
        if market_key in {"spread", "runline", "puckline"}:
            candidate_keys.append("spreads")
        if market_key in {"total", "totals", "over/under", "o/u"}:
            candidate_keys.append("totals")

        market_data = next(
            (
                market
                for market in pinnacle.get("markets", [])
                if market.get("key", "").lower() in candidate_keys
            ),
            None,
        )
        if not market_data:
            # FIXED: Silence the warning for MLB F5 since the Odds API doesn't support it
            if market_key == "model_mlb_f5":
                continue
                
            available_keys = [m.get("key") for m in pinnacle.get("markets", [])]
            print(
                f"CLV: Market not found for {bet['selection']} "
                f"(tried {candidate_keys}, available: {available_keys})."
            )
            continue

        selection_spec = parse_selection(bet["market"], bet["selection"])
        outcome = next(
            (item for item in market_data.get("outcomes", []) if outcome_matches(selection_spec, item)),
            None,
        )
        if not outcome:
            available_outcomes = [
                {"name": o.get("name"), "point": o.get("point")}
                for o in market_data.get("outcomes", [])
            ]
            print(
                f"CLV: Outcome not found for '{bet['selection']}' "
                f"(spec={selection_spec}, available={available_outcomes})."
            )
            continue

        closing_price_decimal = float(outcome["price"])
        if closing_price_decimal <= 1.0:
            print(f"CLV: Invalid price {closing_price_decimal} for {bet['selection']}. Skipping.")
            continue

        placed_decimal = _placed_decimal(bet)
        if not placed_decimal:
            print(f"CLV: Invalid placed odds for {bet['selection']}. Skipping.")
            continue
        clv_edge_pct = ((placed_decimal / closing_price_decimal) - 1.0) * 100.0
        closing_price_american = decimal_to_american(closing_price_decimal)
        previous_clv = bet.get("clv_edge_pct")
        update_bet_clv(bet["id"], closing_price_american, closing_price_decimal, round(clv_edge_pct, 4))
        print(f"CLV Updated for {bet['selection']}: {clv_edge_pct:.2f}%")
        tracked_count += 1

        previous_numeric = parse_float(previous_clv)
        should_alert = previous_numeric is None or abs(previous_numeric - clv_edge_pct) >= CLV_NOTIFY_MIN_CHANGE_PCT
        if should_alert and alert_count < CLV_MAX_ALERTS:
            if _send_clv_update(bet, closing_price_american, clv_edge_pct, previous_clv):
                alert_count += 1

    if missing_sport_counts:
        summary = ", ".join(f"{sport}:{count}" for sport, count in missing_sport_counts.most_common(5))
        print(
            "CLV: skipped "
            f"{sum(missing_sport_counts.values())} bet(s) because the sport was absent from the current cache snapshot "
            f"({summary})."
        )
    if missing_event_counts:
        summary = ", ".join(
            f"{sport}:{event_id[:8]}…x{count}"
            for (sport, event_id), count in missing_event_counts.most_common(5)
        )
        print(
            "CLV: skipped "
            f"{sum(missing_event_counts.values())} bet(s) because the event ID was not present in cache "
            f"({summary})."
        )

    return {"detail": f"clv audit complete | movement alerts={alert_count}", "count": tracked_count, "label": "tracked"}


if __name__ == "__main__":
    run_clv_tracker()
