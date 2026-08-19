"""Normalization for DFS/pick'em player-prop feeds (PrizePicks, Underdog).

A DFS feed names its own games and players and knows nothing about The Odds API
event ids the master cache is keyed on. Rather than mapping team abbreviations
(``LAD`` vs ``Los Angeles Dodgers``) this module matches on the *player*: a DFS
prop is only ingested when the cache already prices that player somewhere, which
is exactly the condition under which the +EV scanner can compare the two sides.
Props with no counterpart are dropped, because an unmatchable line has no sharp
baseline and Rule 1 keeps Pinnacle as the sole source of fair value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.bet_logic import normalize_team_fragment

# DFS platforms label their leagues; the cache is keyed by Odds API sport keys.
SPORT_BY_LEAGUE: dict[str, str] = {
    "mlb": "baseball_mlb",
    "nba": "basketball_nba",
    "wnba": "basketball_wnba",
    "nfl": "americanfootball_nfl",
    "nhl": "icehockey_nhl",
}

# DFS stat labels vary per platform ("3-PT Made" vs "three_points_made"), so both
# sides of this map are slugged before lookup. Only stats that exist as an Odds
# API market are listed: a stat we cannot line up against a sharp price is not
# useful to the scanner.
MARKET_BY_STAT: dict[str, str] = {
    # MLB
    "hits": "batter_hits",
    "total bases": "batter_total_bases",
    "home runs": "batter_home_runs",
    "hitter home runs": "batter_home_runs",
    "runs": "batter_runs_scored",
    "runs scored": "batter_runs_scored",
    "rbis": "batter_rbis",
    "hits runs rbis": "batter_hits_runs_rbis",
    "hitter strikeouts": "batter_strikeouts",
    "pitcher strikeouts": "pitcher_strikeouts",
    "strikeouts": "pitcher_strikeouts",
    "pitching outs": "pitcher_outs",
    "pitcher outs": "pitcher_outs",
    "earned runs allowed": "pitcher_earned_runs",
    "hits allowed": "pitcher_hits_allowed",
    "walks allowed": "pitcher_walks_allowed",
    # Basketball
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
    "pts rebs asts": "player_points_rebounds_assists",
    "points rebounds assists": "player_points_rebounds_assists",
    "pts rebs": "player_points_rebounds",
    "points rebounds": "player_points_rebounds",
    "pts asts": "player_points_assists",
    "points assists": "player_points_assists",
    "rebs asts": "player_rebounds_assists",
    "rebounds assists": "player_rebounds_assists",
    "3 pt made": "player_threes",
    "three points made": "player_threes",
    "threes made": "player_threes",
    "blocks": "player_blocks",
    "steals": "player_steals",
    "turnovers": "player_turnovers",
    # Football
    "pass yards": "player_pass_yds",
    "passing yards": "player_pass_yds",
    "rush yards": "player_rush_yds",
    "rushing yards": "player_rush_yds",
    "receiving yards": "player_receiving_yds",
    "receptions": "player_receptions",
    # Hockey
    "goals": "player_goals",
    "shots on goal": "player_shots_on_goal",
    "shots on target": "player_shots_on_goal",
}

OVER = "Over"
UNDER = "Under"


def slug(value: object) -> str:
    """Collapse a DFS label to a comparable token: ``"3-PT Made" -> "3 pt made"``."""
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return " ".join(text.split())


def sport_for_league(league: object) -> str | None:
    return SPORT_BY_LEAGUE.get(slug(league).replace(" ", ""))


def market_for_stat(stat: object) -> str | None:
    return MARKET_BY_STAT.get(slug(stat))


@dataclass(frozen=True)
class PropLine:
    """One DFS over/under, already mapped onto an Odds API market key.

    ``over_price``/``under_price`` are decimal odds and are ``None`` when the
    platform publishes no price for that side (pick'em products often do not).
    """

    player: str
    sport_key: str
    market_key: str
    point: float
    over_price: float | None = None
    under_price: float | None = None
    league: str = ""

    @property
    def is_priced(self) -> bool:
        return self.over_price is not None or self.under_price is not None


def _player_names(event: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            for outcome in market.get("outcomes") or []:
                description = outcome.get("description")
                if description:
                    names.add(normalize_team_fragment(str(description)))
    return names


def index_players(cache: dict[str, list], sport_key: str) -> dict[str, dict[str, Any]]:
    """Map every player the cache already prices for ``sport_key`` to their event."""
    index: dict[str, dict[str, Any]] = {}
    for event in cache.get(sport_key) or []:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        stub = {
            "id": event_id,
            "home_team": str(event.get("home_team") or ""),
            "away_team": str(event.get("away_team") or ""),
            "commence_time": str(event.get("commence_time") or ""),
            "sport_key": sport_key,
        }
        for name in _player_names(event):
            index.setdefault(name, stub)
    return index


def _outcomes(line: PropLine) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for name, price in ((OVER, line.over_price), (UNDER, line.under_price)):
        if price is None:
            continue
        outcomes.append(
            {
                "name": name,
                "description": line.player,
                "price": round(float(price), 4),
                "point": line.point,
            }
        )
    return outcomes


def build_events(
    book_key: str,
    book_title: str,
    lines: Iterable[PropLine],
    cache: dict[str, list],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fold DFS props into master-cache events, one bookmaker entry per event.

    Returns the events plus counters (matched/unmatched/unpriced) so a scraper can
    report how much of a feed actually reached the scanner.
    """
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    events: dict[str, dict[str, Any]] = {}
    counts = {"matched": 0, "unmatched": 0, "unpriced": 0}

    for line in lines:
        if not line.is_priced:
            counts["unpriced"] += 1
            continue
        index = indexes.get(line.sport_key)
        if index is None:
            index = index_players(cache, line.sport_key)
            indexes[line.sport_key] = index
        stub = index.get(normalize_team_fragment(line.player))
        if not stub:
            counts["unmatched"] += 1
            continue

        counts["matched"] += 1
        event = events.setdefault(
            stub["id"],
            {
                **stub,
                "bookmakers": [
                    {
                        "key": book_key,
                        "title": book_title,
                        "markets": [],
                    }
                ],
            },
        )
        markets = event["bookmakers"][0]["markets"]
        market = next((item for item in markets if item["key"] == line.market_key), None)
        if market is None:
            market = {
                "key": line.market_key,
                "last_update": datetime.now(timezone.utc).isoformat(),
                "outcomes": [],
            }
            markets.append(market)
        market["outcomes"].extend(_outcomes(line))

    return list(events.values()), counts


def events_by_sport(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(str(event.get("sport_key") or ""), []).append(event)
    return grouped
