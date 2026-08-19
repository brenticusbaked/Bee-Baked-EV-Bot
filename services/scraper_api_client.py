"""Centralised ScraperAPI network layer for the sportsbook scrapers.

Every retail-book request goes through one of two ScraperAPI transports:

* the **API endpoint** (``fetch``/``fetch_json``) for the books that expose a
  JSON feed — DraftKings' ``eventgroup`` endpoint, FanDuel's
  ``content-managed-page``, BetMGM's widget API. These need an unblocked IP, not
  a browser, so they run without ``render`` and cost 10 credits instead of 25.
* **proxy mode** (``playwright_proxy``) for the pages that only exist as a
  rendered SPA, so Playwright keeps working but stops egressing from the GitHub
  Actions IP that every WAF already knows.

Status handling is deliberately not uniform, because ScraperAPI overloads the
codes: a 500 means ScraperAPI itself could not get the page (free, retry), a 429
means we exceeded plan concurrency (retry), while a 403 means the account is out
of API credits (retrying only burns runner minutes) and a 401 means the key is
bad. The last two trip a run-wide kill switch instead.
"""

import asyncio
import json
import os
import random
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import quote, urlencode

import requests

from db_manager import load_tracker_state, save_tracker_state
from services.discord_channels import DEAD_LETTER_WEBHOOK_URL, STATUS_WEBHOOK_URL
from utils.config import env_flag
from utils.time import get_local_now

API_ENDPOINT = "https://api.scraperapi.com/"
PROXY_HOST = "proxy-server.scraperapi.com"
PROXY_PORT = 8001
PROXY_USER = "scraperapi"

# Credit costs per successful request, from ScraperAPI's published pricing.
CREDITS_BASE = 1
CREDITS_PREMIUM = 10
CREDITS_RENDER = 10
CREDITS_PREMIUM_RENDER = 25
CREDITS_ULTRA_PREMIUM = 30
CREDITS_ULTRA_PREMIUM_RENDER = 75

# ScraperAPI recommends a 70s client timeout: hard targets can legitimately take
# 60s, and abandoning earlier is still billed as a cancelled request.
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SCRAPERAPI_TIMEOUT_SECONDS", "70"))
MAX_ATTEMPTS = int(os.getenv("SCRAPERAPI_MAX_ATTEMPTS", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("SCRAPERAPI_BACKOFF_BASE_SECONDS", "2"))
BACKOFF_MAX_SECONDS = float(os.getenv("SCRAPERAPI_BACKOFF_MAX_SECONDS", "8"))
# Whole-run wall clock ceiling. The scraper job shares a runner with the rest of
# the pipeline, so the client refuses to start a request once this is spent
# rather than letting retries walk into the job timeout.
RUN_BUDGET_SECONDS = float(os.getenv("SCRAPERAPI_RUN_BUDGET_SECONDS", "600"))
MAX_CREDITS_PER_RUN = int(os.getenv("SCRAPERAPI_MAX_CREDITS_PER_RUN", "2500"))
# Plan ceiling for the billing month. The per-run cap alone cannot stop a
# frequent cron from spending the plan by the 10th, so spend is also checked
# against the month's running total before each request.
MONTHLY_CREDIT_BUDGET = int(os.getenv("SCRAPERAPI_MONTHLY_CREDITS", "100000"))
# Concurrent in-flight requests. ScraperAPI answers 429 when a plan's thread
# limit is exceeded, so this stays low enough to keep the retry path rare while
# still overlapping the 5-15s each protected page takes.
MAX_CONCURRENCY = int(os.getenv("SCRAPERAPI_CONCURRENCY", "2"))
# Process-wide ceiling on simultaneous ScraperAPI requests. The scraper phase
# runs each book in its own thread, so a per-call semaphore cannot see the other
# books; without this the threads together exceed the plan's concurrency limit
# and ScraperAPI answers 429.
MAX_IN_FLIGHT = int(os.getenv("SCRAPERAPI_MAX_IN_FLIGHT", "4"))

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
OUT_OF_CREDITS_STATUS = 403
UNAUTHORIZED_STATUS = 401

CREDIT_STATE_KEY = "scraperapi_credits"

_IN_FLIGHT = threading.BoundedSemaphore(max(1, MAX_IN_FLIGHT))


class ScraperApiError(RuntimeError):
    """Base class for ScraperAPI transport failures."""


class ScraperApiNotConfigured(ScraperApiError):
    """No API key in the environment."""


class ScraperApiOutOfCredits(ScraperApiError):
    """The account has no API credits left this billing cycle."""


class ScraperApiBlocked(ScraperApiError):
    """ScraperAPI could not retrieve the target page."""


@dataclass(frozen=True)
class ScraperApiOptions:
    """Per-book request shape. ``premium`` buys residential/mobile IPs,
    ``render`` runs a headless browser on ScraperAPI's side."""

    premium: bool = True
    render: bool = False
    ultra_premium: bool = False
    keep_headers: bool = False
    country_code: str = "us"
    device_type: str = ""
    wait_for_selector: str = ""

    def __post_init__(self) -> None:
        if self.premium and self.ultra_premium:
            raise ValueError("premium and ultra_premium cannot be combined")
        if self.ultra_premium and self.keep_headers:
            # ScraperAPI discards custom headers on ultra_premium requests, so
            # asking for both silently drops the headers.
            raise ValueError("keep_headers is ignored on ultra_premium requests")
        if self.wait_for_selector and not self.render:
            raise ValueError("wait_for_selector requires render=True")

    @property
    def credits(self) -> int:
        if self.ultra_premium:
            return CREDITS_ULTRA_PREMIUM_RENDER if self.render else CREDITS_ULTRA_PREMIUM
        if self.premium:
            return CREDITS_PREMIUM_RENDER if self.render else CREDITS_PREMIUM
        if self.render:
            return CREDITS_RENDER
        return CREDITS_BASE


# JSON feeds need a clean residential IP, not a browser. Books whose odds only
# exist in rendered HTML are listed in SCRAPERAPI_RENDER_BOOKS instead of paying
# 25 credits on every request here.
JSON_FEED_OPTIONS = ScraperApiOptions(premium=True, render=False)
RENDERED_PAGE_OPTIONS = ScraperApiOptions(premium=True, render=True)

BOOK_PROFILES: dict[str, ScraperApiOptions] = {
    "draftkings": JSON_FEED_OPTIONS,
    "fanduel": JSON_FEED_OPTIONS,
    "betmgm": JSON_FEED_OPTIONS,
    "novig": JSON_FEED_OPTIONS,
    "fanatics": JSON_FEED_OPTIONS,
    "onyx": JSON_FEED_OPTIONS,
    # DFS and exchange feeds are plain JSON APIs; PrizePicks sits behind
    # Cloudflare and needs the residential IP, the others just need an IP that
    # is not a datacentre range.
    "prizepicks": JSON_FEED_OPTIONS,
    "underdog": JSON_FEED_OPTIONS,
    "prophetx": JSON_FEED_OPTIONS,
}


def _csv_env(name: str) -> frozenset:
    raw = os.getenv(name, "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def options_for(book: str) -> ScraperApiOptions:
    """Resolve a book's request shape, with env overrides so a book that starts
    failing can be escalated to rendering or ultra_premium without a deploy."""
    key = (book or "").strip().lower()
    options = BOOK_PROFILES.get(key, JSON_FEED_OPTIONS)
    if key in _csv_env("SCRAPERAPI_RENDER_BOOKS"):
        options = replace(options, render=True)
    if key in _csv_env("SCRAPERAPI_ULTRA_BOOKS"):
        options = replace(options, premium=False, ultra_premium=True, keep_headers=False)
    return options


def api_key() -> str | None:
    """SCRAPERAPI_KEY is the documented name; SCRAPER_API_KEY is the secret
    already configured on this repo, so both are accepted."""
    for name in ("SCRAPERAPI_KEY", "SCRAPER_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def is_configured() -> bool:
    return api_key() is not None


@dataclass
class BookStats:
    """Per-bookmaker call outcomes, for credit-burn and WAF-block monitoring."""

    requests: int = 0
    successes: int = 0
    retries: int = 0
    blocks: int = 0
    credits: int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return (self.successes / self.requests) if self.requests else 0.0

    @property
    def avg_latency(self) -> float:
        return (sum(self.latencies) / len(self.latencies)) if self.latencies else 0.0


_STATS: dict[str, BookStats] = {}
_RUN_STARTED_AT: float | None = None
_DISABLED_REASON: str | None = None
_ALERTED: set = set()


def _stats_for(book: str) -> BookStats:
    return _STATS.setdefault(book or "unknown", BookStats())


def reset_run_state() -> None:
    """Clear stats, the kill switch and the wall-clock budget. Called by tests
    and by any long-lived process that runs the scrapers more than once."""
    global _RUN_STARTED_AT, _DISABLED_REASON
    _STATS.clear()
    _ALERTED.clear()
    _RUN_STARTED_AT = None
    _DISABLED_REASON = None


def _elapsed() -> float:
    global _RUN_STARTED_AT
    if _RUN_STARTED_AT is None:
        _RUN_STARTED_AT = time.monotonic()
        return 0.0
    return time.monotonic() - _RUN_STARTED_AT


def _alert_once(key: str, message: str) -> None:
    """Post an operational failure to Discord at most once per run."""
    if key in _ALERTED:
        return
    _ALERTED.add(key)
    print(f"[scraperapi] {message}", flush=True)
    if not env_flag("SCRAPERAPI_ALERT_ON_FAILURE", True):
        return
    webhook = DEAD_LETTER_WEBHOOK_URL or STATUS_WEBHOOK_URL
    if not webhook:
        return
    # Imported lazily: services.http_client imports this module's siblings and a
    # top-level import would make the dependency circular.
    from services.http_client import post_discord

    post_discord(
        {
            "embeds": [
                {
                    "title": "ScraperAPI failure",
                    "description": message[:1800],
                    "color": 0xE74C3C,
                }
            ]
        },
        webhook_url=webhook,
    )


def _disable(reason: str) -> None:
    global _DISABLED_REASON
    _DISABLED_REASON = reason
    _alert_once(f"disabled:{reason}", reason)


def disabled_reason() -> str | None:
    return _DISABLED_REASON


def _spent_credits() -> int:
    return sum(stats.credits for stats in _STATS.values())


def _check_run_limits(book: str, options: ScraperApiOptions) -> None:
    if _DISABLED_REASON:
        raise ScraperApiError(f"ScraperAPI disabled for this run: {_DISABLED_REASON}")
    if _elapsed() > RUN_BUDGET_SECONDS:
        raise ScraperApiError(
            f"ScraperAPI run budget of {RUN_BUDGET_SECONDS:.0f}s exhausted before {book} request"
        )
    if _spent_credits() + options.credits > MAX_CREDITS_PER_RUN:
        raise ScraperApiError(
            f"ScraperAPI per-run credit cap of {MAX_CREDITS_PER_RUN} reached before {book} request"
        )
    if MONTHLY_CREDIT_BUDGET > 0 and monthly_credits() + options.credits > MONTHLY_CREDIT_BUDGET:
        raise ScraperApiError(
            f"ScraperAPI monthly budget of {MONTHLY_CREDIT_BUDGET} credits reached"
            f" before {book} request"
        )


def build_params(url: str, options: ScraperApiOptions, key: str) -> dict[str, str]:
    params: dict[str, str] = {"api_key": key, "url": url}
    if options.premium:
        params["premium"] = "true"
    if options.ultra_premium:
        params["ultra_premium"] = "true"
    if options.render:
        params["render"] = "true"
    if options.keep_headers:
        params["keep_headers"] = "true"
    if options.country_code:
        params["country_code"] = options.country_code
    if options.device_type:
        params["device_type"] = options.device_type
    if options.wait_for_selector:
        params["wait_for_selector"] = options.wait_for_selector
    return params


def _username_flags(options: ScraperApiOptions) -> str:
    """Proxy mode carries request parameters as dot-separated username suffixes."""
    flags = []
    if options.premium:
        flags.append("premium=true")
    if options.ultra_premium:
        flags.append("ultra_premium=true")
    if options.render:
        flags.append("render=true")
    if options.country_code:
        flags.append(f"country_code={options.country_code}")
    if options.device_type:
        flags.append(f"device_type={options.device_type}")
    return ".".join([PROXY_USER, *flags])


def playwright_proxy(book: str = "") -> dict[str, str] | None:
    """Proxy config for ``browser.launch(proxy=...)``.

    ScraperAPI terminates TLS, so the browser context must be created with
    ``ignore_https_errors=True`` or every navigation fails on a cert error.
    """
    key = api_key()
    if not key:
        return None
    options = options_for(book)
    # Rendering is ScraperAPI-side and meaningless when we drive our own browser.
    options = replace(options, render=False, wait_for_selector="")
    return {
        "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
        "username": _username_flags(options),
        "password": key,
    }


def requests_proxies(book: str = "") -> dict[str, Any]:
    """``requests`` kwargs for proxy mode. Certificate verification is off
    because ScraperAPI presents its own certificate for the tunnelled host."""
    proxy = playwright_proxy(book)
    if not proxy:
        return {}
    url = f"http://{quote(proxy['username'], safe='.=')}:{quote(proxy['password'], safe='')}@{PROXY_HOST}:{PROXY_PORT}"
    return {"proxies": {"http": url, "https": url}, "verify": False}


def target_url(url: str, params: dict[str, Any] | None = None) -> str:
    """Fold a book's query string into the target URL.

    ScraperAPI takes the target as a single ``url`` parameter, so the book's own
    parameters have to be encoded into it rather than sent alongside ``api_key``.
    """
    if not params:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _sleep_for_attempt(attempt: int) -> None:
    delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
    time.sleep(delay + random.uniform(0, 0.5))


def fetch(
    url: str,
    book: str,
    *,
    options: ScraperApiOptions | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> requests.Response:
    """GET ``url`` through the ScraperAPI endpoint, retrying only what is worth
    retrying. Raises a ``ScraperApiError`` subclass on definitive failure."""
    key = api_key()
    if not key:
        _alert_once(
            "missing-key",
            "SCRAPERAPI_KEY is not set; retail book scrapers cannot reach protected sportsbooks.",
        )
        raise ScraperApiNotConfigured("SCRAPERAPI_KEY is not set")

    resolved = options or options_for(book)
    _check_run_limits(book, resolved)
    if resolved.keep_headers and not headers:
        resolved = replace(resolved, keep_headers=False)

    params = build_params(url, resolved, key)
    stats = _stats_for(book)
    last_status: int | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        stats.requests += 1
        started = time.monotonic()
        try:
            with _IN_FLIGHT:
                response = requests.get(
                    API_ENDPOINT,
                    params=params,
                    headers=headers if resolved.keep_headers else None,
                    timeout=timeout,
                )
        except requests.RequestException as exc:
            stats.latencies.append(time.monotonic() - started)
            if attempt == MAX_ATTEMPTS:
                stats.blocks += 1
                raise ScraperApiBlocked(f"{book}: transport error after {attempt} attempts: {exc}") from exc
            stats.retries += 1
            _sleep_for_attempt(attempt)
            continue

        stats.latencies.append(time.monotonic() - started)
        status = response.status_code
        last_status = status

        if status == 200:
            stats.successes += 1
            stats.credits += resolved.credits
            _record_monthly_credits(resolved.credits)
            print(
                f"[scraperapi] {book}: 200 in {stats.latencies[-1]:.1f}s"
                f" ({resolved.credits} credits, attempt {attempt})",
                flush=True,
            )
            return response

        if status == OUT_OF_CREDITS_STATUS:
            _disable("ScraperAPI returned 403: the monthly API credit allowance is exhausted.")
            raise ScraperApiOutOfCredits(f"{book}: out of ScraperAPI credits")
        if status == UNAUTHORIZED_STATUS:
            _disable("ScraperAPI returned 401: the API key is invalid.")
            raise ScraperApiError(f"{book}: invalid ScraperAPI key")
        if status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
            stats.blocks += 1
            raise ScraperApiBlocked(f"{book}: ScraperAPI returned {status} after {attempt} attempts")

        stats.retries += 1
        _sleep_for_attempt(attempt)

    stats.blocks += 1
    raise ScraperApiBlocked(f"{book}: ScraperAPI returned {last_status}")


def fetch_json(
    url: str,
    book: str,
    *,
    options: ScraperApiOptions | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """Fetch a book's JSON feed. A page served instead of JSON (interstitial,
    CAPTCHA, geo-block) surfaces as ``ScraperApiBlocked``, not a parse error."""
    response = fetch(url, book, options=options, headers=headers, timeout=timeout)
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        _stats_for(book).blocks += 1
        raise ScraperApiBlocked(
            f"{book}: expected JSON, got {response.headers.get('content-type', 'unknown')}"
        ) from exc


def try_fetch_json(url: str, book: str, **kwargs) -> Any | None:
    """``fetch_json`` that logs and returns None instead of raising, for callers
    that fall back to another endpoint or transport."""
    try:
        return fetch_json(url, book, **kwargs)
    except ScraperApiError as exc:
        print(f"[scraperapi] {exc}", flush=True)
        return None


async def fetch_json_async(url: str, book: str, **kwargs) -> Any | None:
    """``try_fetch_json`` on a worker thread, so many books can be in flight at
    once. ``requests`` is blocking; a thread per request is cheaper than adding an
    async HTTP dependency and keeps one retry/credit implementation."""
    return await asyncio.to_thread(try_fetch_json, url, book, **kwargs)


async def gather_json(
    targets: Sequence[tuple[str, str]],
    *,
    concurrency: int | None = None,
    **kwargs,
) -> list[Any | None]:
    """Fetch ``(url, book)`` pairs concurrently, bounded by ScraperAPI's thread
    limit. Results keep the input order; a failed fetch is ``None``, so one dead
    league or book never aborts the others."""
    if not targets:
        return []
    limit = max(1, concurrency or MAX_CONCURRENCY)
    semaphore = asyncio.Semaphore(limit)

    async def _one(url: str, book: str) -> Any | None:
        async with semaphore:
            return await fetch_json_async(url, book, **kwargs)

    return list(await asyncio.gather(*(_one(url, book) for url, book in targets)))


def gather_json_sync(targets: Sequence[tuple[str, str]], **kwargs) -> list[Any | None]:
    """``gather_json`` for the synchronous pipeline tasks."""
    return asyncio.run(gather_json(targets, **kwargs))


def _month_key() -> str:
    return get_local_now().strftime("%Y-%m")


def _record_monthly_credits(credits: int) -> None:
    """Accumulate spend per billing month so burn rate is visible without the
    ScraperAPI dashboard. Best-effort: never let bookkeeping fail a scrape."""
    if not env_flag("SCRAPERAPI_TRACK_CREDITS", True):
        return
    try:
        state = load_tracker_state(CREDIT_STATE_KEY, {}) or {}
        month = _month_key()
        if state.get("month") != month:
            state = {"month": month, "credits": 0}
        state["credits"] = int(state.get("credits", 0)) + credits
        save_tracker_state(CREDIT_STATE_KEY, state)
    except Exception as exc:  # noqa: BLE001 - telemetry must not break a scrape
        print(f"[scraperapi] credit bookkeeping skipped: {exc}", flush=True)


def monthly_credits() -> int:
    state = load_tracker_state(CREDIT_STATE_KEY, {}) or {}
    if state.get("month") != _month_key():
        return 0
    return int(state.get("credits", 0))


def stats_snapshot() -> list[tuple[str, BookStats]]:
    return sorted(_STATS.items())


def format_stats() -> str:
    if not _STATS:
        return "[scraperapi] no requests this run"
    lines = []
    for book, stats in stats_snapshot():
        lines.append(
            f"[scraperapi] {book}: {stats.successes}/{stats.requests} ok"
            f" ({stats.success_rate * 100:.0f}%), {stats.retries} retries,"
            f" {stats.blocks} blocked, {stats.credits} credits,"
            f" {stats.avg_latency:.1f}s avg"
        )
    spent_this_month = monthly_credits()
    remaining = max(MONTHLY_CREDIT_BUDGET - spent_this_month, 0) if MONTHLY_CREDIT_BUDGET > 0 else 0
    lines.append(
        f"[scraperapi] run total: {_spent_credits()} credits,"
        f" {spent_this_month} this month, {remaining} left of {MONTHLY_CREDIT_BUDGET}"
    )
    return "\n".join(lines)


def log_stats() -> None:
    try:
        print(format_stats(), flush=True)
    except Exception as exc:  # noqa: BLE001 - telemetry must not fail the job
        # This reporting line crashed the whole scraper job once already, after
        # every scraper had finished successfully.
        print(f"[scraperapi] stats reporting skipped: {exc}", flush=True)
