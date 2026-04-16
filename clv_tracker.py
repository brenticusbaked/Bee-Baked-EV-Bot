import os
from db_manager import get_untracked_bets, update_bet_clv, get_master_cache

def get_decimal(american_odds):
    """Converts American string/int odds to Decimal float."""
    try:
        val = float(str(american_odds).replace('+', ''))
        if val > 0:
            return (val / 100) + 1
        return (100 / abs(val)) + 1
    except:
        return 2.0 # Default fallback

def run_clv_tracker():
    untracked = get_untracked_bets()
    cache = get_master_cache()
    
    if not untracked or not cache:
        print("📊 CLV Audit: Nothing to track or cache empty.")
        return

    print(f"📊 Auditing CLV for {len(untracked)} bets using Cloud Cache...")
    
    for bet in untracked:
        sport = bet.get('sport')
        if sport not in cache: continue
            
        game_data = next((g for g in cache[sport] if str(g.get('id')) == str(bet.get('event_id'))), None)
        
        if game_data:
            pinnacle = next((bm for bm in game_data.get('bookmakers', []) if bm['key'] == 'pinnacle'), None)
            if pinnacle:
                # Standardize market lookup
                market_key = bet['market'].lower()
                market_data = next((m for m in pinnacle.get('markets', []) if m['key'].lower() == market_key), None)
                
                if market_data:
                    # Match selection (handle name stripping for 'Home', 'Away', or Player Names)
                    target = bet['selection'].split(' ')[0].lower()
                    outcome = next((o for o in market_data.get('outcomes', []) if o['name'].lower() == target), None)
                    
                    if outcome:
                        closing_price = float(outcome['price'])
                        if closing_price <= 1.0: continue
                            
                        # FIXED: Use the helper to avoid KeyError: 'odds_decimal'
                        placed_decimal = get_decimal(bet['odds'])
                        clv_edge = (placed_decimal / closing_price) - 1
                        
                        update_bet_clv(bet['id'], closing_price, clv_edge)
                        print(f"✅ CLV Updated for {bet['selection']}: {clv_edge*100:.2f}%")
                    else:
                        print(f"⚠️ Outcome not found for {bet['selection']}.")
            else:
                print(f"⚠️ Pinnacle line not found in cache for {bet['selection']}.")

if __name__ == "__main__":
    run_clv_tracker()