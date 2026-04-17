import os
import random

from playwright.sync_api import sync_playwright

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "mgm_lines.json"
STATE_KEY = "tracker_betmgm_nba"
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]


def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)


def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)


def scrape_betmgm():
    try:
        data = None

        with sync_playwright() as playwright:
            # 1. Setup the Proxy Settings Dictionary
            proxy_settings = None
            if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
                chosen_ip = random.choice(PROXY_IPS)
                proxy_settings = {
                    "server": f"http://{chosen_ip}",
                    "username": PROXY_USERNAME,
                    "password": PROXY_PASSWORD,
                }

            # 2. Launch the browser WITH the proxy settings
            browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
            
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api/v1/fixtures" in response.url and "competitionId=6004" in response.url:
                    try:
                        response_json = response.json()
                        if "fixtures" in response_json:
                            data = response_json
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto("https://...", wait_until="domcontentloaded")
            
            try:
                page.wait_for_response(
                    lambda response: "api/v1/fixtures" in response.url and "competitionId=6004" in response.url,
                    timeout=6000,
                )
            except Exception:
                pass
                
            browser.close()

        if not data:
            print("Could not intercept BetMGM API data via Playwright.")
            return

        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()

        for fixture in data.get("fixtures", []):
            matchup = fixture.get("name", {"value": "Unknown Matchup"}).get("value")
            event_id = str(fixture.get("id"))

            for option_market in fixture.get("optionMarkets", []):
                if "Spread" not in option_market.get("name", {}).get("value", ""):
                    continue
                for outcome in option_market.get("options", []):
                    team = outcome.get("name", {}).get("value")
                    attributes = outcome.get("attributes", {})
                    line = attributes.get("spread") or attributes.get("line")
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
                            f"**MGM STEAM ALERT:** {matchup}\n"
                            f"**{team} Spread Moved!**\n"
                            f"Old Line: {old_line} -> **New Line: {line}**"
                        )

        save_current_lines(current_lines)
        for message in alerts:
            post_discord({"embeds": [{"description": message, "color": 13611036}]}, webhook_url=DISCORD_WEBHOOK_URL)
        return {"detail": "betmgm scrape complete", "count": len(alerts), "label": "alerts"}
        
    except Exception as exc:
        print(f"Error scraping BetMGM: {exc}")
        return {"detail": f"betmgm scrape error: {exc}", "count": 0, "label": "alerts"}

if __name__ == "__main__":
    scrape_betmgm()
