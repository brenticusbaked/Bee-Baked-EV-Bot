import os, requests, json, urllib.parse
from datetime import datetime, timedelta
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def get_dynamic_link(bookmaker, target_string):
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(' ', '').replace('sportsbook', '')
    links = {
        'draftkings': f'https://sportsbook.draftkings.com/search?q={query}',
        'fanduel': f'https://sportsbook.fanduel.com/navigation/search?q={query}',
        'betmgm': f'https://sports.betmgm.com/en/sports/search?q={query}',
        'bet365': f'https://www.bet365.com/#/search?q={query}',
        'caesars': f'https://sportsbook.caesars.com/us/ky/bet/search?q={query}',
        'betrivers': f'https://betrivers.com/?page=search&q={query}',
        'bovada': f'https://www.bovada.lv/sports?search={query}'
    }
    return links.get(book, f"https://www.google.com/search?q={bookmaker}+sportsbook")

def get_best_spread(target_team):
    from db_manager import get_master_cache
    
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty or failed to load.")
        return
        
    best_price, best_point, best_book, best_title, event_id = 0.0, "", "Unknown", "Unknown", ""
    for game in cache.get('basketball_nba', []):
        if target_team in game['home_team'] or target_team in game['away_team']:
            for bm in game.get('bookmakers', []):
                if bm['key'] == 'pinnacle': continue
                for mkt in bm.get('markets', []):
                    if mkt['key'] == 'spreads':
                        for outcome in mkt['outcomes']:
                            if target_team in outcome['name'] and float(outcome['price']) > best_price:
                                best_price, best_point, best_book, best_title, event_id = float(outcome['price']), outcome['point'], bm['key'], bm['title'], game['id']
                                        
    if best_price > 0:
        return best_title, f"+{int((best_price-1)*100)}" if best_price>=2 else str(int(-100/(best_price-1))), str(best_point), get_dynamic_link(best_book, target_team), event_id
    return None, None, None, None, None

def get_espn_schedule(date_str):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    try:
        res = requests.get(url, timeout=10)
        return res.json().get('events', []) if res.status_code == 200 else []
    except: return []

def run_nba_model():
    today_str = datetime.now().strftime("%Y%m%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    
    teams_yesterday = {}
    for game in get_espn_schedule(yesterday_str):
        for comp in game.get('competitions', [{}])[0].get('competitors', []):
            teams_yesterday[comp['team']['displayName']] = comp['homeAway']
            
    for game in get_espn_schedule(today_str):
        comp = game['competitions'][0]
        away = next(c for c in comp['competitors'] if c['homeAway'] == 'away')['team']['displayName']
        home = next(c for c in comp['competitors'] if c['homeAway'] == 'home')['team']['displayName']
        
        # Simple Fatigue Logic: Road Back-to-Back vs Rested Home Team
        if away in teams_yesterday and home not in teams_yesterday:
            book, odds, line, link, eid = get_best_spread(home)
            if book and not is_already_logged(f"{away} @ {home}", "MODEL_NBA_SPREAD", f"{home} {line}"):
                log_bet_to_db(f"{away} @ {home}", "MODEL_NBA_SPREAD", f"{home} {line}", odds, "FATIGUE", "1.0", "MODEL", "basketball_nba", eid)
                msg = f"🏀 **NBA FATIGUE ALERT**\nAdvantage: **{home}** vs {away} (Road B2B)\nOdds: [{book}]({link}) @ {odds}"
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 16734003}]})

if __name__ == "__main__": run_nba_model()