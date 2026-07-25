import os
import random
from typing import Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db_manager import load_tracker_state, save_tracker_state
from services.discord_channels import DEFAULT_WEBHOOK_URL
from utils.time import get_local_date_str


DEFAULT_TIMEOUT = 20

_MLB_STATS_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
}

# statsapi.mlb.com blocks datacenter egress IPs (GitHub Actions runners) with
# HTTP 406. Route those calls through the project's residential proxy pool, and
# rotate to a fresh residential IP on each 406/403 retry. Falls back to a direct
# request when no proxy is configured (e.g. local dev), preserving old behavior.
_PROXY_USERNAME = os.getenv("PROXY_USERNAME")
_PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
_PROXY_IPS = [
    ip.strip()
    for ip in os.getenv("PROXY_LIST", "").replace("\n", ",").split(",")
    if ip.strip()
]
_MLB_PROXY_MAX_ATTEMPTS = 3


def _residential_proxies() -> Optional[dict[str, str]]:
    """Build a rotating residential proxy dict, or None if unconfigured."""
    if not (_PROXY_IPS and _PROXY_USERNAME and _PROXY_PASSWORD):
        return None
    chosen_ip = random.choice(_PROXY_IPS)
    session_id = random.randint(10_000, 99_999)
    # URL-encode the userinfo so special characters in the credentials (@, :, #,
    # /, etc.) don't corrupt the proxy URL and trigger a 407 auth failure.
    user = quote(f"{_PROXY_USERNAME}-session-{session_id}", safe="")
    password = quote(_PROXY_PASSWORD or "", safe="")
    proxy_url = f"http://{user}:{password}@{chosen_ip}"
    return {"http": proxy_url, "https": proxy_url}


DISCORD_WEBHOOK_URL = DEFAULT_WEBHOOK_URL
BEE_IMAGE_URL = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"
BEE_IMAGE_STATE_KEY = "bee_image_daily_limit"
BEE_IMAGE_STATE_FILE = "bee_image_state.json"
MAX_BEE_IMAGES_PER_DAY = 2
RESERVED_DAILY_SLIPS_IMAGE_SLOTS = 1


def build_session(retry_on_429: bool = True) -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=((429, 500, 502, 503, 504) if retry_on_429 else (500, 502, 503, 504)),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "BeeBakedEVBot/1.0"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()
NO_429_RETRY_SESSION = build_session(retry_on_429=False)


def request(method: str, url: str, retry_on_429: bool = True, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    session = SESSION if retry_on_429 else NO_429_RETRY_SESSION
    if "statsapi.mlb.com" in url:
        merged = dict(_MLB_STATS_HEADERS)
        merged.update(kwargs.get("headers") or {})
        kwargs["headers"] = merged
        return _mlb_request(session, method.upper(), url, **kwargs)
    response = session.request(method=method.upper(), url=url, **kwargs)
    response.raise_for_status()
    return response


def _mlb_request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """statsapi.mlb.com request that rotates residential proxies on 406/403."""
    proxies = _residential_proxies()
    if proxies is None:
        response = session.request(method=method, url=url, **kwargs)
        response.raise_for_status()
        return response

    last_response: Optional[requests.Response] = None
    for _ in range(_MLB_PROXY_MAX_ATTEMPTS):
        try:
            response = session.request(method=method, url=url, proxies=proxies, **kwargs)
        except requests.exceptions.ProxyError:
            last_response = None
            break
        if response.status_code not in (403, 406, 407):
            response.raise_for_status()
            return response
        last_response = response
        proxies = _residential_proxies() or proxies
    if last_response is not None and last_response.status_code != 407:
        last_response.raise_for_status()

    response = session.request(method=method, url=url, **kwargs)
    response.raise_for_status()
    return response


def get_json(url: str, **kwargs):
    return request("GET", url, **kwargs).json()


def _load_bee_image_state() -> dict:
    state = load_tracker_state(BEE_IMAGE_STATE_KEY, BEE_IMAGE_STATE_FILE)
    if not isinstance(state, dict):
        return {}
    return state


def _normalize_bee_image_state(state: dict, today: str) -> dict:
    if state.get("date") != today:
        return {"date": today, "default_count": 0, "daily_slips_count": 0}
    state.setdefault("default_count", int(state.get("count", 0)))
    state.setdefault("daily_slips_count", 0)
    state.pop("count", None)
    return state


def _can_use_bee_image(slot: str = "default") -> bool:
    today = get_local_date_str()
    state = _normalize_bee_image_state(_load_bee_image_state(), today)

    if slot == "daily_slips":
        return int(state.get("daily_slips_count", 0)) < RESERVED_DAILY_SLIPS_IMAGE_SLOTS

    max_default_images = max(MAX_BEE_IMAGES_PER_DAY - RESERVED_DAILY_SLIPS_IMAGE_SLOTS, 0)
    return int(state.get("default_count", 0)) < max_default_images


def _record_bee_image_use(slot: str = "default") -> None:
    today = get_local_date_str()
    state = _normalize_bee_image_state(_load_bee_image_state(), today)

    key = "daily_slips_count" if slot == "daily_slips" else "default_count"
    state[key] = int(state.get(key, 0)) + 1
    save_tracker_state(BEE_IMAGE_STATE_KEY, state, BEE_IMAGE_STATE_FILE)


def post_discord(
    payload: dict,
    webhook_url: Optional[str] = None,
    add_bee_image: bool = False,
    bee_image_slot: str = "default",
) -> bool:
    target = webhook_url or DISCORD_WEBHOOK_URL
    if not target:
        return False

    attached_bee_image = False
    if add_bee_image and payload.get("embeds") and _can_use_bee_image(bee_image_slot):
        payload["embeds"][-1]["image"] = {"url": BEE_IMAGE_URL}
        attached_bee_image = True

    try:
        response = request("POST", target, json=payload, timeout=15)
    except requests.RequestException as exc:
        print(f"Discord post failed: {exc}")
        return False

    if response.status_code >= 400:
        print(f"Discord post failed with status {response.status_code}: {response.text[:200]}")
        return False
    if attached_bee_image:
        _record_bee_image_use(bee_image_slot)
    return True
