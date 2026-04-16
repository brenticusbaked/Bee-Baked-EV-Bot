import os
import requests
import json
import random
from datetime import datetime
from playwright.sync_api import sync_playwright

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "fd_lines.json"

# 1. Pull all three secrets
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")

# 2. Convert the raw text secret into a clean Python array
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace('\n', ',').split(',') if ip.strip()]

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
        data = None
        
        with sync_playwright() as p:
            proxy_settings = None
            
            # 3. Pick a random IP and build the credentials
            if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
                chosen_ip = random.choice(PROXY_IPS)
                proxy_settings = {
                    "server": f"http://{chosen_ip}",
                    "username": PROXY_USERNAME,
                    "password": PROXY_PASSWORD
                }
            
            # 4. Launch the browser with the fully assembled proxy
            browser = p.chromium.launch(
                headless=True,
                proxy=proxy_settings
            )
            
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                # Unique to FanDuel:
                if "api/content-managed-page" in response.url and "eventTypeId=7522" in response.url:
                    try:
                        resp_json = response.json()
                        if 'attachments' in resp_json:
                            data = resp_json
                    except:
                        pass

            page.on("response", handle_response)
            
            print(f"Navigating to FanDuel via Webshare Proxy...")
            page.goto("https://sportsbook.fanduel.com/basketball/nba", wait_until="networkidle")
            page.wait_for_timeout(5000)
            
            browser.close()

        if not data:
            print("Could not intercept FanDuel API data via Playwright.")
            return

        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        
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
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 15615}]})
                print("FanDuel Alert sent.")
        else:
            print("FanDuel Scrape Complete: No major line movement.")

    except Exception as e:
        print(f"Error scraping FanDuel: {e}")

if __name__ == "__main__":
    scrape_fanduel()