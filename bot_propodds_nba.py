import os
import requests
import urllib.parse
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

TARGET_STATS = ['points', 'assists']

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def to_decimal(price):
    try:
        p = float(price)
        if p > 100: return (p / 100) + 1
        if p < -100: return (100 / abs(p)) + 1
        return p
    except:
        return 1.909

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

def get_sgo_edges():
    if not SGO_API_KEY: return []

    picks = []
    url = "https://api.sportsgameodds.com/v2/events"
    params = {'apiKey': SGO_API_KEY, 'leagueID': 'NBA', 'oddsAvailable': 'true'}

    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code != 200: return []
        
        data = res.json()
        for event in data:
            matchup = event.get('name', 'Unknown Matchup')
            market_groups = {}
            
            for odd_key, odd_obj in event.get('odds', {}).items():
                odd_id = odd_obj.get('oddID', odd_key)
                parts = odd_id.split('-')
                
                if len(parts) < 5 or parts[0] not in TARGET_STATS: continue
                
                stat_type, player_raw, side = parts[0], parts[1], parts[4]
                bookmaker, price_raw, line = odd_obj.get('bookmakerID', 'unknown'), odd_obj.get('price'), odd_obj.get('handicap')
                
                if price_raw is None or line is None: continue
                
                price = to_decimal(price_raw)
                player = player_raw.split('_1_')[0].replace('_', ' ').title()
                uid = f"{player}_{stat_type}_{line}"
                
                if uid not in market_groups: market_groups[uid] = {'sharp': {}, 'soft': {}}
                
                if bookmaker == 'pinnacle':
                    market_groups[uid]['sharp'][side] = price
                elif bookmaker in ['fanduel', 'draftkings', 'betmgm', 'espn', 'fanatics', 'bet365']:
                    if price > market_groups[uid]['soft'].get(side, {}).get('price', 0):
                        market_groups[uid]['soft'][side] = {'price': price, 'book': bookmaker, 'line': line}

            for uid, val in market_groups.items():
                sharp, soft = val['sharp'], val['soft']
                if 'over' in sharp and 'under' in sharp:
                    p_over, p_under = sharp['over'], sharp['under']
                    vig = (1/p_over) + (1/p_under)
                    probs = {'over': (1/p_over)/vig, 'under': (1/p_under)/vig}
                    
                    for side in ['over', 'under']:
                        if side in soft:
                            s_price = soft[side]['price']
                            ev = (s_price * probs[side]) - 1
                            
                            if ev > 0.02:
                                p_name, s_name, l_val = uid.split('_')
                                market = s_name.upper()
                                selection = f"{p_name} {side.upper()} {l_val}"
                                
                                if not is_already_logged(matchup, market, selection):
                                    units = min((ev / (s_price - 1)) / 4 * 100, 5.0)
                                    is_em = ev >= 0.06
                                    log_bet_to_db(matchup.strip(), market.strip(), selection.strip(), to_american(s_price), ev, f"{units:.2f}", to_american(1/probs[side]))
                                    
                                    deep_link = get_dynamic_link(soft[side]['book'], p_name)
                                    header = "🚨 **SGO PROP EMERGENCY** 🚨" if is_em else "🏀 **NBA PROP ALERT** 🏀"
                                    
                                    picks.append({
                                        "msg": f"{header}\n**Edge:** {ev*100:.2f}%\n**Match:** {matchup}\n**Market:** {market} | {selection}\n**Book:** [{soft[side]['book'].upper()}]({deep_link}) @ {to_american(s_price)}\n**Suggested:** {units:.2f} Units",
                                        "color": 15158332 if is_em else 3447003,
                                        "is_emergency": is_em
                                    })
    except Exception as e: print(f"Error fetching SGO prop edges: {e}")
    return picks

def main():
    picks = get_sgo_edges()
    if not DISCORD_WEBHOOK_URL: return
    
    if picks:
        for p in picks:
            requests.post(DISCORD_WEBHOOK_URL, json={
                "content": "@everyone" if p["is_emergency"] else "", 
                "embeds": [{"description": p["msg"], "color": p["color"], "image": {"url": FOOTER_IMG}}]
            })
        print(f"Sent {len(picks)} NBA Prop alerts.")
    else: print("SGO Scan Complete: No new NBA player prop edges found.")

if __name__ == "__main__": main()