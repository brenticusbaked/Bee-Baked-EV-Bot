import os
from collections import Counter
from datetime import timedelta
from typing import Optional

from db_manager import get_all_bets, get_market_cache, update_bet_clv, supabase, _safe_execute
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

# Player props are priced against Pinnacle only.
SHARP_PROP_BOOKS = {
    book.strip().lower()
    for book in os.getenv("PROP_SHARP_BOOKS", "pinnacle").split(",")
    if book.strip() and book.strip().lower() in {"pinnacle"}
}
PROP_DEVIG_METHOD = os.getenv("PROP_DEVIG_METHOD", "power")
ENABLE_PROP_RETAIL_FALLBACK = env_flag("ENABLE_PROP_RETAIL_FALLBACK", False)

# Pinnacle is the only allowed sharp baseline; no fallback.
SHARP_GAME_BOOKS_PRIORITY = ["pinnacle"]


def _fetch_historical_game_data(event_id: str) -> Optional[dict]:
    """Fallback: Query Supabase historical_odds for events/lines missing from live cache."""
    if not supabase or not event_id:
        return None

    def action():
        fix_resp = (
            supabase.table("fixtures")
            .select("id,sport_key,commence_time,home_team,away_team")
            .eq("id", event_id)
            .limit(1)
            .execute()
        )
        fixture = fix_resp.data[0] if (fix_resp and fix_resp.data) else {}

        odds_resp = (
            supabase.table("historical_odds")
            .select("*")
            .eq("fixture_id", event_id)
            .order("captured_at", desc=True)
            .limit(2000)
            .execute()
        )
        odds_rows = odds_resp.data if (odds_resp and odds_resp.data) else []
        if not odds_rows:
            return None

        freshest: dict = {}
        for row in odds_rows:
            outcome_key = (
                str(row.get("bookmaker_key", "")),
                str(row.get("market_key", "")),
                str(row.get("outcome_name", "")).lower().strip(),
                str(row.get("outcome_description") or "").lower().strip(),
                str(row.get("point") if row.get("point") is not None else ""),
            )
            if outcome_key not in freshest:
                freshest[outcome_key] = row

        books_dict: dict = {}
        for row in freshest.values():
            book_key = str(row.get("bookmaker_key", "")).lower()
            if not book_key:
                continue
            if book_key not in books_dict:
                books_dict[book_key] = {
                    "key": book_key,
                    "title": row.get("bookmaker_title") or book_key.title(),
                    "markets": [],
                    "_markets": {},
                }
            book = books_dict[book_key]

            market_key = str(row.get("market_key", "")).lower()
            if market_key not in book["_markets"]:
                m_dict = {"key": market_key, "outcomes": []}
                book["_markets"][market_key] = m_dict
                book["markets"].append(m_dict)
            else:
                m_dict = book["_markets"][market_key]

            price_dec = parse_float(row.get("price_decimal"))
            if price_dec is None and row.get("price") is not None:
                price_dec = parse_float(row.get("price"))

            if price_dec:
                outcome = {
                    "name": row.get("outcome_name"),
                    "price": price_dec,
                }
                if row.get("point") is not None:
                    outcome["point"] = row.get("point")
                if row.get("outcome_description"):
                    outcome["description"] = row.get("outcome_description")
                m_dict["outcomes"].append(outcome)

        bookmakers = []
        for b in books_dict.values():
            b.pop("_markets", None)
            bookmakers.append(b)

        return {
            "id": event_id,
            "sport_key": fixture.get("sport_key") or "unknown",
            "commence_time": fixture.get("commence_time"),
            "home_team": fixture.get("home_team", ""),
            "away_team": fixture.get("away_team", ""),
            "bookmakers": bookmakers,
        }

    return _safe_execute(action, None)


def _prop_consensus_close(game_data: dict, candidate_keys: list, selection_spec: dict):
    """Closing fair decimal for a player prop from the sharp-book consensus.

    Collects matching Over/Under prices from sharp books that post the prop,
    de-vigs each pair, averages to a consensus fair probability, and returns the
    fair decimal for the bet's side plus a source label. Returns ``None`` when no
    sharp book posts a clean two-way market (unless ENABLE_PROP_RETAIL_FALLBACK is True).
    """
    side = str(selection_spec.get("side", "")).lower()
    if side not in {"over", "under"}:
        return None
    over_spec = {**selection_spec, "side": "over"}
    under_spec = {**selection_spec, "side": "under"}

    def _extract_pairs(target_books=None):
        pairs, used = [], []
        for book in game_data.get("bookmakers", []):
            book_key = str(book.get("key", "")).lower()
            if target_books and book_key not in target_books:
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
            pairs.append({"over": {"price": over_price}, "under": {"price": under_price}})
            used.append(book_key)
        return pairs, used

    # Pass 1: Primary sharp books
    book_pairs, books_used = _extract_pairs(target_books=SHARP_PROP_BOOKS)

    # Pass 2: Optional retail fallback if enabled via env flag
    if not book_pairs and ENABLE_PROP_RETAIL_FALLBACK:
        book_pairs, books_used = _extract_pairs(target_books=None)

    if not book_pairs:
        return None

    fair = consensus_probabilities(book_pairs, method=PROP_DEVIG_METHOD)
    fair_probability = fair.get(side) if fair else None
    if not fair_probability or fair_probability <= 0.0:
        return None

    fair_decimal = 1.0 / fair_probability
    if books_used == ["pinnacle"]:
        label = "Pinnacle"
    elif any(b in SHARP_PROP_BOOKS for b in books_used):
        label = f"Sharp consensus ({len(books_used)})"
    else:
        label = f"Market consensus ({len(books_used)})"

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

    if not bets:
        print("CLV Audit: Nothing to track.")
        return {"detail": "nothing to track", "count": 0, "label": "tracked"}

    cutoff_date = (get_local_now() - timedelta(days=CLV_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # Deduplicate eligible bets by ID
    seen_bet_ids = set()
    eligible_bets = []
    for bet in bets:
        if str(bet.get("date", "")) >= cutoff_date and not str(bet.get("result", "")).strip():
            bet_id = str(bet.get("id"))
            if bet_id and bet_id in seen_bet_ids:
                continue
            if bet_id:
                seen_bet_ids.add(bet_id)
            eligible_bets.append(bet)

    if not eligible_bets:
        print("CLV Audit: No recent bets eligible for tracking.")
        return {"detail": "no recent bets to track", "count": 0, "label": "tracked"}

    print(f"Auditing CLV for {len(eligible_bets)} recent bets using Cloud Cache & Historical Database...")

    # Flatten cache across sports for cross-key fallback lookups
    all_cached_events = [
        event 
        for sport_events in (cache or {}).values() 
        if isinstance(sport_events, list) 
        for event in sport_events
    ]

    for bet in eligible_bets:
        sport = bet.get("sport")
        target_event_id = str(bet.get("event_id"))

        events = (cache or {}).get(sport) or []
        game_data = next((game for game in events if str(game.get("id")) == target_event_id), None)

        if not game_data:
            game_data = next((game for game in all_cached_events if str(game.get("id")) == target_event_id), None)

        is_historical = False
        if not game_data:
            game_data = _fetch_historical_game_data(target_event_id)
            is_historical = True

        if not game_data:
            if not events:
                missing_sport_counts[str(sport)] += 1
            else:
                missing_event_counts[(str(sport), target_event_id)] += 1
            continue

        market_key = str(bet["market"]).lower()
        candidate_keys = [market_key]

        if market_key in {"model_nba_spread", "model_nhl_puckline"}:
            candidate_keys.append("spreads")
        if market_key == "model_mlb_f5":
            candidate_keys.extend(["h2h_1st_5_innings", "h2h_1st_half"])

        if market_key in {"moneyline", "ml"}:
            candidate_keys.append("h2h")
        if market_key in {"spread", "runline", "puckline"}:
            candidate_keys.append("spreads")
        if market_key in {"total", "totals", "over/under", "o/u"}:
            candidate_keys.append("totals")

        selection_spec = parse_selection(bet["market"], bet["selection"])
        closing_source = "Pinnacle"

        if selection_spec.get("type") == "player_prop":
            consensus = _prop_consensus_close(game_data, candidate_keys, selection_spec)
            if consensus is None and not is_historical:
                hist_data = _fetch_historical_game_data(target_event_id)
                if hist_data:
                    consensus = _prop_consensus_close(hist_data, candidate_keys, selection_spec)
            if consensus is None:
                print(
                    f"CLV: No sharp-book prop line for {bet['selection']} "
                    f"(tried {candidate_keys})."
                )
                continue
            closing_price_decimal, closing_source = consensus
        else:
            def _find_sharp_closing_price(g_data):
                for priority_book in SHARP_GAME_BOOKS_PRIORITY:
                    found = next(
                        (b for b in g_data.get("bookmakers", []) if str(b.get("key")).lower() == priority_book),
                        None,
                    )
                    if not found:
                        continue
                    m_data = next(
                        (m for m in found.get("markets", []) if str(m.get("key", "")).lower() in candidate_keys),
                        None,
                    )
                    if not m_data:
                        continue
                    out = next(
                        (item for item in m_data.get("outcomes", []) if outcome_matches(selection_spec, item)),
                        None,
                    )
                    if out and parse_float(out.get("price")):
                        return float(out["price"]), priority_book.title()
                return None, None

            closing_price_decimal, closing_source = _find_sharp_closing_price(game_data)
            if closing_price_decimal is None and not is_historical:
                hist_data = _fetch_historical_game_data(target_event_id)
                if hist_data:
                    closing_price_decimal, closing_source = _find_sharp_closing_price(hist_data)

            if not closing_price_decimal:
                print(f"CLV: Line or market not found for {bet['selection']} (tried {candidate_keys}).")
                continue

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
            f"{sum(missing_event_counts.values())} bet(s) because the event ID was not present in cache or database "
            f"({summary})."
        )

    return {"detail": f"clv audit complete | movement alerts={alert_count}", "count": tracked_count, "label": "tracked"}


if __name__ == "__main__":
    run_clv_tracker()