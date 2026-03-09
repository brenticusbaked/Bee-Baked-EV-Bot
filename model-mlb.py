import os
import requests
import csv
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, edge_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle', 'Result'])
        writer.writerow([datetime.now().strftime("%Y-%m-%d"), matchup, market, selection, odds, f"{edge_val:.2f}%", units, fair_price, "", ""])

def get_best_f5_moneyline(target_team):
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'h2h_1st_half', 'oddsFormat': 'decimal'}
    res = requests.get(url, params=params)
    if res.status_code == 200:
        for game in res.json():
            if target_team in game['home_team'] or target_team in game['away_team']:
                for bm in game.get('bookmakers', []):
                    for mkt in bm.get('markets', []):
                        for outcome in mkt['outcomes']:
                            if target_team in outcome['name']:
                                return bm['title'], to_american(float(outcome['price']))
    return None, None

def get_pitcher_stats(pitcher_id):
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}?hydrate=stats(group=[pitching],type=[season])"
    res = requests.get(url)
    if res.status_code == 200:
        person = res.json().get('people', [{}])[0]
        splits = person.get('stats', [{}])[0].get('splits', [{}])
        if splits:
            stats = splits[0].get('stat', {})
            return float(stats.get('era', 9.99)), float(stats.get('whip', 2.0))
    return None, None

def run_mlb_model():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"
    res = requests.get(url)
    if res.status_code != 200: return
    
    data = res.json()
    dates = data.get('dates', [])
    
    # Safely handle days with no MLB games scheduled
    if not dates:
        print(f"No MLB games scheduled for {today}.")
        if DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "⚾ **MLB Model Run:** No games scheduled today.", "color": 3066993}]})
        return
        
    for game in dates[0].get('games', []):
        away_p = game['teams']['away'].get('probablePitcher')
        home_p = game['teams']['home'].get('probablePitcher')
        
        if away_p and home_p:
            a_era, a_whip = get_pitcher_stats(away_p['id'])
            h_era, h_whip = get_pitcher_stats(home_p['id'])
            
            if a_era and h_era and abs(a_era - h_era) >= 1.50:
                better_team = game['teams']['away']['team']['name'] if a_era < h_era else game['teams']['home']['team']['name']
                book, odds = get_best_f5_moneyline(better_team)
                if book:
                    log_bet_to_csv(f"{game['teams']['away']['team']['name']} @ {game['teams']['home']['team']['name']}", "MODEL_MLB_F5", better_team, odds, abs(a_era - h_era), "1.00", "MODEL")
                    msg = f"⚾ **MLB MODEL MISMATCH** ⚾\n**Advantage:** {better_team}\n**Odds:** {book} @ {odds}"
                    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 3066993}]})

if __name__ == "__main__":
    run_mlb_model()