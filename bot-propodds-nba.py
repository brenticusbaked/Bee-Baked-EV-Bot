import os
import requests
import csv
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SPORTS_GAME_ODDS_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

TARGET_STATS = ['points', 'assists']

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def to_decimal(price):
    try:
        price = float(price)
        if price > 100: return (price / 100) + 1
        elif price < -100: return (100 / abs(price)) + 1
        return price
    except:
        return 1.909 # Default -110 if error

def log_bet_to_csv(matchup, market, selection, odds, ev_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            # We ensure the CLV column is created from the start
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle'])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{ev_val*100:.2f}%", units, fair_price, ""
        ])

def get_sgo_edges():
    if not SGO_API_KEY:
        print("Missing Sports Game Odds API Key!")
        return []

    picks = []
    # SGO's master v2 endpoint
    url = "https://api.sportsgameodds.com/v2/events"
    params = {
        'apiKey': SGO_API_KEY,
        'leagueID': 'NBA',
        'oddsAvailable': 'true'
    }

    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code != 200:
            print(f"SGO API Error: {res.status_code}")
            return []
            
        data = res.json()
        
        for event in data:
            matchup = event.get('name', 'Unknown Matchup')
            odds_data = event.get('odds', {})
            market_groups = {}
            
            # SGO organizes odds by unique oddIDs
            for odd_key, odd_obj in odds_data.items():
                odd_id = odd_obj.get('oddID', odd_key)
                parts = odd_id.split('-')
                
                # oddID format: stat-player-game-ou-over
                if len(parts) >= 5:
                    stat_type = parts[0]
                    player_raw = parts[1]
                    side = parts[4] # 'over' or 'under'
                    
                    if stat_type in TARGET_STATS:
                        bookmaker = odd_obj.get('bookmakerID', 'unknown')
                        price_raw = odd_obj.get('price') 
                        line = odd_obj.get('handicap')
                        
                        if price_raw is None or line is None: continue
                        
                        price = to_decimal(price_raw)
                        player = player_raw.split('_1_')[0].replace('_', ' ').title()
                        unique_id = f"{player}_{stat_type}_{line}"
                        
                        if unique_id not in market_groups:
                            market_groups[unique_id] = {'sharp': {}, 'soft': {}}
                            
                        # Use Pinnacle as sharp, US books as soft
                        if bookmaker == 'pinnacle':
                            market_groups[unique_id]['sharp'][side] = price
                        elif bookmaker in ['fanduel', 'draftkings', 'betmgm', 'espn', 'fanatics', 'bet365']:
                            # Only keep the best soft price for this side
                            if side not in market_groups[unique_id]['soft']