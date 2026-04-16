import os
import json
import requests

# Uses environment variable for security on GitHub Actions
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Optimized configuration: Removed invalid markets and volatile esports
FETCH_CONFIG = {
    "basketball_nba": "h2h,spreads,totals",
    "icehockey_nhl": "h2h,spreads,totals",
    "baseball_mlb": "h2h,spreads,totals", 
    "soccer_epl": "h2h,spreads,totals",
    "tennis_atp_wimbledon": "h2h"
}

def run_fetcher():
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing. Make sure your environment variables are set.")
        return

    # COST SAVER: Strictly limits to US/Novig to prevent massive DFS/AU multipliers
    regions = "us,us_ex"
    cache = {}
    
    credits_remaining = "Unknown"
    start_usage = None
    end_usage = None

    print(f"📥 Generating Optimized Master Cache for {len(FETCH_CONFIG)} sports...")
    
    for sport, markets in FETCH_CONFIG.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': regions,
            'markets': markets,
            'oddsFormat': 'decimal',
            'includeIds': 'true',     # Required for mobile 'Add to Slip' functionality
            'includeLinks': 'true'    # Requests native bookmaker deep links directly
        }
        
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                cache[sport] = res.json()
                
                # Accurately track billing across the loop
                current_usage = int(res.headers.get('x-requests-used', 0))
                credits_remaining = res.headers.get('x-requests-remaining', "Unknown")
                
                if start_usage is None: 
                    start_usage = current_usage
                end_usage = current_usage
                
                print(f"✅ Cached: {sport}")
            else:
                print(f"⚠️ Failed to fetch {sport}: HTTP {res.status_code}")
        except Exception as e:
            print(f"❌ Exception fetching {sport}: {e}")

    # Calculate actual cost of this specific run rather than the monthly cumulative
    actual_cost = (end_usage - start_usage) if (end_usage and start_usage) else 0

    # Failsafe Save Method: Forces creation in the current working directory
    file_path = os.path.join(os.getcwd(), "master_odds_cache.json")
    
    try:
        # Using "w+" to force creation and read/write permissions
        with open(file_path, "w+", encoding="utf-8") as f:
            json.dump(cache, f, indent=4)
        print(f"🚀 Master Cache Saved Successfully at: {file_path}")
    except Exception as e:
        print(f"❌ Failed to save JSON file. Check OS write permissions. Error: {e}")
        
    print(f"💰 Actual Credits Burned This Run: {actual_cost}")
    print(f"🏦 API Balance Remaining: {credits_remaining}")

if __name__ == "__main__":
    # If testing locally, temporarily uncomment the line below and add your key
    # ODDS_API_KEY = "your_key_here"
    run_fetcher()