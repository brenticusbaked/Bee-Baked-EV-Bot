from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db_manager import load_tracker_state, save_tracker_state
from services.discord_channels import DEFAULT_WEBHOOK_URL
from utils.time import get_local_date_str


DEFAULT_TIMEOUT = 20
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
    session.headers.update({"User-Agent": "BeeBakedEVBot/1.0"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()
NO_429_RETRY_SESSION = build_session(retry_on_429=False)


def request(method: str, url: str, retry_on_429: bool = True, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    session = SESSION if retry_on_429 else NO_429_RETRY_SESSION
    response = session.request(method=method.upper(), url=url, **kwargs)
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
