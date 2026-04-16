import os
import requests
from db_manager import is_already_logged, log_bet_to_db, get_master_cache

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def scan_markets():
    # FIXED: Pulls direct from Supabase cloud cache
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return

    alerts = []
    soft_books = ['fanduel', 'draftkings', 'betmgm', 'bet365', 'caesars', 'prizepicks']

    for sport, events in cache.items():
        for event in events:
            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}
            
            for bm in event['bookmakers']:
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
                                'book': bm['title'], 'name': outcome['name'], 
                                'price': float(outcome['price']), 'point': outcome.get('point', '')
                            })

            for m_type, data in markets.items():
                sharp = data['sharp']
                if not sharp: continue # Skips if Pinnacle baseline is missing
                
                for s_bet in data['soft']:
                    if s_bet['name'] in sharp:
                        p_price = sharp[s_bet['name']]
                        if s_bet['price'] > p_price * 1.03: # 3%+ Edge threshold
                            ev = (s_bet['price'] / p_price) - 1
                            selection = f"{s_bet['name']} {s_bet['point']}".strip()
                            
                            if not is_already_logged(matchup, m_type, selection):
                                units = min((ev / (s_bet['price'] - 1)) / 4 * 100, 5.0) 
                                log_bet_to_db(matchup, m_type, selection, to_american(s_bet['price']), ev, f"{units:.2f}", to_american(p_price), sport, event['id'])
                                alerts.append(f"🟢 **+EV {m_type.upper()}**\n**Match:** {matchup}\n**Bet:** {selection}\n**Book:** {s_bet['book']} @ {to_american(s_bet['price'])}\n**Edge:** {ev*100:.2f}%\n**Suggested:** {units:.2f} Units")

    if alerts and DISCORD_WEBHOOK_URL:
        for a in alerts: requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": a, "color": 3066993}]})

if __name__ == "__main__": scan_markets()