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

# FIXED: Updated sport keys and removed props from main markets to prevent 422 errors
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
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, ev_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle'])
        writer.writerow([datetime.now().strftime("%Y-%m-%d"), matchup, market, selection, odds, f"{ev_val*100:.2f}%", units, fair_price, ""])

def send_alert(msg, is_clutch=False, minutes_left=0):
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
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return
    print(f"Filtering competitive games for {config['name']} props...")
    
    # 1. Get spreads and start times (1 API Call)
    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    res = requests.get(url, params={'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'spreads', 'oddsFormat': ODDS_FORMAT})
    if res.status_code != 200: return
    
    now = datetime.now(timezone.utc)
    for game in res.json():
        commence_time = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        is_competitive = any(abs(float(o['point'])) <= 3.0 for bm in game['bookmakers'] for m in bm['markets'] for o in m['outcomes'] if m['key'] == 'spreads')
        
        if is_competitive:
            # 2. Get Props for competitive games (1 API Call per game)
            props_res = requests.get(f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/events/{game['id']}/odds", 
                                     params={'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': PROP_MARKETS, 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT})
            if props_res.status_code == 200:
                # Insert EV detection logic here (omitted for brevity, follows scan_sport pattern)
                # If EV found:
                mins = int((commence_time - now).total_seconds() / 60)
                if 0 < mins <= 60:
                    send_alert("Prop Found", is_clutch=True, minutes_left=mins)

def scan_sport(sport_key):
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return
    print(f"Scanning {config['name']} main markets...")
    # (Original scan_sport logic goes here, updated to use SPORT_CONFIGS)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="main", choices=["main", "props"])
    parser.add_argument("--sport", type=str, default="all")
    args = parser.parse_args()

    if args.mode == "props":
        for s in ["nba", "nhl"]: scan_props(s)
    elif args.sport == "all":
        for s in SPORT_CONFIGS: scan_sport(s)
    else:
        scan_sport(args.sport)