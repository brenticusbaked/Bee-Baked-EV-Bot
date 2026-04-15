import os
import requests
from datetime import datetime
from db_manager import is_already_logged, log_bet_to_db

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_best_f5_moneyline(target_team):
    """Pulls First 5 Innings (F5) Moneyline for the target team."""
    if not ODDS_API_KEY: return None, None
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'h2h_1st_half', 'oddsFormat': 'decimal'}
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            for game in res.json():
                if target_team in game['home_team'] or target_team in game['away_team']:
                    for bm in game.get('bookmakers', []):
                        for mkt in bm.get('markets', []):
                            for outcome in mkt['outcomes']:
                                if target_team in outcome['name']:
                                    return bm['title'], to_american(float(outcome['price']))
    except Exception as e:
        print(f"Error fetching MLB F5 Odds: {e}")
    return None, None

def get_advanced_pitcher_stats(pitcher_id):
    """Calculates Estimated FIP to find true pitcher value vs public ERA."""
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}?hydrate=stats(group=[pitching],type=[season])"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            person = res.json().get('people', [{}])[0]
            splits = person.get('stats', [{}])[0].get('splits', [{}])
            if splits:
                stats = splits[0].get('stat', {})
                
                # Extract raw metrics per 9 innings
                k9 = float(stats.get('strikeOutsPer9Inn', 0))
                bb9 = float(stats.get('walksPer9Inn', 0))
                hr9 = float(stats.get('homeRunsPer9', 0))
                era = float(stats.get('era', 9.99))
                
                # Basic FIP calculation: ((13*HR + 3*BB - 2*K) / 9) + Constant (~3.20)
                est_fip = ((13 * hr9) + (3 * bb9) - (2 * k9)) / 9 + 3.20
                
                return est_fip, era
    except Exception as e:
        print(f"Error fetching stats for Pitcher ID {pitcher_id}: {e}")
    return None, None

def run_mlb_model():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"
    
    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200: return
        
        data = res.json()
        dates = data.get('dates', [])
        
        if not dates:
            print(f"No MLB games scheduled for {today}.")
            return
            
        alerts = []
        for game in dates[0].get('games', []):
            away_team = game['teams']['away']['team']['name']
            home_team = game['teams']['home']['team']['name']
            matchup = f"{away_team} @ {home_team}"
            
            away_p = game['teams']['away'].get('probablePitcher')
            home_p = game['teams']['home'].get('probablePitcher')
            
            if away_p and home_p:
                a_fip, a_era = get_advanced_pitcher_stats(away_p['id'])
                h_fip, h_era = get_advanced_pitcher_stats(home_p['id'])
                
                if a_fip and h_fip:
                    fip_diff = abs(a_f