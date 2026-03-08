import os
import csv
import requests
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

# The three main sports we want to check for closing lines
SPORTS = ['baseball_mlb', 'basketball_nba', 'icehockey_nhl']

def get_current_pinnacle_odds():
    """Pulls the latest Pinnacle odds for all major sports to use as our Closing Line."""
    if not ODDS_API_KEY: 
        print("Missing ODDS_API_KEY")
        return {}
    
    all_odds = {}
    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY, 
            'regions': 'us,eu', 
            'markets': 'h2h', 
            'bookmakers': 'pinnacle', 
            'oddsFormat': 'american' # Pulling American odds natively
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                for game in res.json():
                    matchup = f"{game['away_team']} @ {game['home_team']}"
                    for bm in game.get('bookmakers', []):
                        if bm['key'] == 'pinnacle':
                            for mkt in bm['markets']:
                                if mkt['key'] == 'h2h':
                                    all_odds[matchup] = mkt['outcomes']
        except Exception as e:
            print(f"Error fetching {sport} for CLV: {e}")
            
    return all