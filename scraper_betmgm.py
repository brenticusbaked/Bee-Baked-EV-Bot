import json
import os
import random
import re
import string
from html import unescape
from typing import Dict, Optional
from urllib.parse import quote, urljoin, urlparse

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
DIRECT_TIMEOUT_MS = int(os.getenv("BETMGM_DIRECT_TIMEOUT_MS", "8000"))
LAUNCH_TIMEOUT_MS = int(os.getenv("BETMGM_LAUNCH_TIMEOUT_MS", "8000"))
NAV_TIMEOUT_MS = int(os.getenv("BETMGM_NAV_TIMEOUT_MS", "15000"))
WAIT_CYCLES = int(os.getenv("BETMGM_WAIT_CYCLES", "2"))
WAIT_MS = int(os.getenv("BETMGM_WAIT_MS", "2000"))
ENABLE_BROWSER_FALLBACK = os.getenv("BETMGM_ENABLE_BROWSER_FALLBACK", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
BETMGM_URL = "https://www.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004"
BETMGM_NBA_PATH = "/en/sports/basketball-7/betting/usa-9/nba-6004"
BETMGM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MATCHUP_RECORD_RE = re.compile(r"^(?P<team1>.+?)\s+\d{1,2}-\d{1,2}\s+(?P<team2>.+?)\s+\d{1,2}-\d{1,2}$")
SPREAD_TOKEN_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")
ODDS_DECIMAL_RE = re.compile(r"^\d+(?:\.\d+)?$")


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


def _browser_proxy_candidates():
    return [proxy_ip for proxy_ip in _proxy_candidates() if proxy_ip]


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


def _request_proxy_kwargs(proxy_ip: Optional[str]):
    if not proxy_ip or not (PROXY_USERNAME and PROXY_PASSWORD):
        return {}
    encoded_username = quote(PROXY_USERNAME, safe="")
    encoded_password = quote(PROXY_PASSWORD, safe="")
    proxy_url = f"http://{encoded_username}:{encoded_password}@{proxy_ip}"
    return {"proxies": {"http": proxy_url, "https": proxy_url}}


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

    if current_lines:
        return current_lines

    blocked_prefixes = (
        "today",
        "tomorrow",
        "game ",
        "all wagers",
        "matches",
        "futures",
        "specials",
        "basketball",
        "nba",
        "money",
        "total",
        "spread",
    )

    for index, line in enumerate(lines):
        if line.lower() != "spread":
            continue
        if index + 2 >= len(lines):
            continue
        if lines[index + 1].lower() != "total" or lines[index + 2].lower() != "money":
            continue

        block = lines[index + 3 : index + 20]
        if not block:
            continue

        matchup_line = None
        for token in block:
            token_lower = token.lower()
            if token_lower.startswith(blocked_prefixes):
                continue
            if SPREAD_TOKEN_RE.match(token) or ODDS_DECIMAL_RE.match(token):
                continue
            if token.startswith("O ") or token.startswith("U "):
                continue
            if len(token) < 6 or not re.search(r"[A-Za-z]", token):
                continue
            matchup_line = token
            break

        if not matchup_line:
            continue

        matchup_match = MATCHUP_RECORD_RE.match(matchup_line)
        if not matchup_match:
            continue

        spread_tokens = [token for token in block if SPREAD_TOKEN_RE.match(token)]
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


def _debug_rendered_text_preview(rendered_text: str) -> str:
    lines = [line.strip() for line in rendered_text.splitlines() if line.strip()]
    if not lines:
        return "no rendered text"

    for index, line in enumerate(lines):
        if line.lower() == "spread":
            preview = lines[max(index - 2, 0) : min(index + 14, len(lines))]
            return " | ".join(preview[:12])

    return " | ".join(lines[:12])


def _state_select_page_detected(rendered_text: str) -> bool:
    lowered = rendered_text.lower()
    return "where are you playing from?" in lowered or "select from available locations" in lowered


def _candidate_betmgm_urls_from_html(html: str) -> list[str]:
    href_matches = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    candidates = []
    for href in href_matches:
        href = href.strip()
        if not href:
            continue
        absolute = urljoin(BETMGM_URL, href)
        parsed = urlparse(absolute)
        if "betmgm.com" not in parsed.netloc:
            continue
        candidates.append(absolute)

        if parsed.path != BETMGM_NBA_PATH:
            rebuilt = parsed._replace(path=BETMGM_NBA_PATH, params="", query="", fragment="").geturl()
            candidates.append(rebuilt)

    seen = set()
    deduped = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped[:8]


def _fetch_betmgm_direct_lines() -> Dict[str, Dict[str, object]]:
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://sports.betmgm.com/",
        "User-Agent": BETMGM_USER_AGENT,
    }

    for proxy_ip in _proxy_candidates():
        proxy_label = proxy_ip or "direct"
        try:
            request_kwargs = _request_proxy_kwargs(proxy_ip)

            def fetch_lines_from_url(target_url: str, label_suffix: str = "") -> Dict[str, Dict[str, object]]:
                response = request(
                    "GET",
                    target_url,
                    headers=headers,
                    timeout=max(DIRECT_TIMEOUT_MS / 1000, 1),
                    retry_on_429=False,
                    **request_kwargs,
                )
                rendered_text = _extract_rendered_text_from_html(response.text)
                current = _parse_lines_from_rendered_text(rendered_text)
                if current:
                    suffix = f" {label_suffix}".rstrip()
                    print(f"BetMGM lines parsed via {proxy_label}{suffix} HTML page.")
                    return current

                preview_label = f"{proxy_label}{(' ' + label_suffix) if label_suffix else ''}"
                print(f"BetMGM HTML preview via {preview_label}: {_debug_rendered_text_preview(rendered_text)}")

                if _state_select_page_detected(rendered_text):
                    for candidate_url in _candidate_betmgm_urls_from_html(response.text):
                        try:
                            candidate_response = request(
                                "GET",
                                candidate_url,
                                headers=headers,
                                timeout=max(DIRECT_TIMEOUT_MS / 1000, 1),
                                retry_on_429=False,
                                **request_kwargs,
                            )
                            candidate_text = _extract_rendered_text_from_html(candidate_response.text)
                            candidate_current = _parse_lines_from_rendered_text(candidate_text)
                            if candidate_current:
                                print(f"BetMGM lines parsed via {preview_label} state URL: {candidate_url}")
                                return candidate_current
                        except Exception as candidate_exc:
                            print(f"BetMGM state URL fetch failed via {preview_label}: {candidate_exc}")
                return {}

            current_lines = fetch_lines_from_url(BETMGM_URL)
            if current_lines:
                return current_lines
        except Exception as exc:
            print(f"BetMGM HTML fetch failed via {proxy_label}: {exc}")
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
        for proxy_ip in _browser_proxy_candidates():
            proxy_settings = _proxy_settings(proxy_ip)
            proxy_label = proxy_ip or "direct"
            browser = None
            try:
                browser = playwright.chromium.launch(
                    headless=True,
                    proxy=proxy_settings,
                    timeout=LAUNCH_TIMEOUT_MS,
                )
                context = browser.new_context(
                    ignore_https_errors=True,
                    user_agent=BETMGM_USER_AGENT,
                )
                context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
                context.set_default_timeout(max(WAIT_MS, 3000))
                context.set_extra_http_headers(
                    {
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://www.betmgm.com/",
                    }
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
                print(f"BetMGM rendered preview via {proxy_label}: {_debug_rendered_text_preview(rendered_text)}")
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
        if not current_lines and ENABLE_BROWSER_FALLBACK:
            print("BetMGM: trying proxied browser fallback...")
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
