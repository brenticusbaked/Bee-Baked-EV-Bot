import os
import requests
import json
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# FanDuel's hidden frontend API for NBA events
FD_URL = "https://sbapi.nj.sportsbook.fanduel.com/api/content-managed-page?page=SPORT&eventTypeId=7522&competitionId=10547864"
TRACKER_FILE = "fd_lines.json"

# Spoofed headers to bypass basic Cloudflare protection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}

def load_previous_lines():
    if not os.path.exists(TRACKER_FILE):
        return {}
    with open(TRACKER_FILE, "r") as f:
        return json.load(f)

def save_current_lines(lines):
    with open(TRACKER_FILE, "w") as f:
        json.dump(lines, f)

def scrape_fanduel():
    try:
        res = requests.get(FD_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Blocked by FanDuel! Status Code: {res.status_code}")
            return
            
        data = res.json()
        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        # FanDuel stores their odds in a massive dictionary called 'attachments'
        markets = data.get('attachments', {}).get('markets', {})
        events = data.get('attachments', {}).get('events', {})
        
        for mkt_id, mkt_data in markets.items():
            if mkt_data.get('marketName') == 'Spread':
                event_id = str(mkt_data.get('eventId'))
                matchup = events.get(event_id, {}).get('name', 'Unknown Matchup')
                
                for runner in mkt_data.get('runners', []):
                    team = runner.get('runnerName')
                    line = runner.get('handicap')
                    
                    if line is not None:
                        unique_key = f"{event_id}_{team}"
                        current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line}
                        
                        # Compare against previous run to find STEAM
                        if unique_key in previous_lines:
                            old_line = previous_lines[unique_key]['line']
                            
                            if old_line is not None and line is not None:
                                diff = abs(float(line) - float(old_line))
                                if diff >= 1.5:
                                    alerts.append(
                                        f"📈 **FD STEAM ALERT:** {matchup}\n"
                                        f"**{team} Spread Moved!**\n"
                                        f"Old Line: {old_line} ➡️ **New Line: {line}**"
                                    )
                                    
        save_current_lines(current_lines)
        
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 15615}]}) # FanDuel Blue
                print("FanDuel Alert sent.")
        else:
            print("FanDuel Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping FanDuel: {e}")

if __name__ == "__main__":
    scrape_fanduel()