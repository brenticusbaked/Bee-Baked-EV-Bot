import os
from db_manager import get_untracked_bets, update_bet_clv, get_master_cache

def to_decimal(price):
    try:
        p = float(price)
        if p > 100: return (p / 100) + 1
        if p < -100: return (100 / abs(p)) + 1
        return p
    except: return 1.909

def run_clv_tracker():
    """
    Identifies bets missing CLV data and audits them against the 
    Supabase Master Cache, costing 0 API credits.
    """
    bets = get_untracked_bets()
    if not bets:
        print("No new bets requiring CLV tracking.")
        return

    print(f"📊 Auditing CLV for {len(bets)} bets using Cloud Cache...")
    
    # Load the global cache from Supabase instead of calling The Odds API
    cache = get_master_cache()
    if not cache:
        print("⚠️ Cloud cache is empty or unavailable.")
        return

    tracked_count = 0

    for bet in bets:
        sport = bet.get('sport', 'basketball_nba')
        event_id = bet.get('event_id')
        selection = bet.get('selection', '')
        
        if not event_id or sport not in cache: 
            continue

        sharp_price = None
        
        # Search the cached JSON for the specific event and Pinnacle's line
        for game in cache[sport]:
            if game['id'] == event_id:
                for bm in game.get('bookmakers', []):
                    if bm['key'] == 'pinnacle':
                        for mkt in bm.get('markets', []):
                            for outcome in mkt.get('outcomes', []):
                                
                                # FIX: Safely match H2H, Spreads, and Totals
                                name = str(outcome.get('name', ''))
                                point = str(outcome.get('point', ''))
                                
                                # Reconstruct the string to match how unified_bot logged it (e.g. "Lakers -5.5")
                                reconstructed_selection = f"{name} {point}".strip()
                                
                                if selection in (reconstructed_selection, name):
                                    sharp_price = float(outcome['price'])
                break # Game found, stop searching
                
        if sharp_price:
            # Calculate CLV: (Your Price / Sharp Price) - 1
            user_price = to_decimal(bet['odds'])
            clv_edge = (user_price / sharp_price) - 1
            
            # Update the database so it is never audited again
            update_bet_clv(bet['id'], sharp_price, clv_edge)
            print(f"✅ CLV Updated for {selection}: {clv_edge*100:.2f}%")
            tracked_count += 1
        else:
            print(f"⚠️ Pinnacle line not found in cache for {selection} (Game may have started).")

    print(f"✅ CLV Audit Complete. Cost: 0 API Credits.")

if __name__ == "__main__":
    run_clv_tracker()