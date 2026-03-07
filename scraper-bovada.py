import os
import requests
import json
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# Bovada's public frontend API for NBA
BOVADA_URL = "https://www.bovada.lv/services/sports/api/v1/events/basketball/nba-season"
TRACKER_FILE = "bovada_lines.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json"
}

def load_previous_lines():
    if not os.path.exists(TRACKER_FILE):
        return {}
    with open(TRACKER_FILE, "r") as f:
        return json.load(f)

def save_current_lines(lines):
    with open(TRACKER_FILE, "w") as f:
        json.dump(lines, f)

def scrape_bovada():
    try:
        res = requests.get(BOVADA_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Blocked by Bovada! Status Code: {res.status_code}")
            return
            
        data = res.json()
        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        # Bovada wraps their data in an array
        for item in data:
            for event in item.get('events', []):
                matchup = event.get('description', 'Unknown Matchup')
                event_id = str(event.get('id'))
                
                for display_group in event.get('displayGroups', []):
                    for market in display_group.get('markets', []):
                        if market.get('description') == 'Point Spread':
                            for outcome in market.get('outcomes', []):
                                team = outcome.get('description')
                                line = outcome.get('price', {}).get('handicap')
                                
                                if line is not None:
                                    unique_key = f"{event_id}_{team}"
                                    current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line}
                                    
                                    if unique_key in previous_lines:
                                        old_line = previous_lines[unique_key]['line']
                                        if old_line is not None and line is not None:
                                            diff = abs(float(line) - float(old_line))
                                            if diff >= 1.5:
                                                alerts.append(
                                                    f"📈 **BOVADA OFFSHORE STEAM ALERT:** {matchup}\n"
                                                    f"**{team} Spread Moved!**\n"
                                                    f"Old Line: {old_line} ➡️ **New Line: {line}**"
                                                )
                                                
        save_current_lines(current_lines)
        
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 15158332}]}) # Red
                print("Bovada Alert sent.")
        else:
            print("Bovada Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping Bovada: {e}")

if __name__ == "__main__":
    scrape_bovada()