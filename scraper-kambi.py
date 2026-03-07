import os
import requests
import json
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# Kambi CDN for NBA matches (using BetRivers IL as the proxy)
KAMBI_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/rsiusil/listView/basketball/nba/all/all/matches.json?lang=en_US&market=US-IL"
TRACKER_FILE = "kambi_lines.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
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

def scrape_kambi():
    try:
        res = requests.get(KAMBI_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Blocked by Kambi! Status Code: {res.status_code}")
            return
            
        data = res.json()
        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        for event in data.get('events', []):
            matchup = event.get('event', {}).get('name', 'Unknown Matchup')
            event_id = str(event.get('event', {}).get('id'))
            
            for bet_offer in event.get('betOffers', []):
                # We are looking for the main point spread (Criterion ID 1001212 is often spread)
                if bet_offer.get('criterion', {}).get('englishLabel') == 'Handicap':
                    for outcome in bet_offer.get('outcomes', []):
                        team = outcome.get('englishLabel')
                        line = outcome.get('line')
                        
                        if line is not None:
                            # Kambi formats lines in thousands (e.g., -5500 is -5.5)
                            formatted_line = float(line) / 1000
                            unique_key = f"{event_id}_{team}"
                            current_lines[unique_key] = {"matchup": matchup, "team": team, "line": formatted_line}
                            
                            if unique_key in previous_lines:
                                old_line = previous_lines[unique_key]['line']
                                if old_line is not None and formatted_line is not None:
                                    diff = abs(float(formatted_line) - float(old_line))
                                    if diff >= 1.5:
                                        alerts.append(
                                            f"📈 **KAMBI STEAM ALERT:** {matchup}\n"
                                            f"**{team} Spread Moved!**\n"
                                            f"Old Line: {old_line} ➡️ **New Line: {formatted_line}**"
                                        )
                                        
        save_current_lines(current_lines)
        
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 16776960}]}) # Yellow
                print("Kambi Alert sent.")
        else:
            print("Kambi Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping Kambi: {e}")

if __name__ == "__main__":
    scrape_kambi()