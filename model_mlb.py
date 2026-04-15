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

def is_already_logged(matchup, market, selection):
    """Prevents the bot from spamming the same MLB mismatch all day."""
    if not os.path.exists('bets_log.csv'): return False
    today = datetime.now().strftime("%Y-%m-%d")
    with open('bets_log.csv', 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or not any(row): continue
            if len(row) > 3 and row[0] == today:
                if row[1] == matchup and row[2].upper() == market.upper() and row[3] == selection:
                    return True
    return False

def log_bet_to_csv(matchup, market, selection, odds, edge_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle', 'Result'])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{edge_val:.2f}%", units, fair_price, "", ""
        ])

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
                # This reveals pitchers who are getting unlucky with defense
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
                    fip_diff = abs(a_fip - h_fip)
                    
                    # Look for a significant mismatch in fielding independent pitching (>= 1.25 gap)
                    if fip_diff >= 1.25:
                        better_team = away_team if a_fip < h_fip else home_team
                        market = "MODEL_MLB_F5"
                        selection = better_team
                        
                        # Anti-Spam Check
                        if not is_already_logged(matchup, market, selection):
                            book, odds = get_best_f5_moneyline(better_team)
                            
                            if book:
                                log_bet_to_csv(matchup, market, selection, odds, fip_diff, "1.00", "MODEL")
                                
                                alerts.append(
                                    f"⚾ **MLB ADVANCED METRIC MISMATCH** ⚾\n"
                                    f"**Game:** {matchup}\n━━━━━━━━━━━━━━━━━━━━\n"
                                    f"**Advantage:** {better_team} (First 5 Innings)\n"
                                    f"📊 {away_p['fullName']} FIP: **{a_fip:.2f}** (ERA: {a_era:.2f})\n"
                                    f"📊 {home_p['fullName']} FIP: **{h_fip:.2f}** (ERA: {h_era:.2f})\n"
                                    f"💰 **Best F5 ML:** {book} @ {odds}"
                                )

        if DISCORD_WEBHOOK_URL and alerts:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 3066993, "image": {"url": FOOTER_IMG}}]})
            print(f"Sent {len(alerts)} MLB F5 mismatch alerts.")
        else:
            print("MLB Model Run Complete: No new starting pitcher edges found.")
            
    except Exception as e:
        print(f"Error running MLB model: {e}")

if __name__ == "__main__":
    run_mlb_model()