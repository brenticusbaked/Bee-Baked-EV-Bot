import os
import csv
import requests
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

# The three main sports we want to check for closing lines
SPORTS = ['baseball_mlb', 'basketball_nba', 'icehockey_nhl']

def get_current_pinnacle_odds():
    """Pulls the latest Pinnacle odds for all major sports to use as our Closing Line."""
    if not ODDS_API_KEY: 
        print("Missing ODDS_API_KEY")
        return {}
    
    all_odds = {}
    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY, 
            'regions': 'us,eu', 
            'markets': 'h2h', 
            'bookmakers': 'pinnacle', 
            'oddsFormat': 'american' # Pulling American odds natively
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                for game in res.json():
                    matchup = f"{game['away_team']} @ {game['home_team']}"
                    for bm in game.get('bookmakers', []):
                        if bm['key'] == 'pinnacle':
                            for mkt in bm['markets']:
                                if mkt['key'] == 'h2h':
                                    all_odds[matchup] = mkt['outcomes']
        except Exception as e:
            print(f"Error fetching {sport} for CLV: {e}")
            
    return all_odds # FIXED: Was 'return all'

def track_clv():
    """Reads bets_log.csv, finds pending bets, and updates the Pinnacle CLV column."""
    if not os.path.exists('bets_log.csv'):
        print("No bets_log.csv found.")
        return

    print("Fetching current Pinnacle lines...")
    pinnacle_odds = get_current_pinnacle_odds()
    if not pinnacle_odds:
        print("Could not retrieve Pinnacle odds.")
        return

    updated_rows = []
    updates_made = 0

    with open('bets_log.csv', mode='r') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return
        
        # Ensure standard columns are present
        if "Result" not in header:
            header.append("Result")
        
        updated_rows.append(header)
        
        try:
            clv_idx = header.index("Closing_Line_Pinnacle")
            res_idx = header.index("Result")
            matchup_idx = header.index("Matchup")
            selection_idx = header.index("Selection")
        except ValueError as e:
            print(f"CSV missing an expected column: {e}")
            return

        for row in reader:
            # Pad row cleanly to match headers
            while len(row) < len(header):
                row.append("")
                
            matchup = row[matchup_idx]
            selection = row[selection_idx]
            result = row[res_idx]

            # We only track/update CLV for pending bets that haven't been graded yet
            if not result and matchup in pinnacle_odds:
                # Find the selection in the current Pinnacle outcomes
                for outcome in pinnacle_odds[matchup]:
                    # Match by team name
                    if outcome['name'].lower() in selection.lower():
                        current_line = outcome.get('price')
                        
                        # Add "+" to positive American odds to match your standard format
                        if current_line > 0:
                            formatted_line = f"+{current_line}"
                        else:
                            formatted_line = str(current_line)
                        
                        # Only update the log if the line changed or was previously empty
                        if current_line and formatted_line != row[clv_idx]:
                            row[clv_idx] = formatted_line
                            updates_made += 1
                        break

            updated_rows.append(row)

    # Save upgraded data back to CSV
    if updates_made > 0:
        with open('bets_log.csv', mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(updated_rows)
        print(f"Successfully updated Closing Line Value for {updates_made} pending bets.")
    else:
        print("No CLV updates needed. Lines haven't moved.")

if __name__ == "__main__":
    track_clv()