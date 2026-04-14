import os
import csv
import requests
from datetime import datetime

# --- CONFIG ---
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
SPORTS = ['baseball_mlb', 'basketball_nba', 'icehockey_nhl']

def get_pinnacle_lines():
    lines = {}
    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'h2h,totals,spreads', 'bookmakers': 'pinnacle', 'oddsFormat': 'american'}
        res = requests.get(url, params=params)
        if res.status_code == 200:
            for game in res.json():
                matchup = f"{game['away_team']} @ {game['home_team']}"
                for bm in game.get('bookmakers', []):
                    for mkt in bm['markets']:
                        for out in mkt['outcomes']:
                            point = out.get('point', '')
                            selection_str = f"{out['name']} {point}".strip()
                            # Build the search key securely
                            key = f"{matchup}_{mkt['key']}_{selection_str}".replace(' ', '_').lower()
                            lines[key] = out['price']
    return lines

def track_clv():
    if not os.path.exists('bets_log.csv'): return

    # OPTIMIZATION: Scan the CSV to see if an API call is even necessary
    needs_clv = False
    with open('bets_log.csv', mode='r') as f:
        reader = csv.reader(f)
        header = next(reader, [])
        for row in reader:
            if not row or not any(row): continue
            while len(row) < len(header): row.append("")
            # If there is a bet logged (row[0] has date) but CLV is blank
            if not row[8] and row[0]:  
                needs_clv = True
                break
    
    if not needs_clv:
        print("No open bets require CLV tracking. Skipping API call.")
        return

    print("Open bets found. Fetching Pinnacle lines...")
    pinnacle = get_pinnacle_lines()
    updated_rows = []
    
    with open('bets_log.csv', mode='r') as f:
        reader = csv.reader(f)
        header = next(reader)
        updated_rows.append(header)
        
        for row in reader:
            if not row or not any(row): continue
            while len(row) < len(header): row.append("")
                
            if not row[8]: 
                matchup = row[1].lower()
                raw_market = row[2].lower()
                selection = row[3].lower()

                if 'spread' in raw_market or 'puckline' in raw_market: api_market = 'spreads'
                elif 'total' in raw_market or 'over' in raw_market or 'under' in raw_market: api_market = 'totals'
                elif 'ml' in raw_market or 'moneyline' in raw_market or 'f5' in raw_market or 'h2h' in raw_market: api_market = 'h2h'
                else: api_market = raw_market

                formatted_selection = selection.replace(' ', '_')
                search_key = f"{matchup}_{api_market}_{formatted_selection}".lower()
                
                if search_key in pinnacle:
                    price = pinnacle[search_key]
                    row[8] = f"+{price}" if price > 0 else str(price)
            updated_rows.append(row)

    with open('bets_log.csv', mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(updated_rows)

if __name__ == "__main__":
    track_clv()