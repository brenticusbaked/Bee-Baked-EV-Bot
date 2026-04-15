import os
import requests
import argparse
from datetime import datetime
from db_manager import is_already_logged, log_bet_to_db

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

REGIONS = 'us,eu'
BOOKMAKERS = 'fanduel,draftkings,bet365,pinnacle'
ODDS_FORMAT = 'decimal'

SPORT_CONFIGS = {
    "nba": {"api_key": "basketball_nba", "markets": "h2h,spreads,totals", "icon": "🏀", "name": "NBA"},
    "nhl": {"api_key": "icehockey_nhl", "markets": "h2h,spreads,totals", "icon": "🏒", "name": "NHL"},
    "mlb": {"api_key": "baseball_mlb", "markets": "h2h,spreads,totals", "icon": "⚾", "name": "MLB"},
    "soccer": {"api_key": "soccer_epl", "markets": "h2h,spreads,totals", "icon": "⚽", "name": "EPL"},
    "esports": {"api_key": "esports_counterstrike", "markets": "h2h", "icon": "🎮", "name": "CS2"},
    "tennis": {"api_key": "tennis_atp_wimbledon", "markets": "h2h", "icon": "🎾", "name": "ATP"}
}

DISCORD_BATCH = []

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def calculate_ev(pinnacle_decimal, soft_decimal):
    fair_prob = 1 / (pinnacle_decimal * 1.03) 
    return (soft_decimal * fair_prob) - 1

def process_odds_data(game_data, config):
    matchup = f"{game_data['away_team']} @ {game_data['home_team']}"
    market_data = {}

    for bm in game_data.get('bookmakers', []):
        for mkt in bm.get('markets', []):
            mkey = mkt['key']
            if mkey not in market_data: market_data[mkey] = {}
            for outcome in mkt['outcomes']:
                okey = f"{outcome['name']}_{outcome.get('point', '')}"
                if okey not in market_data[mkey]:
                    market_data[mkey][okey] = {'pinnacle': None, 'softs': []}
                
                price = float(outcome['price'])
                if bm['key'] == 'pinnacle': market_data[mkey][okey]['pinnacle'] = price
                else: market_data[mkey][okey]['softs'].append({'book': bm['title'], 'price': price})

    for mkey, outcomes in market_data.items():
        for okey, data in outcomes.items():
            if data['pinnacle'] and data['softs']:
                for soft in data['softs']:
                    ev = calculate_ev(data['pinnacle'], soft['price'])
                    if ev >= 0.03: 
                        selection = okey.replace('_', ' ').strip()
                        units = min((ev / (soft['price'] - 1)) / 4 * 100, 5.0)
                        
                        DISCORD_BATCH.append({
                            'matchup': matchup, 'market': mkey, 'selection': selection,
                            'odds_american': to_american(soft['price']), 'ev': ev, 'units': units,
                            'fair_price': to_american(data['pinnacle']), 'book': soft['book'], 'icon': config['icon']
                        })

def send_discord_chunk(chunk_text):
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={
            "embeds": [{
                "title": "🔥 NEW +EV OPPORTUNITIES 🔥", "description": chunk_text,
                "color": 5763719, "image": {"url": FOOTER_IMG}, "footer": {"text": "Bee-Baked Automated Syndicate"}
            }]
        })

def flush_alerts():
    if not DISCORD_BATCH: return
        
    final_bets = []
    for bet in DISCORD_BATCH:
        if not is_already_logged(bet['matchup'], bet['market'], bet['selection']):
            final_bets.append(bet)
            log_bet_to_db(bet['matchup'], bet['market'].upper(), bet['selection'], bet['odds_american'], bet['ev'], f"{bet['units']:.2f}", bet['fair_price'])

    if not final_bets: return
        
    final_bets.sort(key=lambda x: x['ev'], reverse=True)
    
    current_msg = ""
    for b in final_bets:
        row = f"{b['icon']} **{b['market'].upper()}** | {b['matchup']}\n↳ **{b['selection']}** | **{b['book']}** @ {b['odds_american']} (Edge: {b['ev']*100:.1f}%)\n\n"
        if len(current_msg) + len(row) > 1900:
            send_discord_chunk(current_msg)
            current_msg = row
        else:
            current_msg += row
            
    if current_msg: send_discord_chunk(current_msg)

def scan_sport(sport_key):
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return

    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': config['markets'], 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT}
    
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            for game in res.json(): process_odds_data(game, config)
    except Exception as e:
        print(f"Error scanning {sport_key}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", type=str, default="all")
    args = parser.parse_args()

    if args.sport == "all":
        for s in SPORT_CONFIGS: scan_sport(s)
    else:
        scan_sport(args.sport)
    flush_alerts()