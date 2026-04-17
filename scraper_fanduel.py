import os
import random

from playwright.sync_api import sync_playwright

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "fd_lines.json"
STATE_KEY = "tracker_fanduel_nba"
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
        # URL format: http://username:password@host:port/
        return {"server": chosen_url}
    except Exception as exc:
        print(f"Failed to parse proxy URL: {exc}")
        return None


def scrape_fanduel():
    try:
        data = None

        with sync_playwright() as playwright:
            # Try with proxy first
            proxy_settings = get_proxy_settings()
            browser = None
            
            if proxy_settings:
                try:
                    print(f"Attempting connection with proxy...")
                    browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
                except Exception as proxy_exc:
                    print(f"Proxy connection failed ({proxy_exc}), falling back to direct connection...")
                    browser = None
            
            # Fall back to direct connection if no proxy or proxy failed
            if not browser:
                browser = playwright.chromium.launch(headless=True)
            
            context = browser.new_context(
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
            page.goto("https://sportsbook.fanduel.com/basketball/nba", wait_until="networkidle")
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