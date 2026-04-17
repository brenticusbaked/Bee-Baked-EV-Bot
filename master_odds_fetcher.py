import os
from datetime import datetime
from db_manager import save_master_cache
from services.http_client import request


ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# ECO-MODE: Morning run pulls only exactly what the models need to scan
BASE_CONFIG = {
    "basketball_nba": "spreads",
    "icehockey_nhl": "spreads",
    "baseball_mlb": "h2h",
}

# FULL-MODE: Afternoon run pulls expanded markets to track Closing Line Value
EXPANDED_CONFIG = {
    "basketball_nba": "spreads,h2h,totals",
    "icehockey_nhl": "spreads,h2h,totals",
    "baseball_mlb": "h2h,spreads,totals", # FIXED: Removed invalid 1st_half market
}


def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    # RESTORED: Both US (retail soft books) and EU (Pinnacle sharp book) are required
    regions = "us,eu"
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    # Time-based API cost saving logic
    current_hour = datetime.utcnow().hour
    if 14 <= current_hour <= 18:
        print("Afternoon Run: Using EXPANDED markets to capture Pinnacle Closing Lines...")
        active_config = EXPANDED_CONFIG
    else:
        print("Morning Run: Using BASE markets to save API credits...")
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
            cache[sport] = response.json()
            remaining = response.headers.get("x-requests-remaining")
            used = response.headers.get("x-requests-used")
            print(f"Cached: {sport} ({markets}) | Used: {used} | Remaining: {remaining}")
        except Exception as exc:
            print(f"Error fetching {sport}: {exc}")

    try:
        save_master_cache(cache)
        print("Master Cache Saved to Supabase Cloud.")
    except Exception as exc:
        print(f"Supabase Save Failed: {exc}")

    return {"detail": "cache refreshed", "count": len(cache), "label": "updates"}


if __name__ == "__main__":
    run_fetcher()
