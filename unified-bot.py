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

SPORT_CONFIGS = {
    "nba": {"api_key": "basketball_nba", "markets": "h2h,spreads,totals", "icon": "🏀", "name": "NBA"},
    "nhl": {"api_key": "icehockey_nhl", "markets": "h2h,spreads,totals", "icon": "🏒", "name": "NHL"},
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
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{ev_val*100:.2f}%", units, fair_price, ""
        ])

def send_clutch_alert(p, minutes_left):
    """Sends a high-priority alert for games starting soon."""
    if not DISCORD_WEBHOOK_URL: return
    requests.post(DISCORD_WEBHOOK_URL, json={
        "content": "🚨 **CLUTCH MOMENT ALERT** 🚨 @everyone", 
        "embeds": [{
            "title": f"⏱️ STARTS IN {minutes_left} MINUTES",
            "description": p["msg"], 
            "color": 15548997, # Vivid Red
            "image": {"url": FOOTER_IMG}
        }]
    })

def scan_props(sport_key):
    """Scans props with Smart Filter and Clutch Moment timing."""
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return

    # 1. Get spreads and start times (1 API Call)
    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'spreads', 'oddsFormat': ODDS_FORMAT}
    res = requests.get(url, params=params)
    if res.status_code != 200: return
    
    now = datetime.now(timezone.utc)

    for game in res.json():
        event_id = game['id']
        matchup = f"{game['away_team']} @ {game['home_team']}"
        commence_time = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        
        # Check if game is "Competitive" (Spread <= 3)
        is_competitive = False
        for bm in game.get('bookmakers', []):
            for mkt in bm.get('markets', []):
                if mkt['key'] == 'spreads':
                    for outcome in mkt['outcomes']:
                        if abs(float(outcome.get('point', 100))) <= 3.0:
                            is_competitive = True
                            break
        
        if not is_competitive: continue

        # 2. Get Props for competitive games (1 API Call per game)
        props_url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/events/{event_id}/odds"
        params = {'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': PROP_MARKETS, 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT}
        props_res = requests.get(props_url, params=params)
        if props_res.status_code != 200: continue
        
        # Process prop EV logic...
        # If EV > 0.01:
        minutes_until_start = int((commence_time - now).total_seconds() / 60)
        
        # Determine if this is a Clutch Moment Alert
        # if 0 < minutes_until_start <= 60:
        #     send_clutch_alert(pick_data, minutes_until_start)