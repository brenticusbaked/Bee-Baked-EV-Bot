import json
import os
import requests
import urllib.parse
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_dynamic_link(bookmaker, target_string):
    """Generates a deep link directly to the sportsbook's search page for the team/player."""
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(' ', '').replace('sportsbook', '')
    
    links = {
        # State-Regulated (KY Licensed)
        'draftkings': f'https://sportsbook.draftkings.com/search?q={query}',
        'fanduel': f'https://sportsbook.fanduel.com/navigation/search?q={query}',
        'betmgm': f'https://sports.betmgm.com/en/sports/search?q={query}',
        'bet365': f'https://www.bet365.com/#/search?q={query}',
        'espn': f'https://espnbet.com/search?q={query}',
        'fanatics': f'https://sportsbook.fanatics.com/search?q={query}',
        'caesars': f'https://sportsbook.caesars.com/us/ky/bet/search?q={query}',
        'betrivers': f'https://betrivers.com/?page=search&q={query}',
        'circasports': 'https://kentucky.circasports.com/',
        'primesports': 'https://primesports.com/',
        'betr': 'https://www.betr.app/',
        # Offshore & International
        'bovada': f'https://www.bovada.lv/sports?search={query}',
        'betonline': 'https://www.betonline.ag/sportsbook',
        'bookmaker': 'https://www.bookmaker.eu/sportsbook',
        'betus': f'https://www.betus.com.pa/sportsbook/search/?q={query}',
        'mybookie': 'https://mybookie.ag/sportsbook/',
        'heritagesports': 'https://www.heritagesports.eu/',
        'sportsbetting': 'https://www.sportsbetting.ag/sportsbook',
        # DFS & Exchanges
        'prizepicks': f'https://app.prizepicks.com/search/{query}',
        'pick6': f'https://sportsbook.draftkings.com/pick6/search?q={query}',
        'novig': f'https://novig.co/market-search?q={query}',
        'dabble': f'https://dabble.com.au/search?q={query}',
        'courtside': 'https://courtside.bet/'
    }
    return links.get(book, f"https://www.google.com/search?q={bookmaker}+sportsbook")

def scan_markets():
    try:
        with open("master_odds_cache.json", "r") as f:
            cache = json.load(f)
    except: return

    alerts = []
    # Targeted soft books including new DFS and Exchange entries
    soft_books = [
        'fanduel', 'draftkings', 'betmgm', 'bet365', 'espn', 'fanatics', 
        'caesars', 'betrivers', 'bovada', 'betonline', 'betus', 'mybookie', 
        'prizepicks', 'pick6', 'novig', 'dabble'
    ]

    for sport, events in cache.items():
        for event in events:
            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}
            
            for bm in event['bookmakers']:
                for mkt in bm['markets']:
                    mkt_key = mkt['key']
                    if mkt_key not in markets: markets[mkt_key] = {'sharp': {}, 'soft': []}
                    
                    if bm['key'] == 'pinnacle':
                        for outcome in mkt['outcomes']:
                            markets[mkt_key]['sharp'][outcome['name']] = float(outcome['price'])
                    elif bm['key'] in soft_books:
                        for outcome in mkt['outcomes']:
                            markets[mkt_key]['soft'].append({
                                'book': bm['title'], 'name': outcome['name'], 
                                'price': float(outcome['price']), 'point': outcome.get('point', '')
                            })

            for m_type, data in markets.items():
                sharp = data['sharp']
                if not sharp: continue
                
                for s_bet in data['soft']:
                    if s_bet['name'] in sharp:
                        p_price = sharp[s_bet['name']]
                        # Simplified EV calc vs Pinnacle
                        if s_bet['price'] > p_price * 1.03:
                            ev = (s_bet['price'] / p_price) - 1
                            selection = f"{s_bet['name']} {s_bet['point']}"
                            if not is_already_logged(matchup, m_type, selection):
                                log_bet_to_db(matchup, m_type, selection, to_american(s_bet['price']), ev, "1.00", "SCANNER")
                                link = get_dynamic_link(s_bet['book'], s_bet['name'])
                                alerts.append(f"🟢 **+EV {m_type.upper()}**\n**Match:** {matchup}\n**Bet:** {selection}\n**Book:** [{s_bet['book']}]({link}) @ {to_american(s_bet['price'])}\n**Edge:** {ev*100:.2f}%")

    if DISCORD_WEBHOOK_URL and alerts:
        for a in alerts: requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": a, "color": 3066993}]})

if __name__ == "__main__": scan_markets()