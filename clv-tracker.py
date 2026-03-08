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
    if not ODDS_API_KEY: return {}
    
    all_odds = {}
    for sport in SPORTS:
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY, 
            'regions': 'us,eu', 
            'markets': 'h2h', 
            'bookmakers': 'pinnacle', 
            'oddsFormat': 'american' # Pulling American odds natively this time
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
            
    return all_odds

def run_clv_tracker():
    if not os.path.exists('bets_log.csv'):
        print("No bets logged yet to track CLV.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    current_odds_map = get_current_pinnacle_odds()
    
    updated_rows = []
    alerts = []
    
    with open('bets_log.csv', mode='r') as f:
        reader = csv.reader(f)
        header = next(reader)
        
        # Dynamically add the CLV column if this is the first time running
        if "Closing_Line_Pinnacle" not in header:
            header.append("Closing_Line_Pinnacle")
        updated_rows.append(header)
        
        for row in reader:
            # Pad the row if it's missing the new CLV column
            while len(row) < len(header):
                row.append("")
                
            bet_date = row[0]
            matchup = row[1]
            selection = row[3]
            odds_taken = row[4]
            
            # Only update CLV for bets placed TODAY
            if bet_date == today:
                if matchup in current_odds_map:
                    for outcome in current_odds_map[matchup]:
                        # Match the team name to our bet selection
                        if outcome['name'] in selection or selection in outcome['name']:
                            clv_price = outcome['price']
                            # Format nicely with a plus sign for positive odds
                            clv_price_str = f"+{clv_price}" if clv_price > 0 else str(clv_price)
                            
                            # If the CLV changed, record it and prep an alert
                            if row[-1] != clv_price_str:
                                row[-1] = clv_price_str
                                alerts.append(
                                    f"**{matchup}** ({selection})\n"
                                    f"Odds Taken: {odds_taken} ➡️ **Sharp CLV: {clv_price_str}**"
                                )
            
            updated_rows.append(row)
            
    # Write the upgraded data back to the CSV
    with open('bets_log.csv', mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(updated_rows)
        
    # Send a consolidated Discord update
    if alerts and DISCORD_WEBHOOK_URL:
        msg = "📈 **DAILY CLV TRACKER UPDATE** 📈\n━━━━━━━━━━━━━━━━━━━━\n" + "\n\n".join(alerts[:10])
        requests.post(DISCORD_WEBHOOK_URL, json={
            "embeds": [{"description": msg, "color": 5763719, "image": {"url": FOOTER_IMG}}] # Success Green
        })
        print("CLV Report Sent.")
    else:
        print("No CLV updates required right now.")

if __name__ == "__main__":
    run_clv_tracker()