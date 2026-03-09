import os
import requests
import csv
import argparse
from datetime import datetime, timezone

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
    "soccer": {"api_key": "soccer_epl", "markets": "h2h,spreads,totals", "icon": "⚽", "name": "EPL"}
}

PROP_MARKETS = "player_points,player_rebounds,player_assists"
DISCORD_BATCH = []

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def calculate_ev(pinnacle_decimal, soft_decimal):
    """Calculates EV by estimating a 3% vig removal from Pinnacle."""
    fair_prob = 1 / (pinnacle_decimal * 1.03) 
    return (soft_decimal * fair_prob) - 1

def log_bet_to_csv(matchup, market, selection, odds, ev_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle', 'Result'])
        writer.writerow([datetime.now().strftime("%Y-%m-%d"), matchup, market, selection, odds, f"{ev_val*100:.2f}%", units, fair_price, "", ""])

def process_odds_data(game_data, config):
    matchup = f"{game_data['away_team']} @ {game_data['home_team']}"
    market_data = {}

    for bm in game_data.get('bookmakers', []):
        for mkt in bm['markets']:
            mkey = mkt['key']
            if mkey not in market_data: market_data[mkey] = {}
            for outcome in mkt['outcomes']:
                okey = f"{outcome['name']}_{outcome.get('point', '')}"
                if okey not in market_data[mkey]: 
    market_data[mkey][okey] = {'pinnacle': None, 'softs': []}