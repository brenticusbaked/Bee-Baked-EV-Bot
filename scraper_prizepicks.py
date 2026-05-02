import os
import random
import json
from typing import Dict

from playwright.sync_api import sync_playwright

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "prizepicks_lines.json"
STATE_KEY = "tracker_prizepicks_nba"
PRIZEPICKS_LEAGUE_ID = os.getenv("PRIZEPICKS_NBA_LEAGUE_ID", "7")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]


def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)


def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)


def _fetch_prizepicks_data() -> dict:
    with sync_playwright() as playwright:
        proxy_settings = None
        if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
            chosen_ip = random.choice(PROXY_IPS)
            proxy_settings = {
                "server": f"http://{chosen_ip}",
                "username": PROXY_USERNAME,
                "password": PROXY_PASSWORD,
            }

        browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        context.set_extra_http_headers(
            {
                "Accept": "application/json",
                "Origin": "https://app.prizepicks.com",
                "Referer": "https://app.prizepicks.com/",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        page = context.new_page()
        try:
            page.goto("https://app.prizepicks.com/board", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
            api_url = (
                "https://api.prizepicks.com/projections"
                f"?league_id={PRIZEPICKS_LEAGUE_ID}&per_page=250&single_stat=true"
            )
            response = page.goto(api_url, wait_until="domcontentloaded", timeout=25000)
            if response is None:
                raise RuntimeError("PrizePicks API navigation returned no response")
            payload = {
                "ok": response.ok,
                "status": response.status,
                "text": response.text(),
            }
        finally:
            browser.close()

    if not payload.get("ok"):
        raise RuntimeError(f"PrizePicks browser-context fetch failed with status {payload.get('status')}")

    return json.loads(payload.get("text", "{}"))


def _player_map(payload: dict) -> Dict[str, str]:
    players = {}
    for item in payload.get("included", []):
        if item.get("type") == "new_player":
            players[str(item.get("id"))] = item.get("attributes", {}).get("name", "Unknown Player")
    return players


def scrape_prizepicks():
    try:
        data = _fetch_prizepicks_data()
    except Exception as exc:
        print(f"PrizePicks browser-context fetch failed: {exc}")
        return {"detail": f"prizepicks scrape error: {exc}", "count": 0, "label": "alerts"}

    if "data" not in data:
        print("PrizePicks fetch returned no projection payload.")
        return {"detail": "prizepicks scrape no data", "count": 0, "label": "alerts"}

    current_lines = {}
    alerts = []
    previous_lines = load_previous_lines()
    players = _player_map(data)

    for projection in data.get("data", []):
        if projection.get("type") != "projection":
            continue

        attributes = projection.get("attributes", {})
        stat_type = attributes.get("stat_type")
        line = attributes.get("line_score")
        player_id = str(
            projection.get("relationships", {})
            .get("new_player", {})
            .get("data", {})
            .get("id", "")
        )
        player_name = players.get(player_id, "Unknown Player")
        if line is None or player_name == "Unknown Player" or not stat_type:
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
    return {
        "detail": f"prizepicks scrape complete ({len(current_lines)} lines tracked)",
        "count": min(len(alerts), 5),
        "label": "alerts",
    }


def scrape_pp():
    return scrape_prizepicks()


if __name__ == "__main__":
    scrape_prizepicks()
