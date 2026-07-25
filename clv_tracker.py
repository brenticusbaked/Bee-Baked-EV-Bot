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
from utils.prop_pricing import consensus_probabilities
from utils.config import env_flag
from utils.time import get_local_now


CLV_LOOKBACK_DAYS = int(os.getenv("CLV_LOOKBACK_DAYS", "2"))
CLV_NOTIFY_MIN_CHANGE_PCT = float(os.getenv("CLV_NOTIFY_MIN_CHANGE_PCT", "0.75"))
CLV_MAX_ALERTS = int(os.getenv("CLV_MAX_ALERTS", "10"))
# Player props are priced against the SHARP-BOOK CONSENSUS (Pinnacle rarely posts
# them), matching how the bet's fair value was computed at placement. Mirror that
# for the CLV closing baseline so prop bets get a CLV instead of "Market not
# found" against a Pinnacle line that never existed.
SHARP_PROP_BOOKS = {
    book.strip().lower()
    for book in os.getenv("PROP_SHARP_BOOKS", "pinnacle,bookmaker,circa,cris").split(",")
    if book.strip()
}
PROP_DEVIG_METHOD = os.getenv("PROP_DEVIG_METHOD", "multiplicative")


def _prop_consensus_close(game_data: dict, candidate_keys: list, selection_spec: dict):
    """Closing fair decimal for a player prop from the sharp-book consensus.

    Collects matching Over/Under prices from each sharp book that posts the prop,
    de-vigs each pair, averages to a consensus fair probability, and returns the
    fair decimal for the bet's side plus a source label. Returns ``None`` when no
    sharp book posts a clean two-way market for the player/line. Audit-only: does
    not touch bet-placement math.
    """
    side = str(selection_spec.get("side", "")).lower()
    if side not in {"over", "under"}:
        return None
    over_spec = {**selection_spec, "side": "over"}
    under_spec = {**selection_spec, "side": "under"}

    book_pairs = []
    books_used = []
    for book in game_data.get("bookmakers", []):
        if str(book.get("key", "")).lower() not in SHARP_PROP_BOOKS:
            continue
        market = next(
            (m for m in book.get("markets", []) if str(m.get("key", "")).lower() in candidate_keys),
            None,
        )
        if not market:
            continue
        outcomes = market.get("outcomes", [])
        over = next((o for o in outcomes if outcome_matches(over_spec, o)), None)
        under = next((o for o in outcomes if outcome_matches(under_spec, o)), None)
        over_price = parse_float(over.get("price")) if over else None
        under_price = parse_float(under.get("price")) if under else None
        if not over_price or not under_price or over_price <= 1.0 or under_price <= 1.0:
            continue
        book_pairs.append({"over": {"price": over_price}, "under": {"price": under_price}})
        books_used.append(str(book.get("key")).lower())

    fair = consensus_probabilities(book_pairs, method=PROP_DEVIG_METHOD)
    fair_probability = fair.get(side) if fair else None
    if not fair_probability or fair_probability <= 0.0:
        return None
    fair_decimal = 1.0 / fair_probability
    label = "Pinnacle" if books_used == ["pinnacle"] else f"Sharp consensus ({len(books_used)})"
    return fair_decimal, label


def _bet_book(bet: dict) -> str:
    return str(bet.get("sportsbook") or _extract_book(bet.get("notes", "")))


def _send_clv_update(bet: dict, closing_price_american: str, clv_edge_pct: float, previous_clv, closing_source: str = "Pinnacle") -> bool:
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
                    f"**{closing_source} Now:** {closing_price_american}\n"
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

        selection_spec = parse_selection(bet["market"], bet["selection"])
        closing_source = "Pinnacle"

        if selection_spec.get("type") == "player_prop":
            # Props: Pinnacle rarely posts them; price the close off the sharp
            # consensus, exactly as the bet's fair value was computed.
            consensus = _prop_consensus_close(game_data, candidate_keys, selection_spec)
            if consensus is None:
                print(
                    f"CLV: No sharp-book prop line for {bet['selection']} "
                    f"(tried {candidate_keys})."
                )
                continue
            closing_price_decimal, closing_source = consensus
        else:
            pinnacle = next(
                (book for book in game_data.get("bookmakers", []) if book.get("key") == "pinnacle"),
                None,
            )
            if not pinnacle:
                print(f"CLV: Pinnacle line not found in cache for {bet['selection']}.")
                continue

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
            if _send_clv_update(bet, closing_price_american, clv_edge_pct, previous_clv, closing_source):
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
