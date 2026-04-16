import os
import requests
import json
import urllib.parse
from datetime import datetime
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_dynamic_link(bookmaker, target_string):
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(' ', '')
    links = {
        'draftkings': f'https://sportsbook.draftkings.com/search?q={query}',
        'fanduel': f'https://sportsbook.fanduel.com/navigation/search?q={query}',
        'betmgm': f'https://sports.betmgm.com/en/sports/search?q={query}',
        'bet365': f'https://www.bet365.com/#/search?q={query}',
        'espn': f'https://espnbet.com/search?q={query}',
        'fanatics': f'https://sportsbook.fanatics.com/search?q={query}'
    }
    return links.get(book, f"https://www.google.com/search?q={bookmaker}+sportsbook")

def get_best_puckline(target_team):
    try:
        with open("master_odds_cache.json", "r") as f:
            cache = json.load(f)
    except FileNotFoundError:
        return None, None, None
        
    best_price = 0.0
    best_book = "Unknown"
    best_book_title = "Unknown"
    
    for game in cache.get('icehockey_nhl', []):
        if target_team in game['home_team'] or target_team in game['away_team']:
            for bm in game.get('bookmakers', []):
                if bm['key'] == 'pinnacle': continue
                for mkt in bm.get('markets', []):
                    if mkt['key'] == 'spreads':
                        for outcome in mkt['outcomes']:
                            if target_team in outcome['name'] and outcome.get('point') == -1.5:
                                price = float(outcome['price'])
                                if price > best_price:
                                    best_price = price
                                    best_book = bm['key']
                                    best_book_title = bm['title']
                                    
    if best_price > 0:
        link = get_dynamic_link(best_book, target_team)
        return best_book_title, to_american(best_price), link
    return None, None, None

def run_nhl_model():
    try:
        standings_res = requests.get("https://api-web.nhle.com/v1/standings/now", timeout=10)
        team_gd = {team['teamName']['default']: team['goalDifferential'] for team in standings_res.json().get('standings', [])}
            
        today = datetime.now().strftime("%Y-%m-%d")
        sched_res = requests.get(f"https://api-web.nhle.com/v1/schedule/{today}", timeout=10)
        sched_data = sched_res.json()
        
        alerts = []
        if 'gameWeek' in sched_data and len(sched_data['gameWeek']) > 0:
            for game in sched_data['gameWeek'][0].get('games', []):
                away = game['awayTeam']['placeName']['default']
                home = game['homeTeam']['placeName']['default']
                matchup = f"{away} @ {home}"
                
                away_gd = next((gd for name, gd in team_gd.items() if away in name), None)
                home_gd = next((gd for name, gd in team_gd.items() if home in name), None)
                
                if away_gd is not None and home_gd is not None:
                    gd_diff = abs(away_gd - home_gd)
                    
                    if gd_diff >= 40:
                        better_team = away if away_gd > home_gd else home
                        better_gd = max(away_gd, home_gd)
                        worse_team = home if away_gd > home_gd else away
                        worse_gd = min(away_gd, home_gd)
                        
                        market = "MODEL_NHL_PUCKLINE"
                        selection = f"{better_team} -1.5"

                        if not is_already_logged(matchup, market, selection):
                            best_book, best_odds, bet_link = get_best_puckline(better_team)
                            
                            if best_book:
                                odds_text = f"\n💰 **Best Puck Line:** [{best_book}]({bet_link}) | **-1.5 ({best_odds})**"
                                log_bet_to_db(matchup.strip(), market.strip(), selection.strip(), best_odds, gd_diff, "1.00", "MODEL", "icehockey_nhl", "NHL_MODEL_NO_ID")

                                alerts.append(
                                    f"🏒 **NHL MODEL MISMATCH DETECTED** 🏒\n"
                                    f"**Game:** {matchup}\n━━━━━━━━━━━━━━━━━━━━\n"
                                    f"**Advantage:** {better_team}\n"
                                    f"✅ {better_team} GD: **{better_gd}**\n"
                                    f"❌ {worse_team} GD: **{worse_gd}**\n"
                                    f"**Net Gap:** {gd_diff} Goals\n{odds_text}"
                                )
                        
        if DISCORD_WEBHOOK_URL:
            if alerts:
                for msg in alerts: 
                    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 3447003, "image": {"url": FOOTER_IMG}}]})
                print(f"Sent {len(alerts)} NHL mismatch alerts.")
            else:
                print("NHL Model Run Complete: No major Goal Differential mismatches today.")
                
    except Exception as e:
        print(f"Error running NHL model: {e}")

if __name__ == "__main__":
    run_nhl_model()