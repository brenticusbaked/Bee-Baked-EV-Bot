import os
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_TIMEOUT = 20
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update({"User-Agent": "BeeBakedEVBot/1.0"})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


def request(method: str, url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    response = SESSION.request(method=method.upper(), url=url, **kwargs)
    response.raise_for_status()
    return response


def get_json(url: str, **kwargs):
    return request("GET", url, **kwargs).json()


def post_discord(payload: dict, webhook_url: Optional[str] = None) -> bool:
    target = webhook_url or DISCORD_WEBHOOK_URL
    if not target:
        return False

    try:
        response = request("POST", target, json=payload, timeout=15)
    except requests.RequestException as exc:
        print(f"Discord post failed: {exc}")
        return False

    if response.status_code >= 400:
        print(f"Discord post failed with status {response.status_code}: {response.text[:200]}")
        return False
    return True
