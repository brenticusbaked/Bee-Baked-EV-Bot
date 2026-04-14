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
        # FIXED: Added 'spreads' to markets parameter
        params = {'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'h2h,totals,spreads', 'bookmakers': 'pinnacle', 'oddsFormat': 'american'}
        res = requests.get(url, params=params)
        if res.status_code == 200:
            for game in res.json():
                matchup = f"{game['away_team']} @ {game['home_team']}"
                for bm in game.get('bookmakers', []):
                    for mkt in bm['markets']:
                        for out in mkt['outcomes']:
                            # FIXED: Format key space instead of underscore for point to match CSV logged format
                            point = out.get('point', '')
                            selection_str = f"{out['name']} {point}".strip()
                            key = f"{matchup}_{mkt['key']}_{selection_str}"
                            lines[key.lower()] = out['price']
    return lines

def track_clv():
    if not os.path.exists('bets_log.csv'): return
    pinnacle = get_pinnacle_lines()
    updated_rows = []
    
    with open('bets_log.csv', mode='r') as f:
        reader = csv.reader(f)
        header = next(reader)
        updated_rows.append(header)
        
        for row in reader:
            # FIXED: Skip empty lines that cause index crashes
            if not row or not any(row):
                continue

            # Pad the row to match the header length to prevent IndexErrors on corrupted lines
            while len(row) < len(header):
                row.append("")
                
            if not row[8]: # If Closing_Line_Pinnacle is empty
                matchup = row[1].lower()
                raw_market = row[2].lower()
                selection = row[3].lower()

                # FIXED: Map CSV market strings to Odds API market formats
                if 'spread' in raw_market or 'puckline' in raw_market:
                    api_market = 'spreads'
                elif 'total' in raw_market or 'over' in raw_market or 'under' in raw_market:
                    api_market = 'totals'
                elif 'ml' in raw_market or 'moneyline' in raw_market or 'f5' in raw_market or 'h2h' in raw_market:
                    api_market = 'h2h'
                else:
                    api_market = raw_market

                # Create search key based on aligned selection string
                search_key = f"{matchup}_{api_market}_{selection}"
                if search_key in pinnacle:
                    row[8] = f"+{pinnacle[search_key]}" if pinnacle[search_key] > 0 else str(pinnacle[search_key])
            updated_rows.append(row)

    with open('bets_log.csv', mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(updated_rows)

if __name__ == "__main__":
    track_clv()