import os
import json
import requests

# Retrieve the API Key from GitHub Repository Secrets
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Comprehensive list of sportsbooks including KY-regulated, Offshore, DFS, and Exchanges
# Includes PrizePicks, Novig, Dabble, DraftKings Pick6, and Courtside (via search)
BOOKMAKERS = (
    "fanduel,draftkings,betmgm,bet365,espn,fanatics,pinnacle,caesars,betrivers,"
    "bovada,betonline,bookmaker,lowvig,betus,mybookie,sportsbetting,"
    "prizepicks,pick6,novig,dabble_au"
)

# Configuration for sports and their respective markets to fetch
FETCH_CONFIG = {
    "basketball_nba": "h2h,spreads,totals",
    "icehockey_nhl": "h2h,spreads,totals",
    "baseball_mlb": "h2h,spreads,totals,h2h_1st_half",
    "soccer_epl": "h2h,spreads,totals",
    "esports_counterstrike": "h2h",
    "tennis_atp_wimbledon": "h2h"
}

def run_fetcher():
    """
    Fetches real-time odds data across multiple regions (US, US DFS, US Exchanges, AU)
    and saves it to a local JSON cache to minimize API credit consumption.
    """
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing from environment variables.")
        return

    # To capture PrizePicks (DFS), Novig (Exchanges), and Dabble (AU) we pull all relevant regions
    regions = "us,us_dfs,us_ex,au"
    cache = {}
    total_calls = 0

    print(f"📥 Generating Master Odds Cache for {len(FETCH_CONFIG)} sports across regions: {regions}...")
    
    for sport, markets in FETCH_CONFIG.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': regions,
            'markets': markets,
            'bookmakers': BOOKMAKERS,
            'oddsFormat': 'decimal'
        }
        
        try:
            # The Odds API charges 1 credit per sport/market set regardless of the number of bookmakers
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                cache[sport] = res.json()
                total_calls += 1
                print(f"✅ Cached: {sport}")
            else:
                print(f"⚠️ Failed to fetch {sport}: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            print(f"❌ Exception occurred while fetching {sport}: {e}")

    # Save the combined data to an ephemeral file for other models to read locally
    with open("master_odds_cache.json", "w") as f:
        json.dump(cache, f)
        
    print(f"🚀 Master Cache Complete. Credits used: {total_calls}.")

if __name__ == "__main__":
    run_fetcher()