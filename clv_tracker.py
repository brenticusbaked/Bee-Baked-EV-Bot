from db_manager import get_master_cache, get_untracked_bets, update_bet_clv
from services.bet_logic import outcome_matches, parse_selection
from utils.odds import american_to_decimal, decimal_to_american, parse_float


def run_clv_tracker():
    untracked = get_untracked_bets()
    cache = get_master_cache()
    tracked_count = 0

    if not untracked or not cache:
        print("CLV Audit: Nothing to track or cache empty.")
        return {"detail": "nothing to track", "count": 0, "label": "tracked"}

    print(f"Auditing CLV for {len(untracked)} bets using Cloud Cache...")

    for bet in untracked:
        sport = bet.get("sport")
        events = cache.get(sport)
        if not events:
            continue

        game_data = next((game for game in events if str(game.get("id")) == str(bet.get("event_id"))), None)
        if not game_data:
            continue

        pinnacle = next((book for book in game_data.get("bookmakers", []) if book.get("key") == "pinnacle"), None)
        if not pinnacle:
            print(f"Pinnacle line not found in cache for {bet['selection']}.")
            continue

        market_key = str(bet["market"]).lower()
        candidate_keys = [market_key]
        
        # --- NEW: Expanded API Key Translations ---
        # Map specific model markets
        if market_key in {"model_nba_spread", "model_nhl_puckline"}:
            candidate_keys.append("spreads")
        if market_key == "model_mlb_f5":
            candidate_keys.append("h2h_1st_half")
            
        # Map standard markets
        if market_key in {"moneyline", "ml"}:
            candidate_keys.append("h2h")
        if market_key in {"spread", "runline", "puckline"}:
            candidate_keys.append("spreads")
        if market_key in {"total", "totals", "over/under", "o/u"}:
            candidate_keys.append("totals")

        market_data = next(
            (market for market in pinnacle.get("markets", []) if market.get("key", "").lower() in candidate_keys),
            None,
        )
        if not market_data:
            print(f"Market not found for {bet['selection']}.")
            continue

        selection_spec = parse_selection(bet["market"], bet["selection"])
        outcome = next(
            (item for item in market_data.get("outcomes", []) if outcome_matches(selection_spec, item)),
            None,
        )
        if not outcome:
            print(f"Outcome not found for {bet['selection']}.")
            continue

        closing_price_decimal = float(outcome["price"])
        if closing_price_decimal <= 1.0:
            print(f"Invalid price for {bet['selection']}. Skipping.")
            continue

        placed_decimal = parse_float(bet.get("odds_decimal")) or american_to_decimal(bet.get("odds", 0))
        clv_edge_pct = ((placed_decimal / closing_price_decimal) - 1.0) * 100.0
        closing_price_american = decimal_to_american(closing_price_decimal)
        update_bet_clv(bet["id"], closing_price_american, closing_price_decimal, round(clv_edge_pct, 4))
        print(f"CLV Updated for {bet['selection']}: {clv_edge_pct:.2f}%")
        tracked_count += 1

    return {"detail": "clv audit complete", "count": tracked_count, "label": "tracked"}


if __name__ == "__main__":
    run_clv_tracker()
