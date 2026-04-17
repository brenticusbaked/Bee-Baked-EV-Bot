import os
from datetime import datetime
from db_manager import save_master_cache
from services.http_client import request

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# ECO-MODE: Expanded to ensure the morning scan has enough data to find edges
BASE_CONFIG = {
    "basketball_nba": "spreads,h2h,totals",
    "icehockey_nhl": "spreads,h2h,totals",
    "baseball_mlb": "h2h,spreads,totals",
}

# FULL-MODE: Added specific 1st 5 innings market for MLB CLV tracking
EXPANDED_CONFIG = {
    "basketball_nba": "spreads,h2h,totals",
    "icehockey_nhl": "spreads,h2h,totals",
    "baseball_mlb": "h2h,spreads,totals,h2h_1st_5_innings", # FIXED KEY
}

def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    regions = "us,eu" # Restored EU for Pinnacle
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    current_hour = datetime.utcnow().hour
    if 14 <= current_hour <= 18:
        print("Afternoon Run: Using EXPANDED markets...")
        active_config = EXPANDED_CONFIG
    else:
        print("Morning Run: Using BASE markets...")
        active_config = BASE_CONFIG

    print(f"Fetching Master Cache for {len(active_config)} sports...")

    for sport, markets in active_config.items():
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
            response.raise_for_status() # Catch 422 errors properly
            cache[sport] = response.json()
            print(f"Cached: {sport} | Remaining: {response.headers.get('x-requests-remaining')}")
        except Exception as exc:
            print(f"Error fetching {sport}: {exc}")

    save_master_cache(cache)
    return {"detail": "cache refreshed", "count": len(cache), "label": "updates"}

if __name__ == "__main__":
    run_fetcher()
