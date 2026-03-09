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
    "soccer": {"api_key": "soccer_epl", "markets": "h2h,spreads,totals", "icon": "⚽", "name": "EPL"},
    "esports": {"api_key": "esports_counterstrike", "markets": "h2h", "icon": "🎮", "name": "CS2"},
    "tennis": {"api_key": "tennis_atp_wimbledon", "markets": "h2h", "icon": "🎾", "name": "ATP"}
}

PROP_MARKETS = "player_points,player_rebounds,player_assists,player_shots_on_goal"

# Global list to hold all found bets before sending them
DISCORD_BATCH = []

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def is_already_logged(matchup, market, selection):
    """Checks if we have already logged this specific bet today to prevent spam."""
    if not os.path.exists('bets_log.csv'): return False
    today = datetime.now().strftime("%Y-%m-%d")
    with open('bets_log.csv', 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) > 3 and row[0] == today:
                if row[1] == matchup and row[2].upper() == market.upper() and row[3] == selection:
                    return True
    return False

def log_bet_to_csv(matchup, market, selection, odds, ev_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle', 'Result'])
        writer.writerow([datetime.now().strftime("%Y-%m-%d"), matchup, market, selection, odds, f"{ev_val*100:.2f}%", units, fair_price, "", ""])

def process_odds_data(game_data, config, commence_time, now):
    """Processes odds for a single game and queues any found edges into the batch list."""
    matchup = f"{game_data['away_team']} @ {game_data['home_team']}"
    market_data = {}

    for bm in game_data.get('bookmakers', []):
        for mkt in bm['markets']:
            mkey = mkt['key']
            if mkey not in market_data: market_data[mkey] = {}
            for outcome in mkt['outcomes']:
                okey = f"{outcome['name']}_{outcome.get('point', '')}"
                if okey not in market_data[mkey]: market_data[mkey][okey] = {'pinnacle': None, 'softs': []}
                
                price = float(outcome['price'])
                if bm['key'] == 'pinnacle':
                    market_data[mkey][okey]['pinnacle'] = price
                else:
                    market_data[mkey][okey]['softs'].append({'book': bm['title'], 'price': price})

    for mkey, outcomes in market_data.items():
        for okey, data in outcomes.items():
            if data['pinnacle'] and data['softs']:
                fair_price = data['pinnacle'] * 0.97 
                for soft in data['softs']:
                    ev = (soft['price'] / fair_price) - 1
                    if ev >= 0.03:
                        selection = okey.replace('_', ' ').strip()
                        units = min((ev / (soft['price'] - 1)) / 4 * 100, 5.0)
                        
                        DISCORD_BATCH.append({
                            'matchup': matchup,
                            'market': mkey,
                            'selection': selection,
                            'odds_american': to_american(soft['price']),
                            'ev': ev,
                            'units': units,
                            'fair_price': to_american(fair_price),
                            'book': soft['book'],
                            'icon': config['icon']
                        })

def flush_alerts():
    """Deduplicates conflicting bets, filters out previously logged bets, and sends ONE Discord message."""
    if not DISCORD_BATCH: 
        print("No new +EV bets found.")
        return
        
    best_bets = {}
    
    # 1. Resolve Contradictions and Duplicates
    for bet in DISCORD_BATCH:
        mkey = bet['market'].lower()
        sel = bet['selection'].lower()
        matchup = bet['matchup']
        
        # Create a unique key to group conflicting bets
        if mkey in ['h2h', 'spreads', 'totals']:
            # For main markets, only allow ONE bet per game per market
            c_key = f"{matchup}_{mkey}"
        else:
            # For props, isolate the player's name so Over/Under conflict
            player = sel.split(' over ')[0].split(' under ')[0]
            c_key = f"{matchup}_{mkey}_{player}"
            
        # Keep only the bet with the highest EV in its conflict group
        if c_key not in best_bets or bet['ev'] > best_bets[c_key]['ev']:
            best_bets[c_key] = bet
            
    # 2. Filter out bets we already logged today
    final_bets = []
    for bet in best_bets.values():
        if not is_already_logged(bet['matchup'], bet['market'], bet['selection']):
            final_bets.append(bet)
            # Save to CSV now that it's approved
            log_bet_to_csv(bet['matchup'], bet['market'].upper(), bet['selection'], bet['odds_american'], bet['ev'], f"{bet['units']:.2f}", bet['fair_price'])

    if not final_bets:
        print("All found bets were already logged today. Skipping Discord alert.")
        return
        
    # 3. Build and send the consolidated Discord message
    final_bets.sort(key=lambda x: x['ev'], reverse=True) # Sort highest edge first
    
    description = ""
    for b in final_bets:
        row = f"{b['icon']} **{b['market'].upper()}** | {b['matchup']}\n↳ **{b['selection']}** | **{b['book']}** @ {b['odds_american']} (Edge: {b['ev']*100:.1f}%)\n\n"
        # Discord limit is 4096 characters per description
        if len(description) + len(row) < 4000:
            description += row
            
    if DISCORD_WEBHOOK_URL:
        payload = {
            "embeds": [{
                "title": f"🔥 {len(final_bets)} NEW +EV OPPORTUNITIES 🔥",
                "description": description,
                "color": 5763719,
                "image": {"url": FOOTER_IMG},
                "footer": {"text": "Bee-Baked Automated Syndicate"}
            }]
        }
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print(f"Sent consolidated alert for {len(final_bets)} bets.")

def scan_props(sport_key):
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return
    print(f"Filtering competitive games for {config['name']} props...")
    
    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    res = requests.get(url, params={'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'spreads', 'oddsFormat': ODDS_FORMAT})
    if res.status_code != 200: return
    
    now = datetime.now(timezone.utc)
    for game in res.json():
        commence_time = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
        is_competitive = any(abs(float(o['point'])) <= 3.0 for bm in game['bookmakers'] for m in bm['markets'] for o in m['outcomes'] if m['key'] == 'spreads')
        
        if is_competitive:
            props_res = requests.get(f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/events/{game['id']}/odds", 
                                     params={'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': PROP_MARKETS, 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT})
            if props_res.status_code == 200:
                process_odds_data(props_res.json(), config, commence_time, now)

def scan_sport(sport_key):
    config = SPORT_CONFIGS.get(sport_key)
    if not config or not ODDS_API_KEY: return
    print(f"Scanning {config['name']} main markets...")

    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': config['markets'], 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT}
    
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            now = datetime.now(timezone.utc)
            for game in res.json():
                commence_time = datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00'))
                process_odds_data(game, config, commence_time, now)
    except Exception as e:
        print(f"Error scanning {sport_key}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="main", choices=["main", "props"])
    parser.add_argument("--sport", type=str, default="all")
    args = parser.parse_args()

    # Step 1: Run the requested scans (adds edges to DISCORD_BATCH)
    if args.mode == "props":
        for s in ["nba", "nhl"]: scan_props(s)
    elif args.sport == "all":
        for s in SPORT_CONFIGS: scan_sport(s)
    else:
        scan_sport(args.sport)
        
    # Step 2: Flush the alerts (Deduplicates, logs, and sends ONE Discord message)
    flush_alerts()