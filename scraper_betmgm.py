import os
import requests
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "mgm_lines.json"

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
        data = None
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api/v1/fixtures" in response.url and "competitionId=6004" in response.url:
                    try:
                        resp_json = response.json()
                        if 'fixtures' in resp_json:
                            data = resp_json
                    except:
                        pass

            page.on("response", handle_response)
            
            print("Navigating to BetMGM NBA page...")
            page.goto("https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004", wait_until="networkidle")
            page.wait_for_timeout(5000)
            
            browser.close()

        if not data:
            print("Could not intercept BetMGM API data via Playwright.")
            return

        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        for fixture in data.get('fixtures', []):
            matchup = fixture.get('name', {'value': 'Unknown Matchup'}).get('value')
            event_id = str(fixture.get('id'))
            
            for option in fixture.get('optionMarkets', []):
                if 'Spread' in option.get('name', {}).get('value', ''):
                    for outcome in option.get('options', []):
                        team = outcome.get('name', {}).get('value')
                        
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
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 13611036}]})
                print("BetMGM Alert sent.")
        else:
            print("BetMGM Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping BetMGM: {e}")

if __name__ == "__main__":
    scrape_betmgm()