import os
import requests
import json
from db_manager import get_untracked_bets, update_bet_clv

# Retrieve API Key for final closing line checks
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

def to_decimal(price):
    try:
        p = float(price)
        if p > 100: return (p / 100) + 1
        if p < -100: return (100 / abs(p)) + 1
        return p
    except: return 1.909

def run_clv_tracker():
    """
    Identifies bets in the database missing CLV data, fetches the sharpest 
    closing price from Pinnacle, and calculates the final edge.
    """
    # Fetch bets from DB that haven't been audited for CLV yet
    bets = get_untracked_bets()
    if not bets:
        print("No new bets requiring CLV tracking.")
        return

    # Comprehensive list of monitored sources for Kentucky & Offshore
    soft_books = [
        'fanduel', 'draftkings', 'betmgm', 'bet365', 'espn', 'fanatics', 
        'caesars', 'betrivers', 'bovada', 'betonline', 'bookmaker', 
        'lowvig', 'betus', 'mybookie', 'prizepicks', 'pick6', 'novig', 'dabble'
    ]

    print(f"📊 Auditing CLV for {len(bets)} bets...")

    for bet in bets:
        # We use Pinnacle as the 'Gold Standard' for the true closing market price
        sport = bet.get('sport', 'basketball_nba')
        event_id = bet.get('event_id')
        
        if not event_id: continue

        url = f"https://api.the-odds-api.com/v4/sports/{sport}/events/{event_id}/odds"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': 'us,eu,us_dfs,us_ex',
            'markets': 'h2h,spreads,totals',
            'bookmakers': 'pinnacle'
        }

        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                sharp_price = None
                
                # Extract Pinnacle's final price for the specific selection
                for bm in data.get('bookmakers', []):
                    if bm['key'] == 'pinnacle':
                        for mkt in bm.get('markets', []):
                            for outcome in mkt.get('outcomes', []):
                                if outcome['name'] == bet['selection']:
                                    sharp_price = float(outcome['price'])
                
                if sharp_price:
                    # Calculate CLV: (Your Price / Sharp Price) - 1
                    user_price = to_decimal(bet['odds'])
                    clv_edge = (user_price / sharp_price) - 1
                    
                    # Update the database with the final audit results
                    update_bet_clv(bet['id'], sharp_price, clv_edge)
                    print(f"✅ CLV Updated for {bet['selection']}: {clv_edge*100:.2f}%")
            else:
                print(f"⚠️ Could not fetch closing lines for Event {event_id}")
        except Exception as e:
            print(f"❌ Error tracking CLV: {e}")

if __name__ == "__main__":
    run_clv_tracker()