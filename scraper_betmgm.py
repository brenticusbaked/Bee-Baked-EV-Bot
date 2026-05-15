import json
import os
import random
import re
import string
from html import unescape
from typing import Dict, Optional

from playwright.sync_api import sync_playwright

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord, request


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "mgm_lines.json"
STATE_KEY = "tracker_betmgm_nba"
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]
MAX_PROXY_ATTEMPTS = int(os.getenv("BETMGM_SCRAPER_PROXY_ATTEMPTS", os.getenv("SCRAPER_PROXY_ATTEMPTS", "2")))
NAV_TIMEOUT_MS = int(os.getenv("BETMGM_NAV_TIMEOUT_MS", "15000"))
WAIT_CYCLES = int(os.getenv("BETMGM_WAIT_CYCLES", "2"))
WAIT_MS = int(os.getenv("BETMGM_WAIT_MS", "2000"))
BETMGM_URL = "https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004"
BETMGM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MATCHUP_RECORD_RE = re.compile(r"^(?P<team1>.+?)\s+\d{1,2}-\d{1,2}\s+(?P<team2>.+?)\s+\d{1,2}-\d{1,2}$")
SPREAD_TOKEN_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")


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
    container = _find_fixture_container(payload)
    if not container:
        return False
    fixtures = container.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        return False
    return any(isinstance(fixture, dict) and fixture.get("optionMarkets") for fixture in fixtures)


def _find_fixture_container(payload):
    if isinstance(payload, dict):
        fixtures = payload.get("fixtures")
        if isinstance(fixtures, list) and fixtures:
            return payload

        for value in payload.values():
            found = _find_fixture_container(value)
            if found:
                return found

    if isinstance(payload, list):
        for item in payload:
            found = _find_fixture_container(item)
            if found:
                return found
    return None


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


def _extract_rendered_text_from_html(html: str) -> str:
    cleaned = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    cleaned = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", cleaned)
    cleaned = re.sub(
        r"(?i)</?(?:p|div|section|article|main|aside|header|footer|li|ul|ol|table|tr|td|th|tbody|thead|h\d|a|span|button|br)[^>]*>",
        "\n",
        cleaned,
    )
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = cleaned.replace("\xa0", " ")
    return cleaned


def _parse_lines_from_rendered_text(rendered_text: str) -> Dict[str, Dict[str, object]]:
    current_lines: Dict[str, Dict[str, object]] = {}
    lines = [line.strip() for line in rendered_text.splitlines() if line.strip()]

    for index, line in enumerate(lines):
        matchup_match = MATCHUP_RECORD_RE.match(line)
        if not matchup_match:
            continue

        upcoming_tokens = lines[index + 1 : index + 9]
        spread_tokens = [token for token in upcoming_tokens if SPREAD_TOKEN_RE.match(token)]
        if len(spread_tokens) < 2:
            continue

        team1 = matchup_match.group("team1").strip()
        team2 = matchup_match.group("team2").strip()
        matchup = f"{team1} @ {team2}"
        matchup_key = re.sub(r"[^a-z0-9]+", "_", matchup.lower()).strip("_")

        current_lines[f"{matchup_key}_{team1.lower()}"] = {
            "matchup": matchup,
            "team": team1,
            "line": spread_tokens[0],
        }
        current_lines[f"{matchup_key}_{team2.lower()}"] = {
            "matchup": matchup,
            "team": team2,
            "line": spread_tokens[1],
        }

    return current_lines


def _fetch_betmgm_direct_lines() -> Dict[str, Dict[str, object]]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://sports.betmgm.com/",
        "User-Agent": BETMGM_USER_AGENT,
    }

    try:
        response = request(
            "GET",
            BETMGM_URL,
            headers=headers,
            timeout=20,
            retry_on_429=False,
        )
        rendered_text = _extract_rendered_text_from_html(response.text)
        current_lines = _parse_lines_from_rendered_text(rendered_text)
        if current_lines:
            print("BetMGM lines parsed via direct HTML page.")
            return current_lines
    except Exception as exc:
        print(f"BetMGM direct HTML fetch failed: {exc}")
    return {}


def _extract_payload_from_page(page):
    captured = {"data": None, "url": None}

    def handle_response(response):
        if captured["data"] is not None:
            return
        content_type = response.headers.get("content-type", "").lower()
        if "json" not in content_type and "javascript" not in content_type and "text" not in content_type:
            return
        try:
            payload = response.json()
        except Exception:
            try:
                payload = _decode_possible_json(response.text())
            except Exception:
                return
        if _looks_like_betmgm_payload(payload):
            captured["data"] = payload
            captured["url"] = response.url

    page.on("response", handle_response)

    try:
        page.goto(BETMGM_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception:
        print("BetMGM navigation timeout caught gracefully. Checking for data anyway...")

    for _ in range(WAIT_CYCLES):
        if captured["data"] is not None:
            break
        try:
            page.wait_for_timeout(WAIT_MS)
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

    rendered_text = ""
    try:
        rendered_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        try:
            rendered_text = _extract_rendered_text_from_html(page.content())
        except Exception:
            rendered_text = ""

    return captured["data"], captured["url"], rendered_text


def _fetch_betmgm_snapshot():
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
                data, source_url, rendered_text = _extract_payload_from_page(page)
                browser.close()
                if data:
                    print(f"BetMGM payload captured via {proxy_label}: {source_url}")
                    return data, rendered_text
                if _parse_lines_from_rendered_text(rendered_text):
                    print(f"BetMGM lines parsed via {proxy_label}: rendered_page_text")
                    return None, rendered_text
                print(f"BetMGM: no usable payload or rendered lines via {proxy_label}.")
            except Exception as exc:
                print(f"BetMGM proxy attempt failed via {proxy_label}: {exc}")
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
        return None, ""


def _build_current_lines(data: dict) -> Dict[str, Dict[str, object]]:
    current_lines = {}
    container = _find_fixture_container(data) or {}
    for fixture in container.get("fixtures", []):
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
        current_lines = _fetch_betmgm_direct_lines()
        if not current_lines:
            data, rendered_text = _fetch_betmgm_snapshot()
            if data:
                current_lines = _build_current_lines(data)
            if not current_lines and rendered_text:
                current_lines = _parse_lines_from_rendered_text(rendered_text)

        if not current_lines:
            print("Could not capture usable BetMGM NBA lines.")
            return {"detail": "betmgm scrape no data", "count": 0, "label": "alerts"}

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
