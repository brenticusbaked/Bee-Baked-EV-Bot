"""PrizePicks projections scraper.

PrizePicks serves its board from ``api.prizepicks.com/projections`` in JSON:API
form — projections in ``data``, players in ``included`` — one request per league,
fetched concurrently. It sits behind Cloudflare and answers 403 to a datacentre
IP, which is exactly what ScraperAPI's residential pool is for.

**PrizePicks publishes no price.** A standard projection carries a
``line_score`` and nothing else: the payout comes from the parlay structure the
bettor builds, not from the leg. So a leg cannot be turned into decimal odds
without inventing a number, and Rule 1 forbids feeding an invented probability
into the EV formula. This module therefore:

* always reports the board and how it compares with the cached market line, and
* only persists prices when ``PRIZEPICKS_LEG_DECIMAL_PRICE`` states the per-leg
  price that actually applies to the account being used.

The line comparison is the part that stands on its own: a projection that sits a
full point off the market line is an edge regardless of the payout, and it is
computed from two real numbers.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from db_manager import get_master_cache
from services.dfs_normalize import (
    PropLine,
    build_events,
    events_by_sport,
    market_for_stat,
    sport_for_league,
)
from services.odds_scraper_ingest import ingest_events
from services.scraper_api_client import gather_json_sync, options_for, try_fetch_json
from utils.config import env_flag

BOOK_KEY = "prizepicks"
BOOK_TITLE = "PrizePicks"
PROJECTIONS_URL = os.getenv("PRIZEPICKS_PROJECTIONS_URL", "https://api.prizepicks.com/projections")
REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://app.prizepicks.com/",
    "User-Agent": os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
}
# PrizePicks addresses leagues by numeric id. The ids are stable but not
# documented by PrizePicks, so they stay overridable without a deploy.
DEFAULT_LEAGUE_IDS = "MLB:2,NBA:7,NHL:8,NFL:9"
PER_PAGE = os.getenv("PRIZEPICKS_PER_PAGE", "250")
# Demon/goblin projections are deliberately mispriced against a different payout
# multiplier, so mixing them with standard lines would compare unlike things.
STANDARD_ODDS_TYPE = "standard"
# PrizePicks' Cloudflare policy answers 429 to parallel requests from the same
# pool and then keeps failing, so leagues go out one at a time by default.
CONCURRENCY = int(os.getenv("PRIZEPICKS_CONCURRENCY", "1"))


def league_ids() -> dict[str, str]:
    raw = os.getenv("PRIZEPICKS_LEAGUE_IDS", DEFAULT_LEAGUE_IDS)
    leagues: dict[str, str] = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        league, league_id = item.split(":", 1)
        league, league_id = league.strip(), league_id.strip()
        if league and league_id:
            leagues[league.upper()] = league_id
    return leagues


def leg_decimal_price() -> float | None:
    """The per-leg decimal price for this account, or ``None`` when unknown.

    Unset means "do not persist PrizePicks as a priced book": there is no public
    per-leg price to fall back on.
    """
    raw = (os.getenv("PRIZEPICKS_LEG_DECIMAL_PRICE") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        print(f"[prizepicks] ignoring non-numeric PRIZEPICKS_LEG_DECIMAL_PRICE={raw!r}", flush=True)
        return None
    if value <= 1.0:
        print("[prizepicks] ignoring PRIZEPICKS_LEG_DECIMAL_PRICE <= 1.0", flush=True)
        return None
    return value


def _targets() -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for league_id in league_ids().values():
        url = f"{PROJECTIONS_URL}?league_id={league_id}&per_page={PER_PAGE}&single_stat=true"
        targets.append((url, BOOK_KEY))
    return targets


def _options(render: bool):
    """PrizePicks checks the Referer/User-Agent pair, so headers are forwarded
    (``keep_headers``) rather than replaced by ScraperAPI's own."""
    return replace(options_for(BOOK_KEY), keep_headers=True, render=render)


def _fetch_leagues() -> list[Any]:
    """Fetch each league's board, escalating to a rendered request only for the
    leagues the plain JSON call could not get.

    Cloudflare serves a JS interstitial rather than JSON when it is unhappy, and
    rendering costs 25 credits against 10, so it is a per-league fallback instead
    of the default.
    """
    targets = _targets()
    payloads = gather_json_sync(
        targets,
        concurrency=CONCURRENCY,
        options=_options(render=False),
        headers=REQUEST_HEADERS,
    )

    retry = [index for index, payload in enumerate(payloads) if payload is None]
    if not retry or not env_flag("PRIZEPICKS_RENDER_FALLBACK", True):
        return payloads

    print(f"[prizepicks] retrying {len(retry)} league(s) with rendering", flush=True)
    rendered = _options(render=True)
    for index in retry:
        url = targets[index][0]
        payloads[index] = try_fetch_json(url, BOOK_KEY, options=rendered, headers=REQUEST_HEADERS)
    return payloads


def _players(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    players: dict[str, dict[str, str]] = {}
    for item in payload.get("included") or []:
        if not isinstance(item, dict) or item.get("type") != "new_player":
            continue
        attributes = item.get("attributes") or {}
        name = str(attributes.get("display_name") or attributes.get("name") or "").strip()
        if not name:
            continue
        players[str(item.get("id") or "")] = {
            "name": name,
            "league": str(attributes.get("league") or ""),
            "team": str(attributes.get("team") or ""),
        }
    return players


def parse_lines(payload: Any, price: float | None) -> list[PropLine]:
    """Turn one league's JSON:API response into ``PropLine``s.

    ``price`` is applied to both sides: a pick'em leg is symmetric, the bettor
    chooses More or Less at the same payout.
    """
    if not isinstance(payload, dict):
        return []
    players = _players(payload)
    lines: list[PropLine] = []

    for projection in payload.get("data") or []:
        if not isinstance(projection, dict):
            continue
        attributes = projection.get("attributes") or {}
        if str(attributes.get("odds_type") or STANDARD_ODDS_TYPE).strip().lower() != STANDARD_ODDS_TYPE:
            continue
        relationships = projection.get("relationships") or {}
        player_ref = (relationships.get("new_player") or {}).get("data") or {}
        player = players.get(str(player_ref.get("id") or ""))
        if not player:
            continue
        sport_key = sport_for_league(player["league"] or attributes.get("league"))
        market_key = market_for_stat(attributes.get("stat_type"))
        if not sport_key or not market_key:
            continue
        try:
            point = float(attributes.get("line_score"))
        except (TypeError, ValueError):
            continue

        lines.append(
            PropLine(
                player=player["name"],
                sport_key=sport_key,
                market_key=market_key,
                point=point,
                over_price=price,
                under_price=price,
                league=player["league"],
            )
        )
    return lines


def line_differentials(lines: list[PropLine], cache: dict[str, list]) -> list[tuple[PropLine, float, float]]:
    """Compare each projection with the same market's cached line.

    Returns ``(line, market_point, difference)`` sorted by the largest gap. This
    needs no payout assumption: a projection two points below the market line is
    a better number than the market's, whatever the payout turns out to be.
    """
    differentials: list[tuple[PropLine, float, float]] = []
    for line in lines:
        for event in cache.get(line.sport_key) or []:
            if not isinstance(event, dict):
                continue
            for book in event.get("bookmakers") or []:
                if str(book.get("key") or "").lower() == BOOK_KEY:
                    continue
                for market in book.get("markets") or []:
                    if str(market.get("key") or "") != line.market_key:
                        continue
                    for outcome in market.get("outcomes") or []:
                        if str(outcome.get("description") or "").strip().lower() != line.player.strip().lower():
                            continue
                        try:
                            market_point = float(outcome.get("point"))
                        except (TypeError, ValueError):
                            continue
                        difference = line.point - market_point
                        if abs(difference) >= 0.5:
                            differentials.append((line, market_point, difference))
                        break
    differentials.sort(key=lambda item: abs(item[2]), reverse=True)
    return differentials


def scrape_prizepicks() -> dict[str, Any]:
    if not env_flag("ENABLE_PRIZEPICKS_SCRAPER", True):
        return {"detail": "prizepicks scraper disabled", "count": 0, "label": "props"}

    price = leg_decimal_price()
    payloads = _fetch_leagues()
    lines: list[PropLine] = []
    for payload in payloads:
        lines.extend(parse_lines(payload, price))
    if not lines and not any(payload for payload in payloads):
        detail = "prizepicks unavailable: every league request was blocked"
        print(f"[prizepicks] {detail}", flush=True)
        return {"detail": detail, "count": 0, "label": "props"}

    cache = get_master_cache() or {}
    differentials = line_differentials(lines, cache)
    for line, market_point, difference in differentials[:5]:
        print(
            f"[prizepicks] line gap {difference:+.1f}: {line.player} {line.market_key}"
            f" {line.point} vs market {market_point}",
            flush=True,
        )

    matched = 0
    if price is None:
        detail = (
            f"prizepicks read-only | {len(lines)} projections"
            f" | {len(differentials)} lines off the market by >=0.5"
            " | set PRIZEPICKS_LEG_DECIMAL_PRICE to price them for EV"
        )
    else:
        events, counts = build_events(BOOK_KEY, BOOK_TITLE, lines, cache)
        for sport_key, sport_events in events_by_sport(events).items():
            if sport_key:
                ingest_events(sport_key, sport_events)
        matched = counts["matched"]
        detail = (
            f"prizepicks scrape complete | {len(lines)} projections at {price} per leg"
            f" | {counts['matched']} matched to cached events"
            f" | {counts['unmatched']} without a cached counterpart"
            f" | {len(differentials)} lines off the market by >=0.5"
        )

    print(f"[prizepicks] {detail}", flush=True)
    return {"detail": detail, "count": matched or len(differentials), "label": "props"}


if __name__ == "__main__":
    scrape_prizepicks()
