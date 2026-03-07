import os
import requests
import json
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# BetMGM's hidden API for NBA matches (Region: US-NJ)
MGM_URL = "https://sports.co.betmgm.com/en/sports/api/v1/fixtures?filter[competitionId]=6004"
TRACKER_FILE = "mgm_lines.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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

def scrape_betmgm():
    try:
        res = requests.get(MGM_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Blocked by BetMGM! Status Code: {res.status_code}")
            return
            
        data = res.json()
        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        for fixture in data.get('fixtures', []):
            matchup = fixture.get('name', {'value': 'Unknown Matchup'}).get('value')
            event_id = str(fixture.get('id'))
            
            for option in fixture.get('optionMarkets', []):
                # BetMGM usually refers to the spread as "Spread" or "Handicap"
                if 'Spread' in option.get('name', {}).get('value', ''):
                    for outcome in option.get('options', []):
                        team = outcome.get('name', {}).get('value')
                        
                        # Extract the actual spread number from the string (e.g., "-5.5")
                        attr = outcome.get('attributes', {})
                        line = attr.get('spread') or attr.get('line')
                        
                        if line is not None:
                            unique_key = f"{event_id}_{team}"
                            current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line}
                            
                            if unique_key in previous_lines:
                                old_line = previous_lines[unique_key]['line']
                                
                                if old_line is not None and line is not None:
                                    diff = abs(float(line) - float(old_line))
                                    if diff >= 1.5:
                                        alerts.append(
                                            f"📈 **MGM STEAM ALERT:** {matchup}\n"
                                            f"**{team} Spread Moved!**\n"
                                            f"Old Line: {old_line} ➡️ **New Line: {line}**"
                                        )
                                        
        save_current_lines(current_lines)
        
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 13611036}]}) # MGM Gold
                print("BetMGM Alert sent.")
        else:
            print("BetMGM Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping BetMGM: {e}")

if __name__ == "__main__":
    scrape_betmgm()