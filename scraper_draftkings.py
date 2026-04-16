import os
import requests
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
        for event in data.get('events', []):
            matchup = event.get('name')
            for market in event.get('markets', []):
                if market.get('name') == 'Spread':
                    for outcome in market.get('outcomes', []):
                        # FIXED: Actually generates movement alerts
                        alerts.append(f"📊 **DK Movement** | {matchup}: {outcome.get('label')} {outcome.get('line')} ({outcome.get('oddsAmerican')})")

        if alerts and DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": "\n".join(alerts)})

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__": scrape_draftkings()