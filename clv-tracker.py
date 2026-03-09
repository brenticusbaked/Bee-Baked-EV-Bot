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
        params = {'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'h2h,totals', 'bookmakers': 'pinnacle', 'oddsFormat': 'american'}
        res = requests.get(url, params=params)
        if res.status_code == 200:
            for game in res.json():
                matchup = f"{game['away_team']} @ {game['home_team']}"
                for bm in game.get('bookmakers', []):
                    for mkt in bm['markets']:
                        for out in mkt['outcomes']:
                            key = f"{matchup}_{mkt['key']}_{out['name']}_{out.get('point', '')}"
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
            if not row[8]: # If Closing_Line_Pinnacle is empty
                matchup = row[1].lower()
                market = row[2].lower().replace('h2h_1st_half', 'h2h')
                selection = row[3].lower()
                
                # Create search key based on selection string
                search_key = f"{matchup}_{market}_{selection}"
                if search_key in pinnacle:
                    row[8] = f"+{pinnacle[search_key]}" if pinnacle[search_key] > 0 else str(pinnacle[search_key])
            updated_rows.append(row)

    with open('bets_log.csv', mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(updated_rows)

if __name__ == "__main__":
    track_clv()