import os
import requests
import json
import random
from datetime import datetime
from playwright.sync_api import sync_playwright

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "dk_lines.json"

# Pull all three secrets
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")

# Convert the raw text secret into a clean Python array
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace('\n', ',').split(',') if ip.strip()]

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
            proxy_settings = None
            
            # Pick a random IP and build the credentials
            if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
                chosen_ip = random.choice(PROXY_IPS)
                proxy_settings = {
                    "server": f"http://{chosen_ip}",
                    "username": PROXY_USERNAME,
                    "password": PROXY_PASSWORD
                }
            
            # Launch the browser with the fully assembled proxy
            browser = p.chromium.launch(
                headless=True,
                proxy=proxy_settings
            )
            
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
            
            print("Navigating to DraftKings via Webshare Proxy...")
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
                        current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line