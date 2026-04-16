import os
import requests
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "prizepicks_lines.json"

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
        data = None
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api.prizepicks.com/projections" in response.url and "league_id=7" in response.url:
                    try:
                        resp_json = response.json()
                        if 'data' in resp_json:
                            data = resp_json
                    except:
                        pass

            page.on("response", handle_response)
            
            print("Navigating to PrizePicks Web App...")
            page.goto("https://app.prizepicks.com/board", wait_until="networkidle")
            page.wait_for_timeout(5000)
            
            browser.close()

        if not data:
            print("Could not intercept PrizePicks API data via Playwright.")
            return
            
        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
        players = {}
        for item in data.get('included', []):
            if item.get('type') == 'new_player':
                players[item['id']] = item.get('attributes', {}).get('name')
                
        for proj in data.get('data', []):
            if proj.get('type') == 'projection':
                attr = proj.get('attributes', {})
                stat_type = attr.get('stat_type')
                line = attr.get('line_score')
                
                player_id = proj.get('relationships', {}).get('new_player', {}).get('data', {}).get('id')
                player_name = players.get(player_id, "Unknown Player")
                
                if line is not None and player_name != "Unknown Player":
                    unique_key = f"{player_id}_{stat_type}"
                    current_lines[unique_key] = {"player": player_name, "stat": stat_type, "line": line}
                    
                    if unique_key in previous_lines:
                        old_line = previous_lines[unique_key]['line']
                        
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
            for msg in alerts[:5]:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 10181046}]})
                print("PrizePicks Alert sent.")
        else:
            print("PrizePicks Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping PrizePicks: {e}")

if __name__ == "__main__":
    scrape_prizepicks()