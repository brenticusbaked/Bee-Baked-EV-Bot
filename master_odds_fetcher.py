import os
import requests
from db_manager import save_master_cache

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# BUDGET UPDATE: Reduced to 3 sports and 2 primary markets
FETCH_CONFIG = {
    "basketball_nba": "h2h,spreads",
    "icehockey_nhl": "h2h,spreads",
    "baseball_mlb": "h2h,spreads"
}

def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return

    # REGIONAL OPTIMIZATION: 
    # 'us' and 'eu' are essential for soft books and the Pinnacle baseline.
    regions = "us,eu"
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    
    cache = {}
    
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
                # Monitor usage in logs
                remaining = res.headers.get('x-requests-remaining')
                used = res.headers.get('x-requests-used')
                print(f"✅ Cached: {sport} | Used: {used} | Remaining: {remaining}")
        except Exception as e:
            print(f"❌ Error fetching {sport}: {e}")

    # CLOUD SAVE
    try:
        save_master_cache(cache)
        print("🚀 Master Cache Saved to Supabase Cloud.")
    except Exception as e:
        print(f"❌ Supabase Save Failed: {e}")

if __name__ == "__main__":
    run_fetcher()