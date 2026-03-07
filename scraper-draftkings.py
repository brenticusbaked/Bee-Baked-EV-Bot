import os
import requests
import json
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# DraftKings Internal NBA API Endpoint
DK_URL = "https://sportsbook.draftkings.com/api/sportscontent/v1/events/42648?format=json"
TRACKER_FILE = "dk_lines.json"

# Spoofed headers to bypass basic Cloudflare/bot protection
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

def scrape_draftkings():
    try:
        # Sneak past the bouncer
        res = requests.get(DK_URL, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Blocked by DraftKings! Status Code: {res.status_code}")
            return
            
        data = res.json()
        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        # Navigate DraftKings' chaotic JSON structure
        events = data.get('events', [])
        for event in events:
            matchup = event.get('name', 'Unknown Matchup')
            event_id = event.get('eventId')
            
            # Find the main point spread market
            for market in event.get('markets', []):
                if market.get('name') == 'Spread':
                    for outcome in market.get('outcomes', []):
                        team = outcome.get('label')
                        line = outcome.get('line')
                        price = outcome.get('oddsAmerican')
                        
                        unique_key = f"{event_id}_{team}"
                        current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line, "price": price}
                        
                        # Compare against previous run to find STEAM
                        if unique_key in previous_lines:
                            old_line = previous_lines[unique_key]['line']
                            
                            # If the point spread moved by 1.5 points or more, someone just hammered it
                            if old_line is not None and line is not None:
                                diff = abs(float(line) - float(old_line))
                                if diff >= 1.5:
                                    alerts.append(
                                        f"📈 **STEAM ALERT:** {matchup}\n"
                                        f"**{team} Spread Moved!**\n"
                                        f"Old Line: {old_line} ➡️ **New Line: {line}** ({price})"
                                    )
                                    
        save_current_lines(current_lines)
        
        # Send to Discord if sharp movement detected
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                payload = {
                    "embeds": [{"description": msg, "color": 16753920}] # Warning Orange
                }
                requests.post(DISCORD_WEBHOOK_URL, json=payload)
                print("Alert sent for line movement.")
        else:
            print("DraftKings Scrape Complete: No major line movement detected.")

    except Exception as e:
        print(f"Error scraping DraftKings: {e}")

if __name__ == "__main__":
    scrape_draftkings()