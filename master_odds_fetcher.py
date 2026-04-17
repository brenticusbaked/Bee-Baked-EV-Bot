import os
from datetime import datetime
from db_manager import save_master_cache
from services.http_client import request

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# UNIFIED CONFIG: Every run is now a "Full Run" to ensure maximum bet detection
FULL_CONFIG = {
    "basketball_nba": "spreads,h2h,totals",
    "icehockey_nhl": "spreads,h2h,totals",
    "baseball_mlb": "h2h,spreads,totals,h2h_1st_5_innings", 
}

def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    # Use 'us,eu' to ensure Pinnacle (sharp) is always included for comparison
    regions = "us,eu" 
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    print(f"BEE-BAKED FETCH: Pulling full markets for all sports...")

    for sport, markets in FULL_CONFIG.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": regions,
            "markets": markets,
            "bookmakers": target_books,
            "oddsFormat": "decimal",
        }

        try:
            response = request("GET", url, params=params, timeout=15)
            response.raise_for_status() 
            cache[sport] = response.json()
            
            remaining = response.headers.get("x-requests-remaining")
            print(f"Cached: {sport} | API Credits Remaining: {remaining}")
        except Exception as exc:
            print(f"Error fetching {sport}: {exc}")

    try:
        save_master_cache(cache)
        print("Master Cache successfully updated in Supabase.")
    except Exception as exc:
        print(f"Supabase Cache Save Failed: {exc}")

    return {"detail": "cache refreshed", "count": len(cache), "label": "updates"}

if __name__ == "__main__":
    run_fetcher()
