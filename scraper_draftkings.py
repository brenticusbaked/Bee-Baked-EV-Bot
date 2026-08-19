import json
import os
import re
from dataclasses import replace
from typing import Dict, Iterable, List, Optional

from db_manager import get_master_cache, load_tracker_state, save_tracker_state
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import post_discord
from services.odds_reference import format_pinnacle_spread_reference
from services.odds_scraper_ingest import ingest_events
from services.scraper_api_client import (
    ScraperApiError,
    fetch,
    options_for,
    target_url,
)
from utils.odds import american_to_decimal


BOOK_KEY = "draftkings"
DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
TRACKER_FILE = "dk_lines.json"
STATE_KEY = "tracker_draftkings_nba"

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)
# DraftKings addresses leagues by page path and numeric league (event group) id.
# Both are overridable so an out-of-season league (NBA in August) can be pointed
# at a live one without a deploy.
DK_PAGE_URL = os.getenv(
    "DRAFTKINGS_PAGE_URL", "https://sportsbook.draftkings.com/leagues/baseball/mlb"
)
# The sportscontent offer service that actually feeds the league page. The older
# ``/sites/US-NJ-SB/api/v1/eventgroup/{id}/full`` route is retired and 301s to the
# homepage, which reads as a successful scrape returning no lines.
DK_MARKETS_URL = os.getenv(
    "DRAFTKINGS_MARKETS_URL",
    "https://sportsbook-nash.draftkings.com/sites/US-SB/api/sportscontent"
    "/controldata/league/leagueSubcategory/v1/markets",
)
# League ids are DraftKings-internal and go stale per league, and a stale id
# returns an eventless payload that reads exactly like a scrape failure, so
# ``_discover_league_ids`` reads the live ids off the league page instead.
DK_LEAGUE_IDS: list[str] = [
    league.strip()
    for league in os.getenv(
        "DRAFTKINGS_LEAGUE_IDS",
        os.getenv("DRAFTKINGS_EVENT_GROUPS", os.getenv("DRAFTKINGS_EVENT_GROUP", "")),
    ).split(",")
    if league.strip()
]
MAX_DISCOVERED_LEAGUES = 3

# The league the configured page points at, so scraped lines are ingested and
# compared against the sharp baseline for the right sport.
DK_SPORT_BY_PATH = {
    "baseball/mlb": "baseball_mlb",
    "basketball/nba": "basketball_nba",
    "basketball/wnba": "basketball_wnba",
    "football/nfl": "americanfootball_nfl",
    "hockey/nhl": "icehockey_nhl",
}

# DraftKings market names, per league, for the three main markets. Anything else
# the offer service returns (period, alternate or player markets) is dropped: the
# master cache holds one price per outcome per market, so an alternate line would
# overwrite the main-line price the scanner de-vigs against.
DK_MARKET_KEYS = {
    "moneyline": "h2h",
    "run line": "spreads",
    "spread": "spreads",
    "puck line": "spreads",
    "point spread": "spreads",
    "total": "totals",
    "total runs": "totals",
    "total points": "totals",
    "total goals": "totals",
}


def _sport_key_for_page(page_url: str) -> str:
    lowered = page_url.lower()
    for path, sport_key in DK_SPORT_BY_PATH.items():
        if path in lowered:
            return sport_key
    return "baseball_mlb"


def _league_slug_for_page(page_url: str) -> str:
    """The league slug DraftKings labels its own nav entries with ("mlb")."""
    slug = os.getenv("DRAFTKINGS_LEAGUE_SLUG", "").strip().lower()
    if slug:
        return slug
    parts = [part for part in page_url.lower().split("?")[0].split("/") if part]
    return parts[-1] if parts else ""


DK_SPORT_KEY = os.getenv("DRAFTKINGS_SPORT_KEY", "") or _sport_key_for_page(DK_PAGE_URL)
DK_LEAGUE_SLUG = _league_slug_for_page(DK_PAGE_URL)


def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)


def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)


def _pinnacle_reference(current: dict) -> str:
    return format_pinnacle_spread_reference(
        get_master_cache() or {},
        DK_SPORT_KEY,
        str(current.get("matchup", "")),
        str(current.get("team", "")),
    )


def _market_params(league_id: str) -> Dict[str, str]:
    """Query the league's primary markets without pinning a subcategory id.

    The site itself filters on a per-league ``subCategoryId``; filtering on the
    ``PrimaryMarket`` tag instead returns the same moneyline/spread/total set for
    every league, so no second lookup is needed to translate a league id.
    """
    return {
        "isBatchable": "false",
        "templateVars": league_id,
        "eventsQuery": f"$filter=leagueId eq '{league_id}'",
        "marketsQuery": "$filter=tags/any(t: t eq 'PrimaryMarket')",
        "include": "Events",
        "entity": "events",
    }


def _looks_like_direct_dk_payload(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    markets = payload.get("markets")
    selections = payload.get("selections")
    return isinstance(markets, list) and bool(markets) and isinstance(selections, list)


def _decode_possible_json(text: str):
    text = (text or "").strip()
    if not text:
        return None

    candidates = [text]
    pre_match = re.search(r"<pre[^>]*>\s*(\{.*\})\s*</pre>", text, flags=re.DOTALL | re.IGNORECASE)
    if pre_match:
        candidates.append(pre_match.group(1))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _extract_price(selection: dict) -> Optional[float]:
    """Decimal price for a selection, preferring the values DK sends as numbers.

    ``displayOdds.american`` is formatted for display and uses a Unicode minus
    sign, so it is only parsed as a last resort and after normalising the sign.
    """
    raw = selection.get("trueOdds")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass

    display = selection.get("displayOdds")
    if isinstance(display, dict):
        decimal = display.get("decimal")
        if decimal is not None:
            try:
                return float(decimal)
            except (TypeError, ValueError):
                pass
        american = display.get("american")
        if american is not None:
            normalized = str(american).replace("\u2212", "-").replace("+", "").strip()
            try:
                return float(american_to_decimal(int(normalized)))
            except (TypeError, ValueError):
                pass
    return None


def _event_meta(event: dict) -> Dict[str, str]:
    name = str(event.get("name") or "").strip()
    home_team, away_team = "", ""
    for participant in event.get("participants") or []:
        if not isinstance(participant, dict):
            continue
        role = str(participant.get("venueRole") or "").lower()
        if role == "home":
            home_team = str(participant.get("name") or "").strip()
        elif role == "away":
            away_team = str(participant.get("name") or "").strip()
    if not home_team or not away_team:
        away_team, home_team = _split_matchup(name)
    return {
        "matchup": name,
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": str(event.get("startEventDate") or "").strip(),
    }


def _split_matchup(name: str) -> tuple[str, str]:
    for separator in (r"\s+@\s+", r"\s+at\s+", r"\s+vs\.?\s+"):
        parts = re.split(separator, name, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
    return name, name


def _market_key(market: dict) -> Optional[str]:
    market_type = market.get("marketType")
    names = [market.get("name")]
    if isinstance(market_type, dict):
        names.append(market_type.get("name"))
    for name in names:
        key = DK_MARKET_KEYS.get(str(name or "").strip().lower())
        if key:
            return key
    return None


def _iter_offer_groups(payload: dict) -> Iterable[dict]:
    """Markets paired with their selections, for callers that want flat offers."""
    selections_by_market: Dict[str, List[dict]] = {}
    for selection in payload.get("selections") or []:
        if isinstance(selection, dict):
            market_id = str(selection.get("marketId") or "")
            selections_by_market.setdefault(market_id, []).append(selection)

    for market in payload.get("markets") or []:
        if not isinstance(market, dict):
            continue
        yield {**market, "selections": selections_by_market.get(str(market.get("id") or ""), [])}


def _parse_market_lines(payload: dict) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Group the payload into ``{market_key: {unique_key: line}}``."""
    if not isinstance(payload, dict):
        return {}

    meta_by_event = {
        str(event.get("id") or ""): _event_meta(event)
        for event in payload.get("events") or []
        if isinstance(event, dict) and event.get("id")
    }

    by_market: Dict[str, Dict[str, Dict[str, object]]] = {}
    for market in _iter_offer_groups(payload):
        market_key = _market_key(market)
        event_id = str(market.get("eventId") or "")
        meta = meta_by_event.get(event_id)
        if not market_key or not meta:
            continue

        for selection in market.get("selections", []):
            team = str(selection.get("label") or "").strip()
            price = _extract_price(selection)
            if not team or not price:
                continue

            point = selection.get("points")
            if market_key != "h2h" and point is None:
                continue

            lines = by_market.setdefault(market_key, {})
            lines.setdefault(
                f"{event_id}_{team}",
                {
                    "event_id": event_id,
                    "matchup": meta["matchup"],
                    "home_team": meta["home_team"],
                    "away_team": meta["away_team"],
                    "commence_time": meta["commence_time"],
                    "team": team,
                    "line": None if market_key == "h2h" else point,
                    "price": price,
                },
            )

    return by_market


def _parse_spread_lines(payload: dict) -> Dict[str, Dict[str, object]]:
    return _parse_market_lines(payload).get("spreads", {})


def _to_ingest_events(
    by_market: Dict[str, Dict[str, Dict[str, object]]],
) -> List[Dict[str, object]]:
    by_event: Dict[str, Dict[str, object]] = {}
    for market_key, lines in by_market.items():
        for line in lines.values():
            event_id = str(line.get("event_id", ""))
            if not event_id:
                continue
            event = by_event.setdefault(
                event_id,
                {
                    "id": event_id,
                    "home_team": line.get("home_team", ""),
                    "away_team": line.get("away_team", ""),
                    "commence_time": line.get("commence_time", ""),
                    "bookmakers": [
                        {"key": "draftkings", "title": "DraftKings", "markets": []}
                    ],
                },
            )
            markets = event["bookmakers"][0]["markets"]
            market = next((m for m in markets if m["key"] == market_key), None)
            if market is None:
                market = {"key": market_key, "outcomes": []}
                markets.append(market)
            market["outcomes"].append(
                {
                    "name": line.get("team", ""),
                    "price": line.get("price"),
                    "point": line.get("line"),
                }
            )
    return list(by_event.values())


def _discover_league_ids() -> list[str]:
    """League ids advertised by the league page's own navigation state.

    The page embeds every league DraftKings offers, so the id has to be matched
    by slug: taking the first ids the page mentions returned AFL for an MLB page.
    """
    try:
        response = fetch(
            DK_PAGE_URL,
            BOOK_KEY,
            options=replace(options_for(BOOK_KEY), keep_headers=True),
            headers={"User-Agent": USER_AGENT},
        )
    except ScraperApiError as exc:
        print(f"DraftKings league discovery failed: {exc}")
        return []

    html = response.text or ""
    found: list[str] = []
    patterns = (
        r'"eventGroupId":\s*"?(\d{2,8})"?[^{}]{0,240}?"nameIdentifier":"%s"',
        r'"nameIdentifier":"%s"[^{}]{0,240}?"eventGroupId":\s*"?(\d{2,8})"?',
    )
    for pattern in patterns:
        if not DK_LEAGUE_SLUG:
            break
        for league in re.findall(pattern % re.escape(DK_LEAGUE_SLUG), html, flags=re.IGNORECASE):
            if league not in found:
                found.append(league)

    if found:
        print(
            f"DraftKings discovered {DK_LEAGUE_SLUG or 'league'} id(s) from "
            f"{DK_PAGE_URL}: {found[:MAX_DISCOVERED_LEAGUES]}"
        )
    else:
        print(
            f"DraftKings page advertised no '{DK_LEAGUE_SLUG}' league id "
            f"({len(html)} bytes)"
        )
    return found[:MAX_DISCOVERED_LEAGUES]


def _fetch_league_payload(league_id: str, options, headers: dict[str, str]):
    try:
        response = fetch(
            target_url(DK_MARKETS_URL, _market_params(league_id)),
            BOOK_KEY,
            options=options,
            headers=headers,
        )
    except ScraperApiError as exc:
        print(f"DraftKings market fetch failed for league {league_id}: {exc}")
        return None

    try:
        payload = response.json()
    except ValueError:
        payload = _decode_possible_json(response.text)
    if not _looks_like_direct_dk_payload(payload):
        print(f"DraftKings league {league_id} returned no offer payload")
        return None

    by_market = _parse_market_lines(payload)
    print(
        f"DraftKings league {league_id}: "
        + ", ".join(f"{key} {len(lines)}" for key, lines in sorted(by_market.items()))
        + f" line(s) across {len(payload.get('events') or [])} event(s)"
    )
    return payload if by_market else None


def _fetch_dk_direct_payload():
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT, "Referer": DK_PAGE_URL}
    # The offer service is JSON behind Akamai: premium residential IPs get it,
    # ScraperAPI-side rendering would only add 15 credits per call.
    options = replace(options_for(BOOK_KEY), keep_headers=True)
    tried: list[str] = []
    for league_id in DK_LEAGUE_IDS:
        tried.append(league_id)
        payload = _fetch_league_payload(league_id, options, headers)
        if payload:
            return payload
    for league_id in _discover_league_ids():
        if league_id in tried:
            continue
        payload = _fetch_league_payload(league_id, options, headers)
        if payload:
            return payload
    return None


def scrape_dk():
    api_data = _fetch_dk_direct_payload()

    if not api_data:
        print("Could not capture DraftKings API data.")
        return {"detail": "draftkings scrape no data", "count": 0, "label": "alerts"}

    try:
        by_market = _parse_market_lines(api_data)
        current_lines = by_market.get("spreads", {})
        if not by_market:
            return {"detail": "draftkings scrape parsed no lines", "count": 0, "label": "alerts"}

        previous_lines = load_previous_lines()
        alerts: List[str] = []

        for unique_key, current in current_lines.items():
            previous = (previous_lines or {}).get(unique_key)
            if not previous:
                continue

            try:
                old_line = float(previous["line"])
                new_line = float(current["line"])
            except (TypeError, ValueError, KeyError):
                continue

            if abs(new_line - old_line) >= 1.5:
                alerts.append(
                    f"**DK STEAM ALERT:** {current['matchup']}\n"
                    f"**{current['team']} Spread Moved!**\n"
                    f"**Pinnacle:** {_pinnacle_reference(current)}\n"
                    f"Old Line: {old_line} -> **New Line: {new_line}**"
                )

        save_current_lines(current_lines)
        events = _to_ingest_events(by_market)
        if events:
            ingest_events(DK_SPORT_KEY, events)
        for message in alerts:
            post_discord({"embeds": [{"description": message, "color": 15844367}]}, webhook_url=DISCORD_WEBHOOK_URL)

        tracked = sum(len(lines) for lines in by_market.values())
        return {
            "detail": (
                f"draftkings scrape complete ({tracked} lines across "
                f"{len(by_market)} market(s), {len(events)} events ingested "
                f"on {DK_SPORT_KEY})"
            ),
            "count": len(alerts),
            "label": "alerts",
        }
    except Exception as exc:
        print(f"DraftKings Scrape Error: {exc}")
        return {"detail": f"draftkings scrape error: {exc}", "count": 0, "label": "alerts"}


def scrape_draftkings():
    return scrape_dk()


if __name__ == "__main__":
    scrape_dk()
