import os
import random

from playwright.sync_api import sync_playwright

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "prizepicks_lines.json"
STATE_KEY = "tracker_prizepicks_nba"
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_URLS = [url.strip() for url in RAW_PROXY_LIST.replace("\n", ",").split(",") if url.strip()]


def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)


def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)


def get_proxy_settings():
    """Get proxy settings in Playwright format or None for direct connection."""
    if not PROXY_URLS:
        return None
    chosen_url = random.choice(PROXY_URLS)
    try:
        return {"server": chosen_url}
    except Exception as exc:
        print(f"Failed to parse proxy URL: {exc}")
        return None


def scrape_prizepicks():
    try:
        data = None

        with sync_playwright() as playwright:
            proxy_settings = get_proxy_settings()
            browser = None
            
            if proxy_settings:
                try:
                    print(f"Attempting connection with proxy...")
                    browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
                except Exception as proxy_exc:
                    print(f"Proxy connection failed ({proxy_exc}), falling back to direct connection...")
                    browser = None
            
            if not browser:
                browser = playwright.chromium.launch(headless=True)
            
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api.prizepicks.com/projections" in response.url and "league_id=7" in response.url:
                    try:
                        response_json = response.json()
                        if "data" in response_json:
                            data = response_json
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto("https://app.prizepicks.com/board", wait_until="networkidle")
            try:
                page.wait_for_response(
                    lambda response: "api.prizepicks.com/projections" in response.url and "league_id=7" in response.url,
                    timeout=6000,
                )
            except Exception:
                pass
            browser.close()

        if not data:
            print("Could not intercept PrizePicks API data via Playwright.")
            return

        current_lines = {}
        alerts = []
        previous_lines = load_previous_lines()
        players = {}

        for item in data.get("included", []):
            if item.get("type") == "new_player":
                players[item["id"]] = item.get("attributes", {}).get("name")

        for projection in data.get("data", []):
            if projection.get("type") != "projection":
                continue
            attributes = projection.get("attributes", {})
            stat_type = attributes.get("stat_type")
            line = attributes.get("line_score")
            player_id = projection.get("relationships", {}).get("new_player", {}).get("data", {}).get("id")
            player_name = players.get(player_id, "Unknown Player")
            if line is None or player_name == "Unknown Player":
                continue

            unique_key = f"{player_id}_{stat_type}"
            current_lines[unique_key] = {"player": player_name, "stat": stat_type, "line": line}
            if unique_key not in previous_lines:
                continue

            old_line = previous_lines[unique_key]["line"]
            diff = abs(float(line) - float(old_line))
            if diff >= 1.0:
                alerts.append(
                    f"**PRIZEPICKS BUMP ALERT:** {player_name}\n"
                    f"**{stat_type} Moved!**\n"
                    f"Old Line: {old_line} -> **New Line: {line}**"
                )

        save_current_lines(current_lines)
        for message in alerts[:5]:
            post_discord({"embeds": [{"description": message, "color": 10181046}]}, webhook_url=DISCORD_WEBHOOK_URL)
        return {"detail": "prizepicks scrape complete", "count": min(len(alerts), 5), "label": "alerts"}
    except Exception as exc:
        print(f"Error scraping PrizePicks: {exc}")
        return {"detail": f"prizepicks scrape error: {exc}", "count": 0, "label": "alerts"}


if __name__ == "__main__":
    scrape_prizepicks()
