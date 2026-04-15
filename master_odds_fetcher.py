import os
import json
import requests

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

FETCH_CONFIG = {
    "basketball_nba": "h2h,spreads,totals",
    "icehockey_nhl": "h2h,spreads,totals",
    "baseball_mlb": "h2h,spreads,totals,h2h_1st_half",
    "soccer_epl": "h2h,spreads,totals",
    "esports_counterstrike": "h2h",
    "tennis_atp_wimbledon": "h2h"
}
BOOKMAKERS = "fanduel,draftkings,betmgm,bet365,espn,fanatics,pinnacle"

def run_fetcher():
    if not ODDS_API_KEY:
        print("API Key missing, skipping fetch.")
        return

    cache = {}
    total_calls = 0

    print("📥 Pulling market data into Master Cache...")
    for sport, markets in FETCH_CONFIG.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': 'us,eu',
            'markets': markets,
            'bookmakers': BOOKMAKERS,
            'oddsFormat': 'decimal'
        }
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                cache[sport] = res.json()
                total_calls += 1
            else:
                print(f"Failed to fetch {sport}: {res.status_code}")
        except Exception as e:
            print(f"Error fetching {sport}: {e}")

    with open("master_odds_cache.json", "w") as f:
        json.dump(cache, f)
        
    print(f"✅ Master Odds Cache generated ({total_calls} API calls used).")

if __name__ == "__main__":
    run_fetcher()