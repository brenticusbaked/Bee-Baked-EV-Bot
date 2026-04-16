import os
import requests
from db_manager import save_master_cache

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

FETCH_CONFIG = {
    "basketball_nba": "h2h,spreads,totals",
    "icehockey_nhl": "h2h,spreads,totals",
    "baseball_mlb": "h2h,spreads,totals", 
    "soccer_epl": "h2h,spreads,totals"
}

def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return

    # FIXED: Added 'eu' for Pinnacle baseline; limited books to save credits
    regions = "us,us_ex,eu"
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    
    cache = {}
    start_usage = None
    end_usage = None

    print(f"📥 Fetching Master Cache for {len(FETCH_CONFIG)} sports...")
    
    for sport, markets in FETCH_CONFIG.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': regions,
            'markets': markets,
            'bookmakers': target_books,
            'oddsFormat': 'decimal'
        }
        
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                cache[sport] = res.json()
                current_usage = int(res.headers.get('x-requests-used', 0))
                if start_usage is None: start_usage = current_usage
                end_usage = current_usage
                print(f"✅ Cached: {sport}")
        except Exception as e:
            print(f"❌ Error fetching {sport}: {e}")

    # CLOUD SAVE: Pushes directly to Supabase
    try:
        save_master_cache(cache)
        print("🚀 Master Cache Saved to Supabase Cloud.")
    except Exception as e:
        print(f"❌ Supabase Save Failed: {e}")

if __name__ == "__main__":
    run_fetcher()