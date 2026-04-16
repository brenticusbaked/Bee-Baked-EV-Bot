import json
import os
import requests
import urllib.parse
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def to_american(dec):
    """Converts decimal odds to American format."""
    if dec >= 2.0: 
        return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_dynamic_link(bookmaker, target_string, selection_id=None, event_id=None, api_link=None):
    """
    Generates Mobile-First Deep Links.
    Prioritizes API links, explicit Add-to-Slip URLs, then native App Schemes.
    """
    # 1. Highest Priority: Direct link provided by the API
    if api_link:
        return api_link 

    book = bookmaker.lower().replace(' ', '').replace('sportsbook', '')
    query = urllib.parse.quote(target_string)
    
    # 2. Explicit "Add to Slip" links (requires Selection ID / Event ID)
    if selection_id:
        slip_links = {
            'draftkings': f'https://sportsbook.draftkings.com/add-to-slip/{selection_id}',
            'fanduel': f'https://sportsbook.fanduel.com/sports/event/{event_id}/selection/{selection_id}',
            'caesars': f'https://sportsbook.caesars.com/us/ky/bet/selection/{selection_id}',
            'betmgm': f'https://sports.betmgm.com/en/sports/event/{event_id}', 
            'espn': f'espnbet://bet/{selection_id}'
            # Novig uses the app scheme below
        }
        if book in slip_links:
             return slip_links[book]

    # 3. Fallback: Force the native app to open and search
    app_schemes = {
        'draftkings': f'draftkings://sportsbook/search?q={query}',
        'fanduel': f'fanduel://sportsbook/navigation/search?q={query}',
        'betmgm': f'betmgm://sportsbook/search?q={query}',
        'caesars': f'caesars://sportsbook/search?q={query}',
        'bet365': f'bet365://sportsbook/search?q={query}',
        'espn': f'espnbet://search?q={query}',
        'fanatics': f'fanatics://search?q={query}',
        'betrivers': f'betrivers://search?q={query}',
        'novig': f'novig://search?q={query}', # Native Novig app scheme
        'prizepicks': f'https://app.prizepicks.com/search/{query}'
    }
    
    # 4. Default to web search if not a known app
    return app_schemes.get(book, f"https://www.google.com/search?q={bookmaker}+{query}")

def scan_markets():
    """Main scanning logic comparing master cache against Pinnacle."""
    try:
        with open("master_odds_cache.json", "r") as f:
            cache = json.load(f)
    except Exception as e:
        print(f"Error loading cache: {e}")
        return

    alerts = []
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
                api_link = bm.get('link') # Capture direct link from API
                for mkt in bm['markets']:
                    mkt_key = mkt['key']
                    if mkt_key not in markets: 
                        markets[mkt_key] = {'sharp': {}, 'soft': []}
                    
                    if bm['key'] == 'pinnacle':
                        for outcome in mkt['outcomes']:
                            markets[mkt_key]['sharp'][outcome['name']] = float(outcome['price'])
                    elif bm['key'] in soft_books:
                        for outcome in mkt['outcomes']:
                            markets[mkt_key]['soft'].append({
                                'book': bm['title'], 
                                'book_key': bm['key'], 
                                'name': outcome['name'], 
                                'price': float(outcome['price']), 
                                'point': outcome.get('point', ''),
                                'id': outcome.get('id'), 
                                'api_link': api_link
                            })

            for m_type, data in markets.items():
                sharp = data['sharp']
                if not sharp: continue
                
                for s_bet in data['soft']:
                    if s_bet['name'] in sharp:
                        p_price = sharp[s_bet['name']]
                        # Require a minimum 3% edge
                        if s_bet['price'] > p_price * 1.03:
                            ev = (s_bet['price'] / p_price) - 1
                            selection = f"{s_bet['name']} {s_bet['point']}"
                            
                            if not is_already_logged(matchup, m_type, selection):
                                
                                # --- DYNAMIC UNIT SIZING (Quarter Kelly Criterion) ---
                                fractional_odds = s_bet['price'] - 1
                                calculated_units = (ev / fractional_odds) / 4 * 100
                                units = min(calculated_units, 5.0) # Cap at 5 Units
                                # ------------------------------------------------------
                                
                                # PASSING ALL 9 ARGUMENTS TO DB
                                log_bet_to_db(
                                    matchup, m_type, selection, to_american(s_bet['price']), 
                                    ev, f"{units:.2f}", to_american(p_price), sport, event['id']
                                )
                                link = get_dynamic_link(s_bet['book_key'], s_bet['name'], s_bet['id'], event['id'], s_bet['api_link'])
                                alerts.append(f"🟢 **+EV {m_type.upper()}**\n**Match:** {matchup}\n**Bet:** {selection}\n**Book:** [{s_bet['book']}]({link}) @ {to_american(s_bet['price'])}\n**Edge:** {ev*100:.2f}%\n**Suggested:** {units:.2f} Units")

    if alerts and DISCORD_WEBHOOK_URL:
        for a in alerts: 
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": a, "color": 3066993}]})
        print(f"Sent {len(alerts)} alerts.")

if __name__ == "__main__": 
    scan_markets()