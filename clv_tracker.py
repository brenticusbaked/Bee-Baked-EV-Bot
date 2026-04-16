import os
from db_manager import get_untracked_bets, update_bet_clv, get_master_cache

def get_decimal(american_odds):
    """Converts American odds (string or int) to Decimal float."""
    try:
        val = float(str(american_odds).replace('+', ''))
        if val > 0:
            return (val / 100) + 1
        return (100 / abs(val)) + 1
    except Exception as e:
        print(f"Error converting odds {american_odds}: {e}")
        return 2.0  # Fallback to even money

def run_clv_tracker():
    # Retrieve bets without closing lines and the fresh cloud cache
    untracked = get_untracked_bets()
    cache = get_master_cache()
    
    if not untracked or not cache:
        print("📊 CLV Audit: Nothing to track or cache empty.")
        return

    print(f"📊 Auditing CLV for {len(untracked)} bets using Cloud Cache...")
    
    for bet in untracked:
        sport = bet.get('sport')
        if sport not in cache:
            continue
            
        # Locate the specific game in the cache using event_id
        game_data = next((g for g in cache[sport] if str(g.get('id')) == str(bet.get('event_id'))), None)
        
        if game_data:
            # Find the Pinnacle (sharp) bookmaker entry
            pinnacle = next((bm for bm in game_data.get('bookmakers', []) if bm['key'] == 'pinnacle'), None)
            if pinnacle:
                # Normalize market lookup (h2h, spreads, totals)
                market_key = bet['market'].lower()
                market_data = next((m for m in pinnacle.get('markets', []) if m['key'].lower() == market_key), None)
                
                if market_data:
                    # Get the selection from your DB (e.g., 'Pittsburgh Pirates -1.5')
                    target = bet['selection'].lower().strip()
                    
                    # FUZZY MATCHING: Resolves 'Outcome not found' by checking if 
                    # one name exists within the other (handles city vs nickname)
                    outcome = None
                    for o in market_data.get('outcomes', []):
                        o_name = o['name'].lower().strip()
                        if o_name in target or target in o_name:
                            outcome = o
                            break
                    
                    if outcome:
                        closing_price = float(outcome['price'])
                        
                        # SAFETY CHECK: Prevents division by zero or extreme anomalous edges
                        if closing_price <= 1.0:
                            print(f"⚠️ Invalid price for {bet['selection']}. Skipping.")
                            continue
                            
                        # Convert American odds from DB to Decimal for calculation
                        placed_decimal = get_decimal(bet.get('odds', 0))
                        
                        # Calculation: (Placed Odds / Closing Odds) - 1
                        clv_edge = (placed_decimal / closing_price) - 1
                        
                        update_bet_clv(bet['id'], closing_price, clv_edge)
                        print(f"✅ CLV Updated for {bet['selection']}: {clv_edge*100:.2f}%")
                    else:
                        print(f"⚠️ Outcome not found for {bet['selection']}.")
            else:
                print(f"⚠️ Pinnacle line not found in cache for {bet['selection']}.")

if __name__ == "__main__":
    run_clv_tracker()