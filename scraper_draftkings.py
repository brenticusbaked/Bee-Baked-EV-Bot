import os
import requests
import json
from playwright.sync_api import sync_playwright

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def scrape_draftkings():
    try:
        data = None
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0")
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api/sportscontent/v1/events/42648" in response.url:
                    try: data = response.json()
                    except: pass

            page.on("response", handle_response)
            page.goto("https://sportsbook.draftkings.com/leagues/basketball/nba", wait_until="networkidle")
            page.wait_for_timeout(5000)
            browser.close()

        if not data: return

        alerts = []
        events = data.get('events', [])
        for event in events:
            matchup = event.get('name')
            for market in event.get('markets', []):
                if market.get('name') == 'Spread':
                    for outcome in market.get('outcomes', []):
                        team = outcome.get('label')
                        line = outcome.get('line')
                        price = outcome.get('oddsAmerican')
                        # Logic to detect movements would go here
                        print(f"DK {matchup}: {team} {line} ({price})")

        if alerts and DISCORD_WEBHOOK_URL:
            for a in alerts: requests.post(DISCORD_WEBHOOK_URL, json={"content": a})

    except Exception as e:
        print(f"Error scraping DK: {e}")

if __name__ == "__main__": scrape_draftkings()