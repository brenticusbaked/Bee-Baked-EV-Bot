import json
import os
import random
import re
import string
from html import unescape
from typing import Dict, Optional
from urllib.parse import quote, urljoin, urlparse

from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import stealth_sync
except Exception:  # pragma: no cover - optional dependency in some environments
    stealth_sync = None

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
BETMGM_PREFERRED_STATE = os.getenv("BETMGM_PREFERRED_STATE", "ky").strip().lower()
BETMGM_HOME_URL = "https://www.betmgm.com/"
BETMGM_URL = "https://www.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004"
BETMGM_NBA_PATH = "/en/sports/basketball-7/betting/usa-9/nba-6004"
BETMGM_API_COUNTRY = os.getenv("BETMGM_API_COUNTRY", "US").strip().upper()
BETMGM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
STATE_DISPLAY_NAMES = {
    "az": "Arizona",
    "co": "Colorado",
    "dc": "District of Columbia",
    "ia": "Iowa",
    "il": "Illinois",
    "in": "Indiana",
    "ks": "Kansas",
    "ky": "Kentucky",
    "la": "Louisiana",
    "ma": "Massachusetts",
    "mi": "Michigan",
    "ms": "Mississippi",
    "nc": "North Carolina",
    "nj": "New Jersey",
    "oh": "Ohio",
    "pa": "Pennsylvania",
    "tn": "Tennessee",
    "va": "Virginia",
    "wv": "West Virginia",
    "wy": "Wyoming",
}
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


def _preferred_betmgm_url() -> str:
    state = BETMGM_PREFERRED_STATE or "ky"
    return f"https://{state}.betmgm.com{BETMGM_NBA_PATH}"


def _preferred_betmgm_api_base() -> str:
    state = BETMGM_PREFERRED_STATE or "ky"
    return f"https://sportsapi.{state}.betmgm.com"


def _preferred_state_label() -> str:
    return STATE_DISPLAY_NAMES.get(BETMGM_PREFERRED_STATE or "ky", "Kentucky")


def _normalized_betmgm_hosts(parsed) -> list[str]:
    labels = [label for label in parsed.netloc.lower().split(".") if label]
    if len(labels) < 2 or labels[-2:] != ["betmgm", "com"]:
        return []

    hosts = []
    if len(labels) == 3 and labels[0] in {"www", "sports"}:
        hosts.append(parsed.netloc.lower())
    if len(labels) == 3 and 2 <= len(labels[0]) <= 3 and labels[0].isalpha():
        hosts.append(parsed.netloc.lower())
    if len(labels) >= 4 and 2 <= len(labels[-3]) <= 3 and labels[-3].isalpha():
        hosts.append(f"{labels[-3]}.betmgm.com")

    seen = set()
    deduped = []
    for host in hosts:
        if host in seen:
            continue
        seen.add(host)
        deduped.append(host)
    return deduped


def _candidate_betmgm_urls_from_html(html: str) -> list[str]:
    candidates = [_preferred_betmgm_url()]
    href_matches = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    for href in href_matches:
        href = href.strip()
        if not href:
            continue
        absolute = urljoin(BETMGM_URL, href)
        parsed = urlparse(absolute)
        if "betmgm.com" not in parsed.netloc:
            continue
        for host in _normalized_betmgm_hosts(parsed):
            if parsed.path == BETMGM_NBA_PATH and host == parsed.netloc.lower():
                candidates.append(parsed._replace(netloc=host, params="", query="", fragment="").geturl())
            else:
                rebuilt = parsed._replace(netloc=host, path=BETMGM_NBA_PATH, params="", query="", fragment="").geturl()
                candidates.append(rebuilt)

    seen = set()
    deduped = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped[:8]


def _translation_text(translations) -> str:
    if isinstance(translations, list):
        for item in translations:
            if isinstance(item, dict):
                text = item.get("text") or item.get("shortText")
                if text:
                    return str(text).strip()
    if isinstance(translations, dict):
        text = translations.get("text") or translations.get("shortText")
        if text:
            return str(text).strip()
    if isinstance(translations, str):
        return translations.strip()
    return ""


def _translation_sign(translations) -> str:
    if isinstance(translations, list):
        for item in translations:
            if isinstance(item, dict):
                sign = item.get("sign") or item.get("shortTextSign")
                if sign:
                    return str(sign).strip()
    if isinstance(translations, dict):
        sign = translations.get("sign") or translations.get("shortTextSign")
        if sign:
            return str(sign).strip()
    return ""


def _build_current_lines_from_api(fixtures: list[dict]) -> Dict[str, Dict[str, object]]:
    current_lines: Dict[str, Dict[str, object]] = {}

    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        participants = fixture.get("participants") or []
        if len(participants) < 2:
            continue
        participant_names = [_translation_text(participant.get("name")) for participant in participants if isinstance(participant, dict)]
        if len(participant_names) < 2:
            continue
        matchup = f"{participant_names[0]} @ {participant_names[1]}"

        fixture_ids = fixture.get("id") or []
        event_id = None
        if isinstance(fixture_ids, list):
            for item in fixture_ids:
                if isinstance(item, dict) and item.get("entityId") is not None:
                    event_id = str(item.get("entityId"))
                    break
        if not event_id:
            event_id = re.sub(r"[^a-z0-9]+", "_", matchup.lower()).strip("_")

        for market in fixture.get("markets", []):
            if not isinstance(market, dict):
                continue
            market_name = _translation_text(market.get("name")).lower()
            market_type = str(market.get("marketType", "")).lower()
            if "spread" not in market_name and "spread" not in market_type:
                continue

            market_value = market.get("value")
            options = market.get("options") or []
            for option in options:
                if not isinstance(option, dict):
                    continue
                option_name = option.get("name")
                team = _translation_text(option_name)
                if not team:
                    continue

                sign = _translation_sign(option_name)
                line_value = None
                if market_value not in (None, ""):
                    try:
                        value_float = float(market_value)
                        if sign in {"+", "-"}:
                            line_value = f"{sign}{abs(value_float):.1f}".rstrip("0").rstrip(".")
                            if "." not in line_value:
                                line_value = f"{line_value}.0"
                        else:
                            line_value = f"{value_float:+.1f}".rstrip("0").rstrip(".")
                            if "." not in line_value:
                                line_value = f"{line_value}.0"
                    except Exception:
                        line_value = None

                if line_value is None:
                    continue

                unique_key = f"{event_id}_{team.lower()}"
                current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line_value}

    return current_lines


def _fetch_betmgm_api_lines() -> Dict[str, Dict[str, object]]:
    headers = {
        "Accept": "application/json",
        "Referer": _preferred_betmgm_url(),
        "User-Agent": BETMGM_USER_AGENT,
    }
    api_base = _preferred_betmgm_api_base()

    for proxy_ip in _proxy_candidates():
        proxy_label = proxy_ip or "direct"
        request_kwargs = _request_proxy_kwargs(proxy_ip)
        try:
            sports_response = request(
                "GET",
                f"{api_base}/offer/api/{BETMGM_API_COUNTRY}/sports",
                headers=headers,
                timeout=max(DIRECT_TIMEOUT_MS / 1000, 1),
                retry_on_429=False,
                **request_kwargs,
            )
            sports_items = sports_response.json().get("items", [])
            basketball_sport_id = None
            for sport in sports_items:
                if not isinstance(sport, dict):
                    continue
                if _translation_text(sport.get("name")).lower() == "basketball":
                    basketball_sport_id = sport.get("id")
                    break
            if basketball_sport_id is None:
                print(f"BetMGM API did not return a Basketball sport id via {proxy_label}.")
                continue

            competitions_response = request(
                "GET",
                f"{api_base}/offer/api/{basketball_sport_id}/{BETMGM_API_COUNTRY}/competitions",
                headers=headers,
                timeout=max(DIRECT_TIMEOUT_MS / 1000, 1),
                retry_on_429=False,
                params={"language": "en"},
                **request_kwargs,
            )
            competitions = competitions_response.json().get("items", [])
            nba_competition_id = None
            for competition in competitions:
                if not isinstance(competition, dict):
                    continue
                if _translation_text(competition.get("name")).lower() == "nba":
                    nba_competition_id = competition.get("id")
                    break
            if nba_competition_id is None:
                print(f"BetMGM API did not return an NBA competition id via {proxy_label}.")
                continue

            fixtures_response = request(
                "GET",
                f"{api_base}/offer/api/{basketball_sport_id}/{BETMGM_API_COUNTRY}/fixtures",
                headers=headers,
                timeout=max(DIRECT_TIMEOUT_MS / 1000, 1),
                retry_on_429=False,
                params={
                    "language": "en",
                    "competitionIds": nba_competition_id,
                    "onlyMainMarkets": "true",
                    "marketsFilterCriteria": "Visible",
                    "isInPlay": "false",
                },
                **request_kwargs,
            )
            fixtures = fixtures_response.json().get("items", [])
            current_lines = _build_current_lines_from_api(fixtures)
            if current_lines:
                print(f"BetMGM lines parsed via sportsbook API on {api_base} using {proxy_label}.")
                return current_lines
            print(f"BetMGM sportsbook API returned fixtures but no spread lines on {api_base} using {proxy_label}.")
        except Exception as exc:
            print(f"BetMGM sportsbook API fetch failed via {proxy_label}: {exc}")

    return {}


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
                            print(f"BetMGM trying state URL via {preview_label}: {candidate_url}")
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

            for target_url, label_suffix in (
                (_preferred_betmgm_url(), "preferred_state"),
                (BETMGM_URL, ""),
            ):
                current_lines = fetch_lines_from_url(target_url, label_suffix)
                if current_lines:
                    return current_lines
        except Exception as exc:
            print(f"BetMGM HTML fetch failed via {proxy_label}: {exc}")
    return {}


def _extract_payload_from_page(page, target_url: str):
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
        page.goto(target_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception:
        print(f"BetMGM navigation timeout caught gracefully for {target_url}. Checking for data anyway...")

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

    if _state_select_page_detected(rendered_text):
        state_label = _preferred_state_label()
        try:
            print(f"BetMGM browser selecting state: {state_label}")
            state_link = page.get_by_role("link", name=state_label).first
            state_link.click(timeout=3000)
            page.wait_for_timeout(1500)
            try:
                page.goto(_preferred_betmgm_url(), wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception:
                print(f"BetMGM navigation timeout caught gracefully for {_preferred_betmgm_url()} after state select.")

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
                        captured["url"] = "embedded_page_state_after_state_select"
                except Exception:
                    pass

            try:
                rendered_text = page.locator("body").inner_text(timeout=3000)
            except Exception:
                try:
                    rendered_text = _extract_rendered_text_from_html(page.content())
                except Exception:
                    rendered_text = ""
        except Exception as exc:
            print(f"BetMGM browser state selection failed: {exc}")

    return captured["data"], captured["url"], rendered_text


def _fetch_betmgm_snapshot():
    target_urls = [BETMGM_HOME_URL]
    with sync_playwright() as playwright:
        for proxy_ip in _browser_proxy_candidates():
            proxy_settings = _proxy_settings(proxy_ip)
            proxy_label = proxy_ip or "direct"
            for target_url in target_urls:
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
                        locale="en-US",
                        timezone_id="America/Chicago",
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
                    if stealth_sync:
                        try:
                            stealth_sync(page)
                        except Exception as exc:
                            print(f"BetMGM stealth setup warning: {exc}")
                    data, source_url, rendered_text = _extract_payload_from_page(page, target_url)
                    browser.close()
                    if data:
                        print(f"BetMGM payload captured via {proxy_label}: {source_url}")
                        return data, rendered_text
                    if _parse_lines_from_rendered_text(rendered_text):
                        print(f"BetMGM lines parsed via {proxy_label}: rendered_page_text ({target_url})")
                        return None, rendered_text
                    print(f"BetMGM rendered preview via {proxy_label} ({target_url}): {_debug_rendered_text_preview(rendered_text)}")
                    print(f"BetMGM: no usable payload or rendered lines via {proxy_label} ({target_url}).")
                except Exception as exc:
                    print(f"BetMGM proxy attempt failed via {proxy_label} ({target_url}): {exc}")
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
        current_lines = _fetch_betmgm_api_lines()
        if not current_lines:
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
