import os
import requests
import json
import urllib.parse
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
    try:
        with open("master_odds_cache.json", "r") as f: cache = json.load(f)
    except: return None, None, None, None
        
    best_price, best_point, best_book, best_title = 0.0, "", "Unknown", "Unknown"
    for game in cache.get('basketball_nba', []):
        if target_team in game['home_team'] or target_team in game['away_team']:
            for bm in game.get('bookmakers', []):
                if bm['key'] == 'pinnacle': continue
                for mkt in bm.get('markets', []):
                    if mkt['key'] == 'spreads':
                        for outcome in mkt['outcomes']:
                            if target_team in outcome['name']:
                                if float(outcome['price']) > best_price:
                                    best_price, best_point, best_book, best_title = float(outcome['price']), outcome['point'], bm['key'], bm['title']
                                        
    if best_price > 0:
        return best_title, f"+{int((best_price-1)*100)}" if best_price>=2 else str(int(-100/(best_price-1))), str(best_point), get_dynamic_link(best_book, target_team)
    return None, None, None, None

# ... (rest of NBA fatigue logic remains the same) ...