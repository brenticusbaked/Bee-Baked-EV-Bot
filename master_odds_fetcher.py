import os
from db_manager import save_master_cache
from services.http_client import request

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# UNIFIED CONFIG: Every run pulls everything. No more restrictive morning runs.
FULL_CONFIG = {
    "basketball_nba": "spreads,h2h,totals",
    "icehockey_nhl": "spreads,h2h,totals",
    "baseball_mlb": "h2h,spreads,totals,h2h_1st_5_innings", 
}

def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    regions = "us,eu" 
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    print("BEE-BAKED FETCH: Running UNIFIED market pull (Morning/Afternoon equalized)...")

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
            response = request("GET", url, params=params, timeout=20)
            response.raise_for_status() 
            cache[sport] = response.json()
            print(f"Cached: {sport} | Remaining Credits: {response.headers.get('x-requests-remaining')}")
        except Exception as exc:
            print(f"Error fetching {sport}: {exc}")

    save_master_cache(cache)
    return {"detail": "cache refreshed", "count": len(cache), "label": "updates"}

if __name__ == "__main__":
    run_fetcher()
