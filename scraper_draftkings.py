import os
import requests
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "dk_lines.json"

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
        data = None
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api/sportscontent/v1/events/42648" in response.url:
                    try:
                        resp_json = response.json()
                        if 'events' in resp_json:
                            data = resp_json
                    except:
                        pass

            page.on("response", handle_response)
            
            # Navigate to the frontend page to trigger the background API request naturally
            print("Navigating to DraftKings NBA page...")
            page.goto("https://sportsbook.draftkings.com/leagues/basketball/nba", wait_until="networkidle")
            page.wait_for_timeout(5000)
            
            browser.close()

        if not data:
            print("Could not intercept DraftKings API data via Playwright.")
            return

        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        events = data.get('events', [])
        for event in events:
            matchup = event.get('name', 'Unknown Matchup')
            event_id = event.get('eventId')
            
            for market in event.get('markets', []):
                if market.get('name') == 'Spread':
                    for outcome in market.get('outcomes', []):
                        team = outcome.get('label')
                        line = outcome.get('line')
                        price = outcome.get('oddsAmerican')
                        
                        unique_key = f"{event_id}_{team}"
                        current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line, "price": price}
                        
                        if unique_key in previous_lines:
                            old_line = previous_lines[unique_key]['line']
                            
                            if old_line is not None and line is not None:
                                diff = abs(float(line) - float(old_line))
                                if diff >= 1.5:
                                    alerts.append(
                                        f"📈 **STEAM ALERT:** {matchup}\n"
                                        f"**{team} Spread Moved!**\n"
                                        f"Old Line: {old_line} ➡️ **New Line: {line}** ({price})"
                                    )
                                    
        save_current_lines(current_lines)
        
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                payload = {"embeds": [{"description": msg, "color": 16753920}]}
                requests.post(DISCORD_WEBHOOK_URL, json=payload)
                print("Alert sent for line movement.")
        else:
            print("DraftKings Scrape Complete: No major line movement detected.")

    except Exception as e:
        print(f"Error scraping DraftKings: {e}")

if __name__ == "__main__":
    scrape_draftkings()