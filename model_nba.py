import os
import requests
import json
from datetime import datetime, timedelta
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

BOOK_LINKS = {
    'draftkings': 'https://sportsbook.draftkings.com/',
    'fanduel': 'https://sportsbook.fanduel.com/',
    'betmgm': 'https://sports.betmgm.com/',
    'bet365': 'https://www.bet365.com/',
    'espn': 'https://espnbet.com/',
    'fanatics': 'https://sportsbook.fanatics.com/',
}

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_best_spread(target_team):
    try:
        with open("master_odds_cache.json", "r") as f:
            cache = json.load(f)
    except FileNotFoundError:
        return None, None, None, None
        
    best_price, best_point, best_book, best_book_title = 0.0, "", "Unknown", "Unknown"
    for game in cache.get('basketball_nba', []):
        if target_team in game['home_team'] or target_team in game['away_team']:
            for bm in game.get('bookmakers', []):
                if bm['key'] == 'pinnacle': continue
                for mkt in bm.get('markets', []):
                    if mkt['key'] == 'spreads':
                        for outcome in mkt['outcomes']:
                            if target_team in outcome['name']:
                                price = float(outcome['price'])
                                if 1.85 <= price <= 1.96 and price > best_price:
                                    best_price = price
                                    best_point = outcome.get('point', '')
                                    best_book = bm['key']
                                    best_book_title = bm['title']
                                        
    if best_price > 0:
        link = BOOK_LINKS.get(best_book, f"https://www.google.com/search?q={best_book}+nba+odds")
        return best_book_title, to_american(best_price), f"{best_point}", link
    return None, None, None, None

def get_espn_schedule(date_str):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json().get('events', [])
    except Exception as e:
        print(f"Error fetching ESPN data: {e}")
    return []

def run_nba_model():
    today_obj = datetime.now()
    yesterday_obj = today_obj - timedelta(days=1)
    
    teams_yesterday = {}
    for game in get_espn_schedule(yesterday_obj.strftime("%Y%m%d")):
        if game.get('competitions'):
            for comp in game['competitions'][0].get('competitors', []):
                teams_yesterday[comp['team']['displayName']] = comp['homeAway']
            
    alerts = []
    for game in get_espn_schedule(today_obj.strftime("%Y%m%d")):
        if not game.get('competitions'): continue
            
        comp = game['competitions'][0]
        away_team = next(c for c in comp['competitors'] if c['homeAway'] == 'away')['team']['displayName']
        home_team = next(c for c in comp['competitors'] if c['homeAway'] == 'home')['team']['displayName']
        matchup = f"{away_team} @ {home_team}"
        
        away_fatigue, home_fatigue = 0, 0
        if away_team in teams_yesterday:
            away_fatigue = 4 if teams_yesterday[away_team] == 'away' else 3
        if home_team in teams_yesterday:
            home_fatigue = 2 if teams_yesterday[home_team] == 'away' else 1
            
        fatigue_diff = away_fatigue - home_fatigue
        target_team, reason, edge = None, "", 0.0
        
        if fatigue_diff >= 3:
            target_team = home_team
            reason = f"{away_team} is on a brutal Road Back-to-Back. {home_team} is rested."
            edge = float(fatigue_diff) 
        elif fatigue_diff <= -3:
            target_team = away_team
            reason = f"{home_team} is on a Home Back-to-Back after traveling. {away_team} is rested."
            edge = float(abs(fatigue_diff))

        if target_team:
            best_book, best_odds, spread_line, bet_link = get_best_spread(target_team)
            market = "MODEL_NBA_SPREAD"
            selection = f"{target_team} {spread_line}"
            
            if best_book and not is_already_logged(matchup, market, selection):
                log_bet_to_db(matchup, market, selection, best_odds, edge, "1.00", "MODEL")
                odds_text = f"\n💰 **Best Odds:** {best_book} | **{spread_line} ({best_odds})**\n🔗 [Click here to bet]({bet_link})"
                alerts.append(
                    f"🏀 **NBA TRAVEL FATIGUE DETECTED** 🏀\n"
                    f"**Game:** {matchup}\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"**Advantage:** {target_team}\n"
                    f"📉 **The Edge:** {reason}\n{odds_text}"
                )
                        
    if DISCORD_WEBHOOK_URL:
        if alerts:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 16734003, "image": {"url": FOOTER_IMG}}]})
            print(f"Sent {len(alerts)} NBA Fatigue mismatch alerts.")
        else:
            print("NBA Model Run Complete: No extreme schedule mismatches today.")

if __name__ == "__main__":
    run_nba_model()