import os
import requests
import json
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# PrizePicks projections endpoint (League 7 is usually NBA)
PRIZEPICKS_URL = "https://api.prizepicks.com/projections?league_id=7"
TRACKER_FILE = "prizepicks_lines.json"

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

def scrape_prizepicks():
    try:
        res = requests.get(PRIZEPICKS_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Blocked by PrizePicks! Status Code: {res.status_code}")
            return
            
        data = res.json()
        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        # PrizePicks splits data into 'data' (the lines) and 'included' (the player names)
        # We need to map player IDs to their names first
        players = {}
        for item in data.get('included', []):
            if item.get('type') == 'new_player':
                players[item['id']] = item.get('attributes', {}).get('name')
                
        # Now track the actual projections
        for proj in data.get('data', []):
            if proj.get('type') == 'projection':
                attr = proj.get('attributes', {})
                stat_type = attr.get('stat_type')
                line = attr.get('line_score')
                
                # We link the projection back to the player ID
                player_id = proj.get('relationships', {}).get('new_player', {}).get('data', {}).get('id')
                player_name = players.get(player_id, "Unknown Player")
                
                if line is not None and player_name != "Unknown Player":
                    unique_key = f"{player_id}_{stat_type}"
                    current_lines[unique_key] = {"player": player_name, "stat": stat_type, "line": line}
                    
                    if unique_key in previous_lines:
                        old_line = previous_lines[unique_key]['line']
                        
                        # If a player's prop moves by 1 or more, the market is shifting
                        if old_line is not None and line is not None:
                            diff = abs(float(line) - float(old_line))
                            if diff >= 1.0:
                                alerts.append(
                                    f"📈 **PRIZEPICKS BUMP ALERT:** {player_name}\n"
                                    f"**{stat_type} Moved!**\n"
                                    f"Old Line: {old_line} ➡️ **New Line: {line}**"
                                )
                                        
        save_current_lines(current_lines)
        
        if alerts and DISCORD_WEBHOOK_URL:
            # We will only send up to 5 alerts to avoid Discord spam if they bump a whole team at once
            for msg in alerts[:5]:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 10181046}]}) # Purple
                print("PrizePicks Alert sent.")
        else:
            print("PrizePicks Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping PrizePicks: {e}")

if __name__ == "__main__":
    scrape_prizepicks()