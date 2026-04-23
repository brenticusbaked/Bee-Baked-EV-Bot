import os
from db_manager import save_master_cache
from services.http_client import request
from utils.thresholds import env_float

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Clean 6-credit pull: one market per sport across two regions.
# NBA and NHL focus on spreads; MLB keeps H2H so the MLB model can still shop prices.
STRICT_CONFIG = {
    "basketball_nba": "spreads",
    "icehockey_nhl": "spreads",
    "baseball_mlb": "h2h",
}

def run_fetcher():
    if not ODDS_API_KEY:
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    # Must use 2 regions for Sharp (EU/Pinnacle) vs Soft (US) comparison
    # Total cost = (Number of Markets) * (Number of Regions)
    regions = "us,eu" 
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    print("BEE-BAKED FETCH: Running 6-credit precision pull...")

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
