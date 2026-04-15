import os
import json
import requests

# Retrieve the API Key from GitHub Repository Secrets
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Comprehensive list of sportsbooks available in Kentucky + Offshore/DFS
# Capture regulated books (Caesars, BetRivers), Offshore (Bovada, BetOnline), 
# DFS (PrizePicks, Pick6), and Exchanges (Novig)
BOOKMAKERS = (
    "fanduel,draftkings,betmgm,bet365,espn,fanatics,pinnacle,caesars,betrivers,"
    "bovada,betonline,bookmaker,lowvig,betus,mybookie,sportsbetting,"
    "prizepicks,pick6,novig,dabble_au"
)

# Configuration for sports and markets
# FIXED: MLB uses 'h2h_1st_5_innings' and Counter-Strike uses 'esports_csgo'
FETCH_CONFIG = {
    "basketball_nba": "h2h,spreads,totals",
    "icehockey_nhl": "h2h,spreads,totals",
    "baseball_mlb": "h2h,spreads,totals,h2h_1st_5_innings", 
    "soccer_epl": "h2h,spreads,totals",
    "esports_csgo": "h2h",
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

    # Pulls all relevant regions in a single sweep
    regions = "us,us_dfs,us_ex,au"
    cache = {}
    total_calls = 0

    print(f"📥 Generating Master Odds Cache for {len(FETCH_CONFIG)} sports...")
    
    for sport, markets in FETCH_CONFIG.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': regions,
            'markets': markets,
            'bookmakers': BOOKMAKERS,
            'oddsFormat': 'decimal',
            'includeIds': 'true' # REQUIRED for "Add to Slip" mobile links
        }
        
        try:
            # Each successful request costs 1 credit per sport/market set
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                cache[sport] = res.json()
                total_calls += 1
                print(f"✅ Cached: {sport}")
            else:
                print(f"⚠️ Failed to fetch {sport}: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            print(f"❌ Exception occurred while fetching {sport}: {e}")

    # Save the combined data to a local file for scanners to read
    with open("master_odds_cache.json", "w") as f:
        json.dump(cache, f)
        
    print(f"🚀 Master Cache Complete. Credits used: {total_calls}.")

if __name__ == "__main__":
    run_fetcher()