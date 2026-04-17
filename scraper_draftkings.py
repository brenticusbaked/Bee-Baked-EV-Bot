import os
import random
import string

from playwright.sync_api import sync_playwright
from services.http_client import post_discord

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

def scrape_draftkings():
    try:
        data = None
        with sync_playwright() as playwright:
            proxy_settings = None
            if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
                chosen_ip = random.choice(PROXY_IPS)
                
                # Dynamic Session ID to force a fresh IP
                random_session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                dynamic_username = f"{PROXY_USERNAME}-session-{random_session}"
                
                proxy_settings = {
                    "server": f"http://{chosen_ip}",
                    "username": dynamic_username,
                    "password": PROXY_PASSWORD,
                }

            browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
            
            # Ignore HTTPS errors to bypass proxy SSL blocks
            context = browser.new_context(
                ignore_https_errors=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api/sportscontent/v1/events/42648" in response.url:
                    try:
                        data = response.json()
                    except Exception:
                        pass

            page.on("response", handle_response)
            
            # domcontentloaded to bypass infinite loops
            page.goto("https://sportsbook.draftkings.com/leagues/basketball/nba", wait_until="domcontentloaded")
            
            try:
                page.wait_for_response(lambda response: "api/sportscontent/v1/events/42648" in response.url, timeout=6000)
            except Exception:
                pass
            browser.close()

        if not data:
            print("Could not intercept DraftKings API data via Playwright.")
            return

        alerts = []
        for event in data.get("events", []):
            matchup = event.get("name")
            for market in event.get("markets", []):
                if market.get("name") != "Spread":
                    continue
                for outcome in market.get("outcomes", []):
                    alerts.append(
                        f"**DK Movement** | {matchup}: "
                        f"{outcome.get('label')} {outcome.get('line')} ({outcome.get('oddsAmerican')})"
                    )

        if alerts:
            post_discord({"content": "\n".join(alerts)}, webhook_url=DISCORD_WEBHOOK_URL)
        return {"detail": "draftkings scrape complete", "count": len(alerts), "label": "alerts"}
        
    except Exception as exc:
        print(f"Error scraping DraftKings: {exc}")
        return {"detail": f"draftkings scrape error: {exc}", "count": 0, "label": "alerts"}

if __name__ == "__main__":
    scrape_draftkings()
