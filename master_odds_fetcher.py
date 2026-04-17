import os
from db_manager import save_master_cache
from services.http_client import request

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# TARGETED CONFIG: Exactly 3 markets total to stay at 6 credits per run (3 markets x 2 regions)
# We prioritize the highest volume +EV markets.
STRICT_CONFIG = {
    "basketball_nba": "spreads,h2h", # 2 markets
    "icehockey_nhl": "h2h",          # 1 market
    "baseball_mlb": "h2h",          # Swapped dynamically if needed
}

def run_fetcher():
    if not ODDS_API_KEY:
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    # Must use 2 regions for Sharp (EU/Pinnacle) vs Soft (US) comparison
    # Total cost = (Number of Markets) * (Number of Regions)
    regions = "us,eu" 
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    print(f"BEE-BAKED FETCH: Running 6-credit precision pull...")

    for sport, markets in STRICT_CONFIG.items():
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
            print(f"Cached {sport} ({markets}). Credits used this request: {len(markets.split(',')) * 2}")
        except Exception as exc:
            print(f"Error fetching {sport}: {exc}")

    save_master_cache(cache)
    return {"detail": "Precision fetch complete", "count": len(cache), "label": "updates"}

if __name__ == "__main__":
    run_fetcher()
