import os
from db_manager import get_untracked_bets, update_bet_clv, get_master_cache

def run_clv_tracker():
    # Pulls bets without closing lines and the fresh cloud cache
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
            
        # Locate the specific game in the cache
        game_data = next((g for g in cache[sport] if str(g.get('id')) == str(bet.get('event_id'))), None)
        
        if game_data:
            pinnacle = next((bm for bm in game_data.get('bookmakers', []) if bm['key'] == 'pinnacle'), None)
            if pinnacle:
                market_data = next((m for m in pinnacle.get('markets', []) if m['key'].upper() == bet['market'].upper()), None)
                if market_data:
                    outcome = next((o for o in market_data.get('outcomes', []) if o['name'].lower() == bet['selection'].split(' ')[0].lower()), None)
                    
                    if outcome:
                        closing_price = float(outcome['price'])
                        
                        # FIXED: Prevents division by zero or extreme anomalous edges
                        if closing_price <= 1.0:
                            print(f"⚠️ Invalid price for {bet['selection']}. Skipping.")
                            continue
                            
                        # Calculation: (Placed Odds / Closing Odds) - 1
                        clv_edge = (float(bet['odds_decimal']) / closing_price) - 1
                        update_bet_clv(bet['id'], closing_price, clv_edge)
                        print(f"✅ CLV Updated for {bet['selection']}: {clv_edge*100:.2f}%")
                    else:
                        print(f"⚠️ Outcome not found for {bet['selection']}.")
            else:
                print(f"⚠️ Pinnacle line not found in cache for {bet['selection']}.")

if __name__ == "__main__":
    run_clv_tracker()