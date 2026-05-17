"""OddsHarvester integration — scrape free odds from OddsPortal and merge into the master cache."""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db_manager import get_master_cache, save_master_cache
from master_odds_fetcher import _merge_cache
from utils.config import env_flag

logger = logging.getLogger(__name__)

ENABLE_ODDSHARVESTER = env_flag("ENABLE_ODDSHARVESTER", False)

# ---------------------------------------------------------------------------
# Sport / league / market mappings
# ---------------------------------------------------------------------------

# OddsHarvester sport → (bot sport key, OddsHarvester league slug)
SPORT_LEAGUE_MAP: Dict[str, List[Dict[str, str]]] = {
    "basketball_nba": {"oh_sport": "basketball", "oh_league": "nba"},
    "icehockey_nhl": {"oh_sport": "ice-hockey", "oh_league": "nhl"},
    "baseball_mlb": {"oh_sport": "baseball", "oh_league": "mlb"},
}

# OddsHarvester market requests per bot sport key.
# Basketball uses "home_away" for moneyline on OddsPortal.
# Ice hockey uses "home_away" for moneyline (regulation + OT).
OH_MARKETS: Dict[str, List[str]] = {
    "basketball_nba": ["home_away"],
    "icehockey_nhl": ["home_away"],
    "baseball_mlb": ["home_away"],
}

# Normalise OddsHarvester bookmaker names → Odds API bookmaker keys.
_BOOKMAKER_KEY_MAP: Dict[str, str] = {
    "pinnacle": "pinnacle",
    "pinnacle sports": "pinnacle",
    "fanduel": "fanduel",
    "draftkings": "draftkings",
    "betmgm": "betmgm",
    "bet365": "bet365",
    "caesars": "caesars",
    "caesars sportsbook": "caesars",
    "bovada": "bovada",
    "william hill": "williamhill",
    "williamhill": "williamhill",
    "betrivers": "betrivers",
    "pointsbet": "pointsbet",
    "unibet": "unibet",
    "betway": "betway",
    "888sport": "888sport",
    "bwin": "bwin",
    "betfair": "betfair",
    "betfair exchange": "betfair_ex_eu",
    "1xbet": "onexbet",
    "marathon bet": "marathonbet",
    "marathonbet": "marathonbet",
}

# Bookmaker keys we actually care about (the bot's target books).
TARGET_BOOK_KEYS = {"pinnacle", "fanduel", "draftkings", "betmgm", "bet365", "caesars"}


def _normalise_book_key(name: str) -> Optional[str]:
    return _BOOKMAKER_KEY_MAP.get(name.strip().lower())


def _book_title(key: str) -> str:
    titles = {
        "pinnacle": "Pinnacle",
        "fanduel": "FanDuel",
        "draftkings": "DraftKings",
        "betmgm": "BetMGM",
        "bet365": "Bet365",
        "caesars": "Caesars",
        "bovada": "Bovada",
    }
    return titles.get(key, key.title())


def _synthetic_event_id(home: str, away: str, match_date: str) -> str:
    raw = f"{home}|{away}|{match_date}".lower().strip()
    return "oh_" + hashlib.md5(raw.encode()).hexdigest()[:12]


def _parse_odds_float(value: Any) -> Optional[float]:
    try:
        v = float(str(value).strip())
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def _parse_commence_time(match_date: Optional[str]) -> Optional[str]:
    """Convert OddsHarvester match_date to ISO 8601 UTC string."""
    if not match_date:
        return None
    try:
        for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(match_date, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Transform a single OddsHarvester match dict → Odds API event dict
# ---------------------------------------------------------------------------

def _transform_match(match: dict, sport_key: str) -> Optional[dict]:
    """Convert one OddsHarvester match result into a Odds-API-shaped event."""
    home = match.get("home_team")
    away = match.get("away_team")
    if not home or not away:
        return None

    match_date = match.get("match_date", "")
    commence_time = _parse_commence_time(match_date)
    event_id = _synthetic_event_id(home, away, match_date or "")

    bookmakers_by_key: Dict[str, dict] = {}

    # Process moneyline / home_away market
    for market_field in ("home_away_market", "moneyline_market", "1x2_market"):
        market_rows = match.get(market_field)
        if not market_rows or not isinstance(market_rows, list):
            continue

        for row in market_rows:
            bookie_name = row.get("bookmaker_name", "")
            bookie_key = _normalise_book_key(bookie_name)
            if not bookie_key or bookie_key not in TARGET_BOOK_KEYS:
                continue

            # OddsHarvester labels differ by market type.
            # home_away: odds_home / odds_away
            # moneyline: odds_home / odds_away  (or odds_1 / odds_2)
            # 1x2: odds_1 / odds_X / odds_2
            home_odds = _parse_odds_float(
                row.get("odds_home") or row.get("odds_1")
            )
            away_odds = _parse_odds_float(
                row.get("odds_away") or row.get("odds_2")
            )
            if home_odds is None or away_odds is None:
                continue

            if bookie_key not in bookmakers_by_key:
                bookmakers_by_key[bookie_key] = {
                    "key": bookie_key,
                    "title": _book_title(bookie_key),
                    "markets": [],
                }

            bookmakers_by_key[bookie_key]["markets"].append({
                "key": "h2h",
                "outcomes": [
                    {"name": home, "price": home_odds},
                    {"name": away, "price": away_odds},
                ],
            })

    # Process over/under markets
    for field_key, field_data in match.items():
        if not field_key.startswith("over_under_") or not field_key.endswith("_market"):
            continue
        if not isinstance(field_data, list):
            continue

        # Extract the point value from the field name, e.g. "over_under_5_5_market" → 5.5
        parts = field_key.replace("_market", "").replace("over_under_", "").replace("games_", "")
        try:
            point = float(parts.replace("_", "."))
        except ValueError:
            continue

        for row in field_data:
            bookie_name = row.get("bookmaker_name", "")
            bookie_key = _normalise_book_key(bookie_name)
            if not bookie_key or bookie_key not in TARGET_BOOK_KEYS:
                continue

            over_odds = _parse_odds_float(row.get("odds_over") or row.get("odds_1"))
            under_odds = _parse_odds_float(row.get("odds_under") or row.get("odds_2"))
            if over_odds is None or under_odds is None:
                continue

            if bookie_key not in bookmakers_by_key:
                bookmakers_by_key[bookie_key] = {
                    "key": bookie_key,
                    "title": _book_title(bookie_key),
                    "markets": [],
                }

            bookmakers_by_key[bookie_key]["markets"].append({
                "key": "totals",
                "outcomes": [
                    {"name": "Over", "price": over_odds, "point": point},
                    {"name": "Under", "price": under_odds, "point": point},
                ],
            })

    if not bookmakers_by_key:
        return None

    return {
        "id": event_id,
        "sport_key": sport_key,
        "commence_time": commence_time,
        "home_team": home,
        "away_team": away,
        "bookmakers": list(bookmakers_by_key.values()),
    }


# ---------------------------------------------------------------------------
# Async scrape orchestrator
# ---------------------------------------------------------------------------

async def _scrape_sport(sport_key: str) -> List[dict]:
    """Run OddsHarvester for one sport and return Odds-API-shaped events."""
    from oddsharvester.core.scraper_app import run_scraper
    from oddsharvester.utils.command_enum import CommandEnum

    cfg = SPORT_LEAGUE_MAP.get(sport_key)
    if not cfg:
        return []

    markets = OH_MARKETS.get(sport_key, ["home_away"])
    logger.info(
        "OddsHarvester: scraping %s (sport=%s, league=%s, markets=%s)",
        sport_key, cfg["oh_sport"], cfg["oh_league"], markets,
    )

    result = await run_scraper(
        command=CommandEnum.UPCOMING_MATCHES,
        sport=cfg["oh_sport"],
        leagues=[cfg["oh_league"]],
        markets=markets,
        headless=True,
        request_delay=1.5,
        concurrency_tasks=2,
    )

    if not result or not result.success:
        logger.warning("OddsHarvester: no data for %s", sport_key)
        return []

    events = []
    for match in result.success:
        event = _transform_match(match, sport_key)
        if event:
            events.append(event)

    logger.info(
        "OddsHarvester: %s → %d matches scraped, %d events with usable odds",
        sport_key, len(result.success), len(events),
    )
    return events


async def _run_harvester() -> Dict[str, Any]:
    """Scrape all configured sports and merge into the master cache."""
    cache = get_master_cache() or {}
    total_events = 0
    sports_scraped = 0

    for sport_key in SPORT_LEAGUE_MAP:
        try:
            events = await _scrape_sport(sport_key)
            if events:
                _merge_cache(cache, sport_key, events)
                total_events += len(events)
                sports_scraped += 1
        except Exception as exc:
            logger.error("OddsHarvester: %s failed: %s", sport_key, exc)

    if total_events:
        save_master_cache(cache)
        logger.info("OddsHarvester: merged %d events across %d sports into cache", total_events, sports_scraped)

    return {
        "detail": f"oddsharvester scraped {total_events} events from {sports_scraped} sports",
        "count": total_events,
        "label": "updates",
    }


# ---------------------------------------------------------------------------
# Sync entry point (called by the pipeline task runner)
# ---------------------------------------------------------------------------

def scrape_oddsharvester() -> Dict[str, Any]:
    """Synchronous wrapper for the async OddsHarvester pipeline."""
    if not ENABLE_ODDSHARVESTER:
        return {"detail": "oddsharvester disabled", "count": 0, "label": "updates"}
    return asyncio.run(_run_harvester())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(scrape_oddsharvester())
