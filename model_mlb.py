import os
import requests
import json
from datetime import datetime
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_best_f5_moneyline(target_team):
    try:
        with open("master_odds_cache.json", "r") as f:
            cache = json.load(f)
    except FileNotFoundError:
        return None, None
        
    best_price = 0.0
    best_book = "Unknown"
    best_book_title = "Unknown"
    
    for game in cache.get('baseball_mlb', []):
        if target_team in game['home_team'] or target_team in game['away_team']:
            for bm in game.get('bookmakers', []):
                if bm['key'] == 'pinnacle': continue
                for mkt in bm.get('markets', []):
                    if mkt['key'] == 'h2h_1st_half':
                        for outcome in mkt['outcomes']:
                            if target_team in outcome['name']:
                                price = float(outcome['price'])
                                if price > best_price:
                                    best_price = price
                                    best_book = bm['key']
                                    best_book_title = bm['title']
                                    
    if best_price > 0:
        return best_book_title, to_american(best_price)
    return None, None

def get_advanced_pitcher_stats(pitcher_id):
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}?hydrate=stats(group=[pitching],type=[season])"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            person = res.json().get('people', [{}])[0]
            splits = person.get('stats', [{}])[0].get('splits', [{}])
            if splits:
                stats = splits[0].get('stat', {})
                k9 = float(stats.get('strikeOutsPer9Inn', 0))
                bb9 = float(stats.get('walksPer9Inn', 0))
                hr9 = float(stats.get('homeRunsPer9', 0))
                era = float(stats.get('era', 9.99))
                
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
                    
                    if fip_diff >= 1.25:
                        better_team = away_team if a_fip < h_fip else home_team
                        market = "MODEL_MLB_F5"
                        selection = better_team
                        
                        if not is_already_logged(matchup, market, selection):
                            book, odds = get_best_f5_moneyline(better_team)
                            
                            if book:
                                log_bet_to_db(matchup, market, selection, odds, fip_diff, "1.00", "MODEL")
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