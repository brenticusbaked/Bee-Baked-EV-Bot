import os
import json
import requests

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Removed Dabble and DFS from the Bookmakers string for the main game-line pull
BOOKMAKERS = (
    "fanduel,draftkings,betmgm,bet365,espn,fanatics,pinnacle,caesars,betrivers,"
    "bovada,betonline,bookmaker,lowvig,betus,mybookie,sportsbetting,novig"
)

FETCH_CONFIG = {
    "basketball_nba": "h2h,spreads,totals",
    "icehockey_nhl": "h2h,spreads,totals",
    "baseball_mlb": "h2h,spreads,totals,h2h_1st_5_innings", 
    "soccer_epl": "h2h,spreads,totals",
    "esports_csgo": "h2h",
    "tennis_atp_wimbledon": "h2h"
}

def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return

    # OPTIMIZATION: Dropped 'us_dfs' and 'au'. DFS apps don't offer game lines, 
    # so we stop paying the API multiplier to ask them for it.
    regions = "us,us_ex"
    cache = {}
    
    total_cost_this_run = 0
    credits_remaining = "Unknown"

    print(f"📥 Generating Optimized Master Odds Cache for {len(FETCH_CONFIG)} sports...")
    
    for sport, markets in FETCH_CONFIG.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': regions,
            'markets': markets,
            'bookmakers': BOOKMAKERS,
            'oddsFormat': 'decimal',
            'includeIds': 'true',
            'includeLinks': 'true' 
        }
        
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                cache[sport] = res.json()
                
                # Extract exact billing headers provided by The Odds API
                cost = int(res.headers.get('x-requests-used', 1))
                credits_remaining = res.headers.get('x-requests-remaining', "Unknown")
                total_cost_this_run += cost
                
                print(f"✅ Cached: {sport} (Cost: {cost})")
            else:
                print(f"⚠️ Failed to fetch {sport}: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            print(f"❌ Exception: {e}")

    with open("master_odds_cache.json", "w") as f:
        json.dump(cache, f)
        
    print(f"🚀 Master Cache Complete.")
    print(f"💰 Credits Burned This Run: {total_cost_this_run}")
    print(f"🏦 API Balance Remaining: {credits_remaining}")

if __name__ == "__main__":
    run_fetcher()