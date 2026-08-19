"""Underdog Fantasy pick'em scraper.

Underdog's pick'em catalogue comes from one JSON endpoint
(``/beta/v5/over_under_lines``) that returns the whole board in a single
response: no per-league fan-out, no pagination, one ScraperAPI request for every
sport. Unlike most DFS products Underdog publishes a real per-side price
(``american_price``), which is what makes it usable as a bookmaker for the EV
math instead of a reference line.

The payload is relational: ``players`` and ``appearances`` have to be joined onto
``over_under_lines`` through ``over_under.appearance_stat.appearance_id`` to learn
which player a line belongs to.
"""

from __future__ import annotations

import os
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
from services.scraper_api_client import ScraperApiError, fetch_json
from utils.odds import american_to_decimal

BOOK_KEY = "underdog"
BOOK_TITLE = "Underdog Fantasy"
OVER_UNDER_URL = os.getenv(
    "UNDERDOG_OVER_UNDER_URL",
    "https://api.underdogfantasy.com/beta/v5/over_under_lines",
)
REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
}
# Underdog marks a line suspended while a game is stopped or a player is
# questionable; those prices are not takeable.
SKIPPED_STATUSES = {"suspended", "cancelled", "settled"}
HIGHER_CHOICES = {"higher", "over", "more"}
LOWER_CHOICES = {"lower", "under", "less"}


def _players_by_appearance(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Join ``appearances`` back onto ``players`` so a line can name its player."""
    players: dict[str, dict[str, Any]] = {}
    for player in payload.get("players") or []:
        player_id = str(player.get("id") or "").strip()
        if not player_id:
            continue
        full_name = " ".join(
            part
            for part in (str(player.get("first_name") or ""), str(player.get("last_name") or ""))
            if part
        ).strip()
        players[player_id] = {
            "name": full_name,
            "sport_id": str(player.get("sport_id") or ""),
        }

    by_appearance: dict[str, dict[str, str]] = {}
    for appearance in payload.get("appearances") or []:
        appearance_id = str(appearance.get("id") or "").strip()
        player = players.get(str(appearance.get("player_id") or "").strip())
        if appearance_id and player and player["name"]:
            by_appearance[appearance_id] = player
    return by_appearance


def _line_value(line: dict[str, Any], over_under: dict[str, Any], option: dict[str, Any]) -> float | None:
    """Underdog has moved the O/U number between ``stat_value`` on the line, the
    ``over_under`` block and the option itself, so all three are checked."""
    for source in (option, line, over_under, over_under.get("appearance_stat") or {}):
        if not isinstance(source, dict):
            continue
        raw = source.get("stat_value")
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _decimal_price(option: dict[str, Any]) -> float | None:
    """Only real published prices are used. A pick'em option without a price is
    dropped rather than being assigned a payout-derived guess, which would feed
    an invented number into the EV formula."""
    raw = option.get("decimal_price")
    if raw is not None:
        try:
            value = float(raw)
            if value > 1.0:
                return value
        except (TypeError, ValueError):
            pass
    raw = option.get("american_price")
    if raw in (None, ""):
        return None
    try:
        return float(american_to_decimal(float(raw)))
    except (TypeError, ValueError):
        return None


def parse_lines(payload: dict[str, Any]) -> list[PropLine]:
    """Normalize the raw catalogue into ``PropLine``s the cache can accept."""
    if not isinstance(payload, dict):
        return []
    by_appearance = _players_by_appearance(payload)
    lines: list[PropLine] = []

    for raw_line in payload.get("over_under_lines") or []:
        if not isinstance(raw_line, dict):
            continue
        if str(raw_line.get("status") or "").strip().lower() in SKIPPED_STATUSES:
            continue
        over_under = raw_line.get("over_under") or {}
        appearance_stat = over_under.get("appearance_stat") or {}
        player = by_appearance.get(str(appearance_stat.get("appearance_id") or "").strip())
        if not player:
            continue
        sport_key = sport_for_league(player["sport_id"])
        market_key = market_for_stat(appearance_stat.get("display_stat") or appearance_stat.get("stat"))
        if not sport_key or not market_key:
            continue

        over_price: float | None = None
        under_price: float | None = None
        point: float | None = None
        for option in raw_line.get("options") or []:
            if not isinstance(option, dict):
                continue
            choice = str(option.get("choice") or "").strip().lower()
            price = _decimal_price(option)
            if price is None:
                continue
            if point is None:
                point = _line_value(raw_line, over_under, option)
            if choice in HIGHER_CHOICES:
                over_price = price
            elif choice in LOWER_CHOICES:
                under_price = price

        if point is None:
            continue
        lines.append(
            PropLine(
                player=player["name"],
                sport_key=sport_key,
                market_key=market_key,
                point=point,
                over_price=over_price,
                under_price=under_price,
                league=player["sport_id"],
            )
        )
    return lines


def scrape_underdog() -> dict[str, Any]:
    try:
        payload = fetch_json(OVER_UNDER_URL, BOOK_KEY, headers=REQUEST_HEADERS)
    except ScraperApiError as exc:
        print(f"[underdog] {exc}", flush=True)
        return {"detail": f"underdog unavailable: {exc}", "count": 0, "label": "props"}

    lines = parse_lines(payload)
    events, counts = build_events(BOOK_KEY, BOOK_TITLE, lines, get_master_cache() or {})
    for sport_key, sport_events in events_by_sport(events).items():
        if sport_key:
            ingest_events(sport_key, sport_events)

    detail = (
        f"underdog scrape complete | {len(lines)} priced lines"
        f" | {counts['matched']} matched to cached events"
        f" | {counts['unmatched']} without a cached counterpart"
        f" | {counts['unpriced']} unpriced"
    )
    print(f"[underdog] {detail}", flush=True)
    return {"detail": detail, "count": counts["matched"], "label": "props"}


if __name__ == "__main__":
    scrape_underdog()
