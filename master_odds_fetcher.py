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
            res = requests.get(url, params=params, timeout=