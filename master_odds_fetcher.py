import os

from db_manager import save_master_cache
from services.http_client import request


ODDS_API_KEY = os.getenv("ODDS_API_KEY")

FETCH_CONFIG = {
    "basketball_nba": "spreads",
    "icehockey_nhl": "spreads",
    "baseball_mlb": "h2h",
}


def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return

    regions = "us,eu"
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    print(f"Fetching Master Cache for {len(FETCH_CONFIG)} sports...")

    for sport, markets in FETCH_CONFIG.items():
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
            print(f"Cached: {sport} | Used: {used} | Remaining: {remaining}")
        except Exception as exc:
            print(f"Error fetching {sport}: {exc}")

    try:
        save_master_cache(cache)
        print("Master Cache Saved to Supabase Cloud.")
    except Exception as exc:
        print(f"Supabase Save Failed: {exc}")


if __name__ == "__main__":
    run_fetcher()
