import os
import random
import string

from playwright.sync_api import sync_playwright
from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "fd_lines.json"
STATE_KEY = "tracker_fanduel_nba"
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)

def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)

def scrape_fanduel():
    try:
        data = None
        with sync_playwright() as playwright:
            proxy_settings = None
            if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
                chosen_ip = random.choice(PROXY_IPS)
                
                # --- FIX 3: Dynamic Session ID to force a fresh IP ---
                random_session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                dynamic_username = f"{PROXY_USERNAME}-session-{random_session}"
                
                proxy_settings = {
                    "server": f"http://{chosen_ip}",
                    "username": dynamic_username,
                    "password": PROXY_PASSWORD,
                }

            browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
            
            # --- FIX 1: Ignore HTTPS errors ---
            context = browser.new_context(
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api/content-managed-page" in response.url and "eventTypeId=7522" in response.url:
                    try:
                        response_json = response.json()
                        if "attachments" in response_json:
                            data = response_json
                    except Exception:
                        pass

            page.on("response", handle_response)
            
            # --- FIX 2: domcontentloaded ---
            page.goto("https://sportsbook.fanduel.com/basketball/nba", wait_until="domcontentloaded")
            
            try:
                page.wait_for_response(
                    lambda response: "api/content-managed-page" in response.url and "eventTypeId=7522" in response.url,
                    timeout=6000,
                )
            except Exception:
                pass
            browser.close()

        if not data:
            print("Could not intercept FanDuel API data via Playwright.")
            return

        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        markets = data.get("attachments", {}).get("markets", {})
        events = data.get("attachments", {}).get("events", {})

        for market_data in markets.values():
            if market_data.get("marketName") != "Spread":
                continue
            event_id = str(market_data.get("eventId"))
            matchup = events.get(event_id, {}).get("name", "Unknown Matchup")

            for runner in market_data.get("runners", []):
                team = runner.get("runnerName")
                line = runner.get("handicap")
                if line is None:
                    continue

                unique_key = f"{event_id}_{team}"
                current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line}
                if unique_key not in previous_lines:
                    continue

                old_line = previous_lines[unique_key]["line"]
                diff = abs(float(line) - float(old_line))
                if diff >= 1.5:
                    alerts.append(
                        f"**FD STEAM ALERT:** {matchup}\n"
                        f"**{team} Spread Moved!**\n"
                        f"Old Line: {old_line} -> **New Line: {line}**"
                    )

        save_current_lines(current_lines)
        for message in alerts:
            post_discord({"embeds": [{"description": message, "color": 15615}]}, webhook_url=DISCORD_WEBHOOK_URL)
        return {"detail": "fanduel scrape complete", "count": len(alerts), "label": "alerts"}
        
    except Exception as exc:
        print(f"Error scraping FanDuel: {exc}")
        return {"detail": f"fanduel scrape error: {exc}", "count": 0, "label": "alerts"}

if __name__ == "__main__":
    scrape_fanduel()
