import os
import requests
import csv
import argparse
from datetime import datetime, timezone, timedelta

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

REGIONS = 'us,eu'
BOOKMAKERS = 'fanduel,draftkings,bet365,pinnacle'
ODDS_FORMAT = 'decimal'

# Updated sport keys and main markets
SPORT_CONFIGS = {
    "nba": {"api_key": "basketball_nba", "markets": "h2h,spreads,totals", "icon": "🏀", "name": "NBA"},
    "nhl": {"api_key": "icehockey_nhl", "markets": "h2h,spreads,totals", "icon": "🏒", "name": "NHL"},
    "mlb": {"api_key": "baseball_mlb", "markets": "h2h,spreads,totals", "icon": "⚾", "name": "MLB"},
    "soccer": {"api_key": "soccer_epl", "markets": "h2h,spreads,totals", "icon": "⚽", "name": "EPL"},
    "esports": {"api_key": "esports_counterstrike", "markets": "h2h", "icon": "🎮", "name": "CS2"},
    "tennis": {"api_key": "tennis_atp_wimbledon", "markets": "h2h", "icon": "🎾", "name": "ATP"}
}

PROP_MARKETS = "player_points,player_rebounds,player_assists,player_shots_on_goal"

def to_american(dec):
    """Converts decimal odds to American format string."""
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, ev_val, units, fair_price):
    """Logs the identified +EV bet to bets_log.csv for tracking."""
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle'])
        writer.writerow([datetime.now().strftime("%Y-%m-%d"), matchup, market, selection, odds, f"{ev_val*100:.2f}%", units, fair_price, ""])

def send_alert(msg, is_clutch=False, minutes_left=0):
    """Sends a formatted alert to the Discord webhook."""
    if not DISCORD_WEBHOOK_URL: return
    color = 15548997 if is_clutch else 5763719
    content = "🚨 **CLUTCH MOMENT ALERT** 🚨 @everyone" if is_clutch else ""
    title = f"⏱️ STARTS IN {minutes_left} MINUTES" if is_clutch else "🔥 +EV OPPORTUNITY"
    
    payload = {
        "content": content,
        "embeds": [{
            "title": title,
            "description": msg,
            "color": color,
            "image": {"url": FOOTER_IMG},
            "footer": {"text": "Bee-Baked Automated Syndicate"}
        }]
    }
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

def scan_props(sport_key):
    """Scans for +EV player props in competitive games."""
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return
    print(f"Filtering competitive games for {config['name']} props...")
    
    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    res = requests.get(url, params={'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'spreads', 'oddsFormat': ODDS_FORMAT})
    if res.status_code != 200: return
    
    now = datetime.now(timezone.utc)
    for game in res.json():
        commence_time = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        # Only look at props for games with a spread of 3 points or less
        is_competitive = any(abs(float(o['point'])) <= 3.0 for bm in game['bookmakers'] for m in bm['markets'] for o in m['outcomes'] if m['key'] == 'spreads')
        
        if is_competitive:
            props_res = requests.get(f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/events/{game['id']}/odds", 
                                     params={'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': PROP_MARKETS, 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT})
            if props_res.status_code == 200:
                process_odds_data(props_res.json(), config, commence_time, now)

def scan_sport(sport_key):
    """Scans main markets for a specific sport for +EV opportunities."""
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return
    print(f"Scanning {config['name']} main markets...")

    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': config['markets'], 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT}
    
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code != 200: return
        
        for game in res.json():
            commence_time = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
            process_odds_data(game, config, commence_time, datetime.now(timezone.utc))
    except Exception as e:
        print(f"Error scanning {sport_key}: {e}")

def process_odds_data(game_data, config, commence_time, now):
    """Processes odds for a single game/event to identify edges against Pinnacle."""
    matchup = f"{game_data['away_team']} @ {game_data['home_team']}"
    market_data = {}

    # Organize bookmaker data by market and specific outcome
    bookmakers = game_data.get('bookmakers', [])
    for bm in bookmakers:
        for mkt in bm['markets']:
            mkey = mkt['key']
            if mkey not in market_data: market_data[mkey] = {}
            for outcome in mkt['outcomes']:
                # Create a unique key for the outcome (e.g., Team Name + Point Spread)
                okey = f"{outcome['name']}_{outcome.get('point', '')}"
                if okey not in market_data[mkey]: market_data[mkey][okey] = {'pinnacle': None, 'softs': []}
                
                price = float(outcome['price'])
                if bm['key'] == 'pinnacle':
                    market_data[mkey][okey]['pinnacle'] = price
                else:
                    market_data[mkey][okey]['softs'].append({'book': bm['title'], 'price': price})

    # Compare soft books against Pinnacle (sharp line)
    for mkey, outcomes in market_data.items():
        for okey, data in outcomes.items():
            if data['pinnacle'] and data['softs']:
                # Estimate fair price by removing estimated Pinnacle vig (approx 3%)
                fair_price = data['pinnacle'] * 0.97 
                for soft in data['softs']:
                    ev = (soft['price'] / fair_price) - 1
                    if ev >= 0.03: # Alert if Expected Value is 3% or higher
                        selection = okey.replace('_', ' ').strip()
                        # Suggest Kelly Criterion units (fractional)
                        units = min((ev / (soft['price'] - 1)) / 4 * 100, 5.0)
                        
                        mins_left = int((commence_time - now).total_seconds() / 60)
                        is_clutch = 0 < mins_left <= 60

                        log_bet_to_csv(matchup, mkey.upper(), selection, to_american(soft['price']), ev, f"{units:.2f}", to_american(fair_price))
                        
                        msg = f"**Match:** {matchup}\n**Market:** {mkey.upper()}\n**Bet:** {selection}\n**Book:** {soft['book']} @ {to_american(soft['price'])}\n**Edge:** {ev*100:.1f}% | **Units:** {units:.2f}"
                        send_alert(f"{config['icon']} {msg}", is_clutch=is_clutch, minutes_left=mins_left)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="main", choices=["main", "props"])
    parser.add_argument("--sport", type=str, default="all")
    args = parser.parse_args()

    if args.mode == "props":
        # Scanning major sports for props when in props mode
        for s in ["nba", "nhl"]: scan_props(s)
    elif args.sport == "all":
        for s in SPORT_CONFIGS: scan_sport(s)
    else:
        scan_sport(args.sport)