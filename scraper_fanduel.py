import os
import random
from typing import Dict, Optional

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
FANDUEL_URL = "https://sportsbook.fanduel.com/basketball/nba"
FANDUEL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)


def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)


def _proxy_candidates():
    if not (PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD):
        return [None]
    shuffled = PROXY_IPS[:]
    random.shuffle(shuffled)
    return [None] + shuffled


def _proxy_settings(proxy_ip: Optional[str]):
    if not proxy_ip:
        return None
    return {
        "server": f"http://{proxy_ip}",
        "username": PROXY_USERNAME,
        "password": PROXY_PASSWORD,
    }


def _looks_like_fanduel_nba_payload(response_url: str, payload: dict) -> bool:
    container = _find_market_container(payload)
    if not container:
        return False
    markets = container.get("markets", {})
    if not isinstance(markets, dict) or not markets:
        return False
    if "basketball" not in response_url.lower() and "nba" not in response_url.lower() and "eventTypeId=7522" not in response_url:
        market_names = [
            str(market.get("marketName", "")).lower()
            for market in markets.values()
            if isinstance(market, dict)
        ]
        if not any("spread" in name for name in market_names):
            return False
    return True


def _find_market_container(payload):
    if isinstance(payload, dict):
        attachments = payload.get("attachments")
        if isinstance(attachments, dict):
            markets = attachments.get("markets")
            events = attachments.get("events")
            if isinstance(markets, dict) and isinstance(events, dict):
                return attachments

        markets = payload.get("markets")
        events = payload.get("events")
        if isinstance(markets, dict) and isinstance(events, dict):
            return payload

        for value in payload.values():
            found = _find_market_container(value)
            if found:
                return found

    if isinstance(payload, list):
        for item in payload:
            found = _find_market_container(item)
            if found:
                return found
    return None


def _extract_payload_from_page(page):
    captured = {"data": None, "url": None}

    def handle_response(response):
        if captured["data"] is not None:
            return
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type and "javascript" not in content_type:
            return
        url = response.url
        try:
            payload = response.json()
        except Exception:
            return
        if _looks_like_fanduel_nba_payload(url, payload):
            captured["data"] = payload
            captured["url"] = url

    page.on("response", handle_response)

    try:
        page.goto(FANDUEL_URL, wait_until="domcontentloaded", timeout=25000)
    except Exception:
        print("FanDuel navigation timeout caught gracefully. Checking for data anyway...")

    for _ in range(3):
        if captured["data"] is not None:
            break
        try:
            page.wait_for_timeout(3000)
        except Exception:
            break
        try:
            page.mouse.wheel(0, 2500)
        except Exception:
            pass

    return captured["data"], captured["url"]


def _fetch_fanduel_payload():
    with sync_playwright() as playwright:
        for proxy_ip in _proxy_candidates():
            proxy_settings = _proxy_settings(proxy_ip)
            proxy_label = proxy_ip or "direct"
            browser = None
            try:
                browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
                context = browser.new_context(user_agent=FANDUEL_USER_AGENT)
                page = context.new_page()
                data, source_url = _extract_payload_from_page(page)
                browser.close()
                if data:
                    print(f"FanDuel payload captured via {proxy_label}: {source_url}")
                    return data
                print(f"FanDuel: no usable payload via {proxy_label}.")
            except Exception as exc:
                print(f"FanDuel proxy attempt failed via {proxy_label}: {exc}")
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
        return None


def _build_current_lines(data: dict) -> Dict[str, Dict[str, object]]:
    current_lines = {}
    container = _find_market_container(data) or {}
    markets = container.get("markets", {})
    events = container.get("events", {})

    for market_data in markets.values():
        if not isinstance(market_data, dict):
            continue
        market_name = str(market_data.get("marketName", "")).lower()
        if "spread" not in market_name:
            continue

        event_id = str(market_data.get("eventId"))
        matchup = events.get(event_id, {}).get("name", "Unknown Matchup")

        for runner in market_data.get("runners", []):
            team = runner.get("runnerName")
            line = runner.get("handicap")
            if team in (None, "") or line in (None, ""):
                continue

            unique_key = f"{event_id}_{team}"
            current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line}

    return current_lines


def scrape_fanduel():
    try:
        data = _fetch_fanduel_payload()
        if not data:
            print("Could not capture a usable FanDuel NBA payload.")
            return {"detail": "fanduel scrape no data", "count": 0, "label": "alerts"}

        current_lines = _build_current_lines(data)
        if not current_lines:
            print("FanDuel payload captured, but no spread lines were parsed.")
            return {"detail": "fanduel scrape parsed no spread lines", "count": 0, "label": "alerts"}

        alerts = []
        previous_lines = load_previous_lines()

        for unique_key, current in current_lines.items():
            if unique_key not in previous_lines:
                continue

            old_line = previous_lines[unique_key]["line"]
            diff = abs(float(current["line"]) - float(old_line))
            if diff >= 1.5:
                alerts.append(
                    f"**FD STEAM ALERT:** {current['matchup']}\n"
                    f"**{current['team']} Spread Moved!**\n"
                    f"Old Line: {old_line} -> **New Line: {current['line']}**"
                )

        save_current_lines(current_lines)
        for message in alerts:
            post_discord({"embeds": [{"description": message, "color": 15615}]}, webhook_url=DISCORD_WEBHOOK_URL)
        return {
            "detail": f"fanduel scrape complete ({len(current_lines)} lines tracked)",
            "count": len(alerts),
            "label": "alerts",
        }
    except Exception as exc:
        print(f"Error scraping FanDuel: {exc}")
        return {"detail": f"fanduel scrape error: {exc}", "count": 0, "label": "alerts"}


if __name__ == "__main__":
    scrape_fanduel()
