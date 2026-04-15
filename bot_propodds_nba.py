import os
import requests
import urllib.parse
from db_manager import is_already_logged, log_bet_to_db

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

TARGET_STATS = ['points', 'assists', 'rebounds']

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def to_decimal(price):
    try:
        p = float(price)
        if p > 100: return (p / 100) + 1
        if p < -100: return (100 / abs(p)) + 1
        return p
    except: return 1.909

def get_dynamic_link(bookmaker, target_string):
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(' ', '').replace('sportsbook', '')
    app_schemes = {
        'draftkings': f'draftkings://sportsbook/search?q={query}',
        'fanduel': f'fanduel://sportsbook/navigation/search?q={query}',
        'betmgm': f'betmgm://sportsbook/search?q={query}',
        'caesars': f'caesars://sportsbook/search?q={query}',
        'prizepicks': f'https://app.prizepicks.com/search/{query}'
    }
    return app_schemes.get(book, f"https://www.google.com/search?q={bookmaker}+{query}")

def get_sgo_edges():
    if not SGO_API_KEY: return []
    soft_list = ['fanduel', 'draftkings', 'betmgm', 'espn', 'fanatics', 'bet365', 'caesars', 'betrivers', 'bovada', 'prizepicks', 'pick6', 'novig', 'dabble']
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
                parts = odd_obj.get('oddID', odd_key).split('-')
                if len(parts) < 5 or parts[0] not in TARGET_STATS: continue
                
                stat, player_raw, side = parts[0], parts[1], parts[4]
                book = odd_obj.get('bookmakerID', 'unknown')
                price, line = to_decimal(odd_obj.get('price')), odd_obj.get('handicap')
                prop_link = odd_obj.get('deepLink') # Extract SGO deep link
                
                player = player_raw.split('_1_')[0].replace('_', ' ').title()
                uid = f"{player}_{stat}_{line}"
                if uid not in market_groups: market_groups[uid] = {'sharp': {}, 'soft': {}}
                
                if book == 'pinnacle': market_groups[uid]['sharp'][side] = price
                elif book in soft_list:
                    if price > market_groups[uid]['soft'].get(side, {}).get('price', 0):
                        market_groups[uid]['soft'][side] = {'price': price, 'book': book, 'line': line, 'prop_link': prop_link}

            for uid, val in market_groups.items():
                sharp, soft = val['sharp'], val['soft']
                if 'over' in sharp and 'under' in sharp:
                    vig = (1/sharp['over']) + (1/sharp['under'])
                    probs = {'over': (1/sharp['over'])/vig, 'under': (1/sharp['under'])/vig}
                    for side in ['over', 'under']:
                        if side in soft:
                            ev = (soft[side]['price'] * probs[side]) - 1
                            if ev > 0.02:
                                p_name, s_type, l_val = uid.split('_')
                                market, selection = s_type.upper(), f"{p_name} {side.upper()} {l_val}"
                                
                                if not is_already_logged(matchup, market, selection):
                                    units = min((ev / (soft[side]['price'] - 1)) / 4 * 100, 5.0)
                                    log_bet_to_db(
                                        matchup.strip(), market, selection, to_american(soft[side]['price']), 
                                        ev, f"{units:.2f}", to_american(1/probs[side]), "basketball_nba", str(event.get('id', ''))
                                    )
                                    # Fallback to app scheme if deepLink is missing
                                    link = soft[side].get('prop_link') or get_dynamic_link(soft[side]['book'], p_name)
                                    picks.append({"msg": f"🏀 **NBA PROP ALERT** 🏀\n**Match:** {matchup}\n**Prop:** {selection}\n**Book:** [{soft[side]['book'].upper()}]({link}) @ {to_american(soft[side]['price'])}\n**Edge:** {ev*100:.2f}%"})
    except Exception as e: print(f"Prop Bot Error: {e}")
    return picks

def main():
    picks = get_sgo_edges()
    if picks and DISCORD_WEBHOOK_URL:
        for p in picks: requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": p['msg'], "color": 15158332, "image": {"url": FOOTER_IMG}}]})

if __name__ == "__main__": main()