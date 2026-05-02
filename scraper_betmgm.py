import json
import os
import random
import re
import string
from typing import Dict, Optional

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
MAX_PROXY_ATTEMPTS = int(os.getenv("SCRAPER_PROXY_ATTEMPTS", "3"))
BETMGM_URL = "https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004"
BETMGM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
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
    return [None] + shuffled[: max(MAX_PROXY_ATTEMPTS - 1, 0)]


def _proxy_settings(proxy_ip: Optional[str]):
    if not proxy_ip:
        return None
    random_session = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    dynamic_username = f"{PROXY_USERNAME}-session-{random_session}"
    return {
        "server": f"http://{proxy_ip}",
        "username": dynamic_username,
        "password": PROXY_PASSWORD,
    }


def _looks_like_betmgm_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        return False
    return any(isinstance(fixture, dict) and fixture.get("optionMarkets") for fixture in fixtures)


def _decode_possible_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    candidates = [text]
    pre_match = re.search(r"<pre[^>]*>\s*(\{.*\})\s*</pre>", text, flags=re.DOTALL | re.IGNORECASE)
    if pre_match:
        candidates.append(pre_match.group(1))
    next_data_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, flags=re.DOTALL | re.IGNORECASE)
    if next_data_match:
        candidates.append(next_data_match.group(1))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _extract_embedded_payload(html: str):
    patterns = [
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r'window\.__NEXT_DATA__\s*=\s*({.*?});',
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'window\.__NUXT__\s*=\s*({.*?});',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        payload = _decode_possible_json(match.group(1))
        if _looks_like_betmgm_payload(payload):
            return payload
    return None


def _extract_payload_from_page(page):
    captured = {"data": None, "url": None}

    def handle_response(response):
        if captured["data"] is not None:
            return
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type and "javascript" not in content_type:
            return
        try:
            payload = response.json()
        except Exception:
            return
        if _looks_like_betmgm_payload(payload):
            captured["data"] = payload
            captured["url"] = response.url

    page.on("response", handle_response)

    try:
        page.goto(BETMGM_URL, wait_until="domcontentloaded", timeout=25000)
    except Exception:
        print("BetMGM navigation timeout caught gracefully. Checking for data anyway...")

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

    if captured["data"] is None:
        try:
            html = page.content()
            embedded = _extract_embedded_payload(html)
            if embedded:
                captured["data"] = embedded
                captured["url"] = "embedded_page_state"
        except Exception:
            pass

    return captured["data"], captured["url"]


def _fetch_betmgm_payload():
    with sync_playwright() as playwright:
        for proxy_ip in _proxy_candidates():
            proxy_settings = _proxy_settings(proxy_ip)
            proxy_label = proxy_ip or "direct"
            browser = None
            try:
                browser = playwright.chromium.launch(headless=True, proxy=proxy_settings)
                context = browser.new_context(
                    ignore_https_errors=True,
                    user_agent=BETMGM_USER_AGENT,
                )
                page = context.new_page()
                data, source_url = _extract_payload_from_page(page)
                browser.close()
                if data:
                    print(f"BetMGM payload captured via {proxy_label}: {source_url}")
                    return data
                print(f"BetMGM: no usable payload via {proxy_label}.")
            except Exception as exc:
                print(f"BetMGM proxy attempt failed via {proxy_label}: {exc}")
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
        return None


def _build_current_lines(data: dict) -> Dict[str, Dict[str, object]]:
    current_lines = {}
    for fixture in data.get("fixtures", []):
        matchup = fixture.get("name", {"value": "Unknown Matchup"}).get("value")
        event_id = str(fixture.get("id"))

        for option_market in fixture.get("optionMarkets", []):
            market_name = option_market.get("name", {}).get("value", "")
            if "Spread" not in market_name:
                continue
            for outcome in option_market.get("options", []):
                team = outcome.get("name", {}).get("value")
                attributes = outcome.get("attributes", {})
                line = attributes.get("spread") or attributes.get("line")
                if team in (None, "") or line in (None, ""):
                    continue

                unique_key = f"{event_id}_{team}"
                current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line}
    return current_lines


def scrape_betmgm():
    try:
        data = _fetch_betmgm_payload()
        if not data:
            print("Could not capture a usable BetMGM NBA payload.")
            return {"detail": "betmgm scrape no data", "count": 0, "label": "alerts"}

        current_lines = _build_current_lines(data)
        if not current_lines:
            print("BetMGM payload captured, but no spread lines were parsed.")
            return {"detail": "betmgm scrape parsed no spread lines", "count": 0, "label": "alerts"}

        alerts = []
        previous_lines = load_previous_lines()

        for unique_key, current in current_lines.items():
            if unique_key not in previous_lines:
                continue

            old_line = previous_lines[unique_key]["line"]
            diff = abs(float(current["line"]) - float(old_line))
            if diff >= 1.5:
                alerts.append(
                    f"**MGM STEAM ALERT:** {current['matchup']}\n"
                    f"**{current['team']} Spread Moved!**\n"
                    f"Old Line: {old_line} -> **New Line: {current['line']}**"
                )

        save_current_lines(current_lines)
        for message in alerts:
            post_discord({"embeds": [{"description": message, "color": 13611036}]}, webhook_url=DISCORD_WEBHOOK_URL)
        return {
            "detail": f"betmgm scrape complete ({len(current_lines)} lines tracked)",
            "count": len(alerts),
            "label": "alerts",
        }
    except Exception as exc:
        print(f"Error scraping BetMGM: {exc}")
        return {"detail": f"betmgm scrape error: {exc}", "count": 0, "label": "alerts"}


def scrape_mgm():
    return scrape_betmgm()


if __name__ == "__main__":
    scrape_betmgm()
