import json
import os
import re
from dataclasses import replace
from html import unescape
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright
try:
    from playwright_stealth import stealth_sync
except Exception:  # pragma: no cover - optional dependency in some environments
    stealth_sync = None

from db_manager import get_master_cache, load_tracker_state, save_tracker_state
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import post_discord
from services.http_client import request as http_request
from services.odds_reference import format_pinnacle_spread_reference
from services.odds_scraper_ingest import extract_price, ingest_current_lines
from services.scraper_api_client import fetch as scraper_api_fetch
from services.scraper_api_client import (
    options_for,
    playwright_proxy,
)
from services.scraper_api_client import target_url as scraper_api_target_url


BOOK_KEY = "betmgm"
DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
TRACKER_FILE = "mgm_lines.json"
STATE_KEY = "tracker_betmgm_nba"

DIRECT_TIMEOUT_MS = int(os.getenv("BETMGM_DIRECT_TIMEOUT_MS", "8000"))
LAUNCH_TIMEOUT_MS = int(os.getenv("BETMGM_LAUNCH_TIMEOUT_MS", "8000"))
NAV_TIMEOUT_MS = int(os.getenv("BETMGM_NAV_TIMEOUT_MS", "15000"))
WAIT_CYCLES = int(os.getenv("BETMGM_WAIT_CYCLES", "2"))
WAIT_MS = int(os.getenv("BETMGM_WAIT_MS", "2000"))
ENABLE_BROWSER_FALLBACK = os.getenv("BETMGM_ENABLE_BROWSER_FALLBACK", "false").strip().lower() in {
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

# BetMGM's SPA is fed by the bwin/GVC "cds-api" offer service. That service answers
# JSON from an ordinary datacenter IP (it is the marketing site and the JS bundles
# that sit behind Cloudflare), so the fixtures call is made directly and only falls
# back to ScraperAPI when the direct call is refused. The one thing the service
# requires is an ``x-bwin-accessid`` token, which is minted per brand and published
# in the sportsbook page for a real browser session.
CDS_FIXTURES_PATH = "/cds-api/bettingoffer/fixtures"
BETMGM_ACCESS_ID = os.getenv("BETMGM_ACCESS_ID", "").strip()
ACCESS_ID_STATE_KEY = "betmgm_access_id"
ACCESS_ID_FILE = "betmgm_access_id.json"
ACCESS_ID_PATTERNS = (
    r"x-bwin-accessid=([A-Za-z0-9+/=_-]{12,})",
    r'"accessId"\s*:\s*"([A-Za-z0-9+/=_-]{12,})"',
    r'accessId["\']?\s*[:=]\s*["\']([A-Za-z0-9+/=_-]{12,})["\']',
)
# bwin sport ids: 23 = Baseball, 7 = Basketball, 11 = American Football, 12 = Ice Hockey.
BETMGM_SPORT_ID = os.getenv("BETMGM_SPORT_ID", "23").strip()
BETMGM_COMPETITION_NAME = os.getenv("BETMGM_COMPETITION_NAME", "MLB").strip()
BETMGM_SPORT_KEY = os.getenv("BETMGM_SPORT_KEY", "baseball_mlb").strip()
BETMGM_FIXTURE_LIMIT = int(os.getenv("BETMGM_FIXTURE_LIMIT", "50"))
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


def _pinnacle_reference(current: dict) -> str:
    return format_pinnacle_spread_reference(
        get_master_cache() or {},
        BETMGM_SPORT_KEY,
        str(current.get("matchup", "")),
        str(current.get("team", "")),
    )


def _proxy_candidates():
    return ["scraperapi"]


def _browser_proxy_candidates():
    return ["scraperapi"]


def _proxy_settings(proxy_ip: Optional[str]):
    return playwright_proxy(BOOK_KEY)


def _sa_get(
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict[str, str]] = None,
    render: bool = False,
):
    """GET a BetMGM URL through ScraperAPI.

    ``render`` is for the HTML sportsbook page, whose odds are injected by the
    SPA after load; the ``offer/api`` endpoints answer with JSON and only need a
    residential IP.
    """
    options = replace(options_for(BOOK_KEY), keep_headers=True, render=render)
    return scraper_api_fetch(
        scraper_api_target_url(url, params),
        BOOK_KEY,
        options=options,
        headers=headers,
    )


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
                text = item.get("value") or item.get("text") or item.get("shortText")
                if text:
                    return str(text).strip()
    if isinstance(translations, dict):
        text = translations.get("value") or translations.get("text") or translations.get("shortText")
        if text:
            return str(text).strip()
    if isinstance(translations, str):
        return translations.strip()
    return ""


def _cds_host() -> str:
    state = BETMGM_PREFERRED_STATE or "ky"
    return f"https://sports.{state}.betmgm.com"


def _cds_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{_cds_host()}/en/sports",
        "User-Agent": BETMGM_USER_AGENT,
        "x-bwin-accessid": "",
    }


def _cds_params(access_id: str) -> Dict[str, str]:
    state = (BETMGM_PREFERRED_STATE or "ky").upper()
    return {
        "x-bwin-accessid": access_id,
        "lang": "en-us",
        "country": BETMGM_API_COUNTRY,
        "userCountry": BETMGM_API_COUNTRY,
        "subdivision": f"{BETMGM_API_COUNTRY}-{state}",
        "fixtureTypes": "Standard",
        "state": "Latest",
        "offerMapping": "All",
        "offerCategories": "Gridable",
        "fixtureCategories": "Gridable,NonGridable,Other",
        "sportIds": BETMGM_SPORT_ID,
        "skip": "0",
        "take": str(BETMGM_FIXTURE_LIMIT),
        "sortBy": "Tags",
    }


def _cached_access_id() -> str:
    state = load_tracker_state(ACCESS_ID_STATE_KEY, ACCESS_ID_FILE)
    if isinstance(state, dict):
        return str(state.get("access_id") or "").strip()
    if isinstance(state, str):
        return state.strip()
    return ""


def _store_access_id(access_id: str) -> None:
    save_tracker_state(ACCESS_ID_STATE_KEY, {"access_id": access_id}, ACCESS_ID_FILE)


def _access_id_from_text(text: str) -> str:
    for pattern in ACCESS_ID_PATTERNS:
        match = re.search(pattern, text or "")
        if match:
            return match.group(1).strip()
    return ""


def _discover_access_id() -> str:
    """Find the brand's ``x-bwin-accessid`` token.

    Cloudflare serves the sportsbook's JS bundles and client config as empty
    bodies to datacenter IPs, so discovery goes through a residential ScraperAPI
    IP. One HTML page is enough and the token is long-lived, so it is cached in
    ``bot_state`` and only re-fetched when the offer service rejects it.
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": BETMGM_USER_AGENT,
    }
    targets = (
        f"{_cds_host()}/en/sports",
        f"{_cds_host()}/en/api/clientconfig?browserUrl={_cds_host()}/en/sports&x-from-product=host-app",
    )
    for target in targets:
        for render in (False, True):
            try:
                response = _sa_get(target, headers, render=render)
            except Exception as exc:
                print(f"BetMGM access id lookup failed on {target} (render={render}): {exc}")
                continue
            access_id = _access_id_from_text(response.text)
            if access_id:
                print(f"BetMGM discovered access id via {target} (render={render}).")
                _store_access_id(access_id)
                return access_id
    print("BetMGM: no access id found; set BETMGM_ACCESS_ID to skip discovery.")
    return ""


def _access_id_candidates() -> list[str]:
    candidates = [BETMGM_ACCESS_ID, _cached_access_id()]
    return [candidate for candidate in dict.fromkeys(candidates) if candidate]


def _cds_get(access_id: str):
    """Fetch the fixtures JSON, direct first and through ScraperAPI if refused.

    The offer service itself is not WAF-protected, so the direct call normally
    succeeds and costs nothing; ScraperAPI is the fallback for runners whose IP
    range Cloudflare does challenge.
    """
    url = f"{_cds_host()}{CDS_FIXTURES_PATH}"
    params = _cds_params(access_id)
    headers = _cds_headers()
    headers["x-bwin-accessid"] = access_id

    try:
        response = http_request("GET", url, params=params, headers=headers, timeout=DIRECT_TIMEOUT_MS / 1000)
        if response.status_code < 400:
            return response
        print(f"BetMGM cds-api direct call returned {response.status_code}; retrying via scraperapi.")
        if response.status_code == 400:
            return response
    except Exception as exc:
        print(f"BetMGM cds-api direct call failed: {exc}")

    return _sa_get(url, headers, params=params)


def _fixture_competition(fixture: dict) -> str:
    competition = fixture.get("competition")
    if isinstance(competition, dict):
        return _translation_text(competition.get("name"))
    return ""


def _matches_configured_competition(fixture: dict) -> bool:
    if not BETMGM_COMPETITION_NAME:
        return True
    wanted = BETMGM_COMPETITION_NAME.lower()
    haystacks = [_fixture_competition(fixture).lower(), _translation_text(fixture.get("league")).lower()]
    return any(wanted in haystack for haystack in haystacks if haystack)


def _fetch_betmgm_cds_markets() -> Dict[str, Dict[str, Dict[str, object]]]:
    tried_discovery = False
    for access_id in _access_id_candidates() + [""]:
        if not access_id:
            if tried_discovery:
                break
            tried_discovery = True
            access_id = _discover_access_id()
            if not access_id:
                break

        try:
            response = _cds_get(access_id)
        except Exception as exc:
            print(f"BetMGM cds-api fetch failed: {exc}")
            continue

        body = response.text or ""
        if "Access id is invalid" in body or response.status_code == 400:
            print("BetMGM access id rejected by the offer service; rediscovering.")
            _store_access_id("")
            continue

        payload = _decode_possible_json(body)
        if not isinstance(payload, (dict, list)):
            print(f"BetMGM cds-api returned a non-JSON body ({len(body)} bytes).")
            continue

        container = _find_fixture_container(payload) or {}
        fixtures = [
            fixture
            for fixture in container.get("fixtures", [])
            if isinstance(fixture, dict) and _matches_configured_competition(fixture)
        ]
        if not fixtures:
            print(
                "BetMGM cds-api returned no "
                f"{BETMGM_COMPETITION_NAME or BETMGM_SPORT_ID} fixtures."
            )
            continue

        by_market = _build_market_lines({"fixtures": fixtures})
        if by_market:
            summary = ", ".join(f"{key} {len(lines)}" for key, lines in sorted(by_market.items()))
            print(
                f"BetMGM cds-api parsed {len(fixtures)} "
                f"{BETMGM_COMPETITION_NAME or 'fixture'} event(s): {summary}."
            )
            return by_market
        print(f"BetMGM cds-api returned {len(fixtures)} fixture(s) but no main-market lines.")

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

            def fetch_lines_from_url(page_url: str, label_suffix: str = "") -> Dict[str, Dict[str, object]]:
                # This is the SPA page rather than the JSON API, so the odds only
                # exist after ScraperAPI runs the page's JavaScript.
                response = _sa_get(page_url, headers, render=True)
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
                            candidate_response = _sa_get(candidate_url, headers, render=True)
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


def _classify_market(market_name: str) -> Optional[str]:
    """Map a BetMGM grid market name onto a master-cache market key.

    Period, alternate and player markets are skipped: the master cache holds one
    line per outcome per market, so mixing "1st 5 Innings Run Line" into
    ``spreads`` would overwrite the full-game price the scanner de-vigs against.
    """
    name = (market_name or "").lower()
    if not name:
        return None
    if any(token in name for token in ("1st", "2nd", "3rd", "first ", "half", "quarter", "inning", "period", "player", "team total")):
        return None
    if any(token in name for token in ("spread", "run line", "puck line", "handicap")):
        return "spreads"
    if any(token in name for token in ("money line", "moneyline", "match result", "winner", "to win")):
        return "h2h"
    if "total" in name or "over/under" in name:
        return "totals"
    return None


def _option_point(option: dict, market: dict) -> Optional[str]:
    """The handicap or total attached to an option, as a signed string."""
    attributes = option.get("attributes")
    if isinstance(attributes, dict):
        for key in ("spread", "line", "handicap", "total"):
            value = attributes.get(key)
            if value not in (None, ""):
                return str(value)

    for candidate in (
        _translation_text(option.get("sourceName")),
        str(option.get("attr") or ""),
        _translation_text(option.get("name")),
        str(market.get("value") or ""),
    ):
        match = re.search(r"[+-]?\d+(?:\.\d+)?", candidate or "")
        if match:
            return match.group(0)
    return None


def _build_market_lines(data: dict) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Group a cds-api fixtures payload into {market_key: {unique_key: line}}."""
    by_market: Dict[str, Dict[str, Dict[str, object]]] = {}
    container = _find_fixture_container(data) or {}

    for fixture in container.get("fixtures", []):
        if not isinstance(fixture, dict):
            continue
        matchup = _translation_text(fixture.get("name")) or "Unknown Matchup"
        event_id = str(fixture.get("id"))
        commence_time = str(
            fixture.get("startDate")
            or fixture.get("startTime")
            or fixture.get("eventStartTime")
            or ""
        ).strip()

        for market in fixture.get("optionMarkets", []):
            if not isinstance(market, dict):
                continue
            market_key = _classify_market(_translation_text(market.get("name")))
            if not market_key:
                continue

            for option in market.get("options", []):
                if not isinstance(option, dict):
                    continue
                team = _translation_text(option.get("name"))
                price = extract_price(option)
                if not team or price is None:
                    continue

                point = None if market_key == "h2h" else _option_point(option, market)
                if market_key != "h2h" and point is None:
                    continue

                lines = by_market.setdefault(market_key, {})
                unique_key = f"{event_id}_{team}"
                lines.setdefault(
                    unique_key,
                    {
                        "event_id": event_id,
                        "matchup": matchup,
                        "commence_time": commence_time,
                        "team": team,
                        "line": point,
                        "price": price,
                    },
                )

    return by_market


def _build_current_lines(data: dict) -> Dict[str, Dict[str, object]]:
    return _build_market_lines(data).get("spreads", {})


def scrape_betmgm():
    try:
        by_market = _fetch_betmgm_cds_markets()
        current_lines = by_market.get("spreads", {})
        if not by_market:
            current_lines = _fetch_betmgm_direct_lines()
        if not current_lines and ENABLE_BROWSER_FALLBACK:
            print("BetMGM: trying proxied browser fallback...")
            data, rendered_text = _fetch_betmgm_snapshot()
            if data:
                current_lines = _build_current_lines(data)
            if not current_lines and rendered_text:
                current_lines = _parse_lines_from_rendered_text(rendered_text)

        if current_lines and "spreads" not in by_market:
            by_market["spreads"] = current_lines

        if not by_market:
            print(f"Could not capture usable BetMGM {BETMGM_COMPETITION_NAME or 'main-market'} lines.")
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
                    f"**Pinnacle:** {_pinnacle_reference(current)}\n"
                    f"Old Line: {old_line} -> **New Line: {current['line']}**"
                )

        save_current_lines(current_lines)
        for market_key, lines in by_market.items():
            ingest_current_lines(BETMGM_SPORT_KEY, BOOK_KEY, market_key, lines)
        for message in alerts:
            post_discord({"embeds": [{"description": message, "color": 13611036}]}, webhook_url=DISCORD_WEBHOOK_URL)

        tracked = sum(len(lines) for lines in by_market.values())
        return {
            "detail": (
                f"betmgm scrape complete ({tracked} lines across "
                f"{len(by_market)} market(s) on {BETMGM_SPORT_KEY})"
            ),
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