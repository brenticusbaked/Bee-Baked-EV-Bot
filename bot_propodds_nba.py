import os
import re
import time
from typing import Dict, List, Optional, Tuple
import requests
from dotenv import load_dotenv

from db_manager import is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.book_weights import book_weight_for, get_book_weights
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import request
from services.last_ten import build_last_ten_context_line
from utils.links import sportsbook_search_link
from utils.odds import decimal_to_american
from utils.prop_pricing import (
    consensus_probabilities,
    infer_mean_from_over_probability,
    infer_negative_binomial_mean_from_over_probability,
    negative_binomial_prop_probabilities,
    poisson_prop_probabilities,
    uncertainty_adjusted_prop_kelly_units,
)
from utils.thresholds import env_float

# Load local environment variables for manual runs
load_dotenv()

DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL


SGO_API_KEY = os.getenv("SGO_API_KEY")
SGO_API_KEY_2 = os.getenv("SGO_API_KEY_2")
SGO_API_KEY_3 = os.getenv("SGO_API_KEY_3")
SGO_MAX_EVENTS_PER_LEAGUE = max(0, int(os.getenv("SGO_MAX_EVENTS_PER_LEAGUE", "0")))
SGO_LEAGUE_STAGGER_SECONDS = max(0.0, float(os.getenv("SGO_LEAGUE_STAGGER_SECONDS", "1.5")))


def _sgo_keys():
    return [key for key in (SGO_API_KEY, SGO_API_KEY_2, SGO_API_KEY_3) if key]


def _sgo_fetch(url: str, league: str, keys: list) -> dict | None:
    """Fetch a league from SGO, rotating through available keys on 429."""
    saw_retry = False
    for key_index, api_key in enumerate(keys, start=1):
        params = {"apiKey": api_key, "leagueID": league, "oddsAvailable": "true"}
        try:
            resp = request("GET", url, params=params, timeout=15, retry_on_429=False)
            remaining = resp.headers.get("x-requests-remaining")
            if remaining is not None:
                print(f"[prop_bot] SGO key #{key_index} x-requests-remaining: {remaining}")
            if resp.status_code == 429:
                print(f"[prop_bot] {league}: rate-limited on SGO key #{key_index}; trying next key.")
                continue
            data = resp.json()
            if isinstance(data, dict) and data.get("success") is False:
                error = (data.get("error") or "").lower()
                if "rate limit" in error or "quota" in error or "too many" in error:
                    print(f"[prop_bot] {league}: rate-limited on SGO key #{key_index}; trying next key.")
                    continue
                if "unsupported" in error:
                    print(f"[prop_bot] {league}: skipped (unsupported on current SGO plan).")
                    raise _SgoUnsupportedLeague(league)
                print(f"[prop_bot] {league}: SGO key #{key_index} API error: {data.get('error')}; trying next key.")
                continue
            return data
        except requests.exceptions.RetryError:
            saw_retry = True
            print(f"[prop_bot] {league}: transient retry exhaustion on SGO key #{key_index}; trying next key.")
            continue
        except requests.HTTPError as exc:
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None) if resp else None
            body = ""
            try:
                body = (resp.text or "")[:300] if resp is not None else ""
            except Exception:
                body = ""
            if status == 400 or "unsupported" in body.lower():
                print(f"[prop_bot] {league}: skipped (unsupported on current SGO plan).")
                raise _SgoUnsupportedLeague(league)
            if status == 429:
                print(f"[prop_bot] {league}: rate-limited on SGO key #{key_index}; trying next key.")
                continue
            print(f"[prop_bot] {league}: SGO key #{key_index} HTTP error {status}; trying next key. | {body[:120]}")
            continue
        except Exception as exc:
            print(f"[prop_bot] {league}: SGO key #{key_index} fetch failed ({type(exc).__name__}); trying next key.")
            continue
    if saw_retry:
        raise _SgoRetryExhausted(league)
    return None


class _SgoUnsupportedLeague(Exception):
    pass


class _SgoRetryExhausted(Exception):
    pass

DEFAULT_TARGET_STATS = [
    "points",
    "assists",
    "rebounds",
    "three_pointers",
    "steals",
    "blocks",
    "turnovers",
    "points_rebounds_assists",
    "points_rebounds",
    "points_assists",
    "rebounds_assists",
    "strikeouts",
    "hits",
    "runs",
    "rbis",
    "total_bases",
    "home_runs",
    "passing_yards",
    "rushing_yards",
    "receiving_yards",
    "receptions",
    "passing_touchdowns",
    "rushing_attempts",
    "pass_attempts",
    "pass_completions",
    "interceptions",
]

STAT_ALIASES = {
    "pts": "points",
    "point": "points",
    "points": "points",
    "ast": "assists",
    "assist": "assists",
    "assists": "assists",
    "reb": "rebounds",
    "rebound": "rebounds",
    "rebounds": "rebounds",
    "3pm": "three_pointers",
    "3pt": "three_pointers",
    "3ptm": "three_pointers",
    "threes": "three_pointers",
    "three_pointers": "three_pointers",
    "three_points_made": "three_pointers",
    "three_pointers_made": "three_pointers",
    "made_threes": "three_pointers",
    "stl": "steals",
    "steal": "steals",
    "steals": "steals",
    "blk": "blocks",
    "block": "blocks",
    "blocks": "blocks",
    "to": "turnovers",
    "turnover": "turnovers",
    "turnovers": "turnovers",
    "pra": "points_rebounds_assists",
    "points_rebounds_assists": "points_rebounds_assists",
    "par": "points_rebounds",
    "points_rebounds": "points_rebounds",
    "pa": "points_assists",
    "points_assists": "points_assists",
    "ra": "rebounds_assists",
    "rebounds_assists": "rebounds_assists",
    "k": "strikeouts",
    "ks": "strikeouts",
    "so": "strikeouts",
    "strikeout": "strikeouts",
    "strikeouts": "strikeouts",
    "pitcher_ks": "strikeouts",
    "pitcher_strikeout": "strikeouts",
    "pitcher_strikeouts": "strikeouts",
    "hit": "hits",
    "hits": "hits",
    "batter_hits": "hits",
    "player_hits": "hits",
    "run": "runs",
    "runs": "runs",
    "batter_runs": "runs",
    "player_runs": "runs",
    "rbi": "rbis",
    "rbis": "rbis",
    "runs_batted_in": "rbis",
    "batter_rbis": "rbis",
    "player_rbis": "rbis",
    "tb": "total_bases",
    "base": "total_bases",
    "bases": "total_bases",
    "total_bases": "total_bases",
    "batter_total_bases": "total_bases",
    "hr": "home_runs",
    "hrs": "home_runs",
    "home_run": "home_runs",
    "home_runs": "home_runs",
    "batter_home_runs": "home_runs",
    "passing_yards": "passing_yards",
    "pass_yards": "passing_yards",
    "pass_yds": "passing_yards",
    "passing_yds": "passing_yards",
    "player_pass_yds": "passing_yards",
    "rushing_yards": "rushing_yards",
    "rush_yards": "rushing_yards",
    "rush_yds": "rushing_yards",
    "rushing_yds": "rushing_yards",
    "player_rush_yds": "rushing_yards",
    "receiving_yards": "receiving_yards",
    "reception_yards": "receiving_yards",
    "rec_yards": "receiving_yards",
    "rec_yds": "receiving_yards",
    "receiving_yds": "receiving_yards",
    "player_reception_yds": "receiving_yards",
    "receptions": "receptions",
    "reception": "receptions",
    "recs": "receptions",
    "player_receptions": "receptions",
    "passing_touchdowns": "passing_touchdowns",
    "passing_tds": "passing_touchdowns",
    "pass_tds": "passing_touchdowns",
    "passing_td": "passing_touchdowns",
    "player_pass_tds": "passing_touchdowns",
    "rushing_attempts": "rushing_attempts",
    "rush_attempts": "rushing_attempts",
    "carries": "rushing_attempts",
    "pass_attempts": "pass_attempts",
    "passing_attempts": "pass_attempts",
    "pass_completions": "pass_completions",
    "passing_completions": "pass_completions",
    "completions": "pass_completions",
    "interceptions": "interceptions",
    "interception": "interceptions",
    "ints": "interceptions",
    "passing_interceptions": "interceptions",
}

STAT_LABELS = {
    "points": "POINTS",
    "assists": "ASSISTS",
    "rebounds": "REBOUNDS",
    "three_pointers": "3PM",
    "steals": "STEALS",
    "blocks": "BLOCKS",
    "turnovers": "TURNOVERS",
    "points_rebounds_assists": "PRA",
    "points_rebounds": "PTS+REB",
    "points_assists": "PTS+AST",
    "rebounds_assists": "REB+AST",
    "strikeouts": "STRIKEOUTS",
    "hits": "HITS",
    "runs": "RUNS",
    "rbis": "RBIS",
    "total_bases": "TOTAL BASES",
    "home_runs": "HOME RUNS",
    "passing_yards": "PASS YARDS",
    "rushing_yards": "RUSH YARDS",
    "receiving_yards": "REC YARDS",
    "receptions": "RECEPTIONS",
    "passing_touchdowns": "PASS TDS",
    "rushing_attempts": "RUSH ATT",
    "pass_attempts": "PASS ATT",
    "pass_completions": "COMPLETIONS",
    "interceptions": "INTERCEPTIONS",
}

LEAGUE_SPORT_KEYS = {
    "NBA": "basketball_nba",
    "WNBA": "basketball_wnba",
    "MLB": "baseball_mlb",
    "NFL": "americanfootball_nfl",
    "NHL": "icehockey_nhl",
}

PROP_EV_THRESHOLD = env_float("PROP_EV_THRESHOLD", 0.01)
PROP_NEAR_MISS_THRESHOLD = env_float("PROP_NEAR_MISS_THRESHOLD", 0.005)
PROP_CONSENSUS_MIN_BOOKS = max(1, int(os.getenv("PROP_CONSENSUS_MIN_BOOKS", "1")))
PROP_DEVIG_METHOD = os.getenv("PROP_DEVIG_METHOD", "power")
PROP_KELLY_FRACTION = env_float("PROP_KELLY_FRACTION", 0.25)
PROP_MAX_UNITS = env_float("PROP_MAX_UNITS", 2.0)
PROP_CONFIDENCE_FULL_BOOKS = max(1.0, env_float("PROP_CONFIDENCE_FULL_BOOKS", 3.0))
PROP_UNCERTAINTY_Z = env_float("PROP_UNCERTAINTY_Z", 0.35)
PROP_UNCERTAINTY_EFFECTIVE_SAMPLES = env_float("PROP_UNCERTAINTY_EFFECTIVE_SAMPLES", 24.0)
PROP_NEG_BINOMIAL_VARIANCE_MULTIPLIER = max(1.0, env_float("PROP_NEG_BINOMIAL_VARIANCE_MULTIPLIER", 1.35))
ENABLE_PROP_NEGATIVE_BINOMIAL = os.getenv("ENABLE_PROP_NEGATIVE_BINOMIAL", "true").strip().lower() in {"1", "true", "yes", "on"}

LOW_COUNT_POISSON_STATS = {
    "assists",
    "rebounds",
    "three_pointers",
    "steals",
    "blocks",
    "turnovers",
    "strikeouts",
    "hits",
    "runs",
    "rbis",
    "total_bases",
    "home_runs",
    "receptions",
    "passing_touchdowns",
    "interceptions",
}
NEGATIVE_BINOMIAL_STATS = {
    stat.strip().lower()
    for stat in os.getenv(
        "PROP_NEG_BINOMIAL_STATS",
        "points,assists,rebounds,three_pointers,steals,blocks,turnovers,strikeouts,hits,runs,rbis,total_bases,home_runs",
    ).split(",")
    if stat.strip()
}

SHARP_PROP_BOOK_ORDER = [
    book.strip().lower()
    for book in os.getenv("PROP_SHARP_BOOKS", "pinnacle,bookmaker,circa,cris,draftkings").split(",")
    if book.strip()
]
SHARP_PROP_BOOKS = set(SHARP_PROP_BOOK_ORDER)

def _parse_target_stats() -> set:
    raw = os.getenv("PLAYER_PROP_STATS") or os.getenv("NBA_PROP_STATS", "")
    if not raw.strip():
        return set(DEFAULT_TARGET_STATS)
    parsed = {
        _normalize_stat_name(part)
        for part in raw.split(",")
        if _normalize_stat_name(part)
    }
    return parsed or set(DEFAULT_TARGET_STATS)

def to_decimal(price):
    try:
        price = float(price)
        if price >= 100:
            return (price / 100) + 1
        if price <= -100:
            return (100 / abs(price)) + 1
        if price > 1.0:
            return price
        return 1.909
    except Exception:
        return 1.909

def get_dynamic_link(bookmaker, target_string):
    return sportsbook_search_link(bookmaker, target_string)

def _slugify(value: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

def _normalize_stat_name(value: str) -> Optional[str]:
    if not value:
        return None
    key = _slugify(value)
    return STAT_ALIASES.get(key)

TARGET_STATS = _parse_target_stats()

def _parse_player_prop_leagues() -> List[str]:
    raw = os.getenv("PLAYER_PROP_LEAGUES", "NBA,MLB,NFL")
    allow_wnba = os.getenv("ENABLE_WNBA_PROP_BOT", "").strip().lower() in {"1", "true", "yes", "on"}
    leagues = []
    for item in raw.split(","):
        league = item.strip().upper()
        if league == "WNBA" and not allow_wnba:
            continue
        if league in LEAGUE_SPORT_KEYS:
            leagues.append(league)
    if allow_wnba and "WNBA" not in leagues:
        leagues.insert(1 if "NBA" in leagues else 0, "WNBA")
    return leagues or ["NBA"]

PLAYER_PROP_LEAGUES = _parse_player_prop_leagues()


def _extract_sgo_events(payload) -> List[dict]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("events", "data", "results"):
        events = payload.get(key)
        if isinstance(events, list):
            return events

    nested = payload.get("data")
    if isinstance(nested, dict):
        for key in ("events", "data", "results"):
            events = nested.get(key)
            if isinstance(events, list):
                return events
    return []

def _normalize_side(value: str) -> Optional[str]:
    text = str(value or "").lower()
    if "over" in text:
        return "over"
    if "under" in text:
        return "under"
    return None

def _clean_player_name(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"_\d+_[A-Za-z]+$", "", text)
    text = text.replace("_", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text.title()

_NON_PLAYER_ENTITIES = {"home", "away", "all", "home1", "away1", "home2", "away2"}
_OVER_UNDER_BET_TYPES = {"ou", "over_under", "overunder"}


def _resolve_player_name(player_id: str, players_map: dict) -> str:
    info = players_map.get(player_id) if isinstance(players_map, dict) else None
    if isinstance(info, dict):
        for field in ("name", "displayName", "fullName", "shortName"):
            candidate = str(info.get(field, "")).strip()
            if candidate:
                return _clean_player_name(candidate)
        first = str(info.get("firstName", "")).strip()
        last = str(info.get("lastName", "")).strip()
        if first or last:
            return _clean_player_name(f"{first} {last}".strip())
    return _clean_player_name(player_id)


def _team_display_name(team: dict) -> str:
    if not isinstance(team, dict):
        return ""
    names = team.get("names")
    if isinstance(names, dict):
        for field in ("long", "medium", "short"):
            candidate = str(names.get(field, "")).strip()
            if candidate:
                return candidate
    for field in ("name", "teamID"):
        candidate = str(team.get(field, "")).strip()
        if candidate:
            return candidate
    return ""


def _matchup_from_event(event: dict) -> str:
    name = str(event.get("name", "")).strip()
    if name:
        return name
    teams = event.get("teams")
    if isinstance(teams, dict):
        away = _team_display_name(teams.get("away", {}))
        home = _team_display_name(teams.get("home", {}))
        if away and home:
            return f"{away} @ {home}"
        if home or away:
            return home or away
    return "Unknown Matchup"


def _book_line(book_data: dict, odd_obj: dict) -> Optional[str]:
    for source in (book_data, odd_obj):
        for field in ("overUnder", "line", "handicap", "points"):
            value = source.get(field)
            if value not in (None, ""):
                return str(value)
    return None


def _parse_prop_offers(odd_obj: dict, players_map: dict) -> List[dict]:
    if str(odd_obj.get("betTypeID", "")).strip().lower() not in _OVER_UNDER_BET_TYPES:
        return []
    side = _normalize_side(odd_obj.get("sideID"))
    if not side:
        return []
    player_id = str(odd_obj.get("statEntityID", "")).strip()
    if not player_id or player_id.lower() in _NON_PLAYER_ENTITIES:
        return []
    stat = _normalize_stat_name(str(odd_obj.get("statID", "")))
    if stat not in TARGET_STATS:
        return []
    player = _resolve_player_name(player_id, players_map)
    if not player:
        return []

    by_book = odd_obj.get("byBookmaker")
    if not isinstance(by_book, dict):
        return []

    offers: List[dict] = []
    for book_id, book_data in by_book.items():
        if not isinstance(book_data, dict):
            continue
        if book_data.get("available") is False:
            continue
        raw_price = book_data.get("odds")
        if raw_price in (None, ""):
            continue
        line = _book_line(book_data, odd_obj)
        if line is None:
            continue
        offers.append(
            {
                "stat": stat,
                "player": player,
                "side": side,
                "line": line,
                "book": str(book_id).strip().lower(),
                "price": to_decimal(raw_price),
                "prop_link": book_data.get("deepLink") or book_data.get("link"),
            }
        )
    return offers

def _consensus_from_sharp_books(sharp_by_book: Dict[str, Dict[str, dict]], stat_type: str, line_value: str) -> tuple[Dict[str, float], str, int]:
    # Pinnacle is the primary sharp baseline; fall back to other sharp books if Pinnacle is missing/incomplete.
    book_pairs = []
    source_books = []
    for book in SHARP_PROP_BOOK_ORDER:
        sides = sharp_by_book.get(book)
        if isinstance(sides, dict) and "over" in sides and "under" in sides:
            if book == "pinnacle":
                book_pairs = [sides]
                source_books = ["pinnacle"]
                break
            book_pairs.append(sides)
            source_books.append(book)
    if not book_pairs:
        return {}, "none", 0
    probabilities = consensus_probabilities(book_pairs, method=PROP_DEVIG_METHOD)
    if not probabilities:
        return {}, "none", len(book_pairs)
    source = ",".join(source_books)
    if ENABLE_PROP_NEGATIVE_BINOMIAL and stat_type in NEGATIVE_BINOMIAL_STATS:
        try:
            line = float(line_value)
        except (TypeError, ValueError):
            line = None
        if line is not None:
            mean = infer_negative_binomial_mean_from_over_probability(
                line,
                probabilities["over"],
                variance_multiplier=PROP_NEG_BINOMIAL_VARIANCE_MULTIPLIER,
            )
            negative_binomial_probabilities = negative_binomial_prop_probabilities(
                line,
                mean,
                variance_multiplier=PROP_NEG_BINOMIAL_VARIANCE_MULTIPLIER,
            )
            if negative_binomial_probabilities:
                probabilities = negative_binomial_probabilities
                source = f"{source}_negbin"
    elif stat_type in LOW_COUNT_POISSON_STATS:
        try:
            line = float(line_value)
        except (TypeError, ValueError):
            line = None
        if line is not None:
            mean = infer_mean_from_over_probability(line, probabilities["over"])
            poisson_probabilities = poisson_prop_probabilities(line, mean)
            if poisson_probabilities:
                probabilities = poisson_probabilities
                source = f"{source}_poisson"
    return probabilities, source, len(book_pairs)

def _sharp_prop_reference(sharp_by_book: Dict[str, Dict[str, dict]], side: str) -> str:
    candidates = []
    for book, sides in sharp_by_book.items():
        offer = sides.get(side)
        if offer:
            candidates.append((book, offer["price"]))
    if not candidates:
        return "unavailable"
    candidates = sorted(candidates, key=lambda item: (item[0] != "pinnacle", item[0]))
    book, price = candidates[0]
    return f"{book.upper()} {decimal_to_american(price)}"

def _log_unparsed_event_shape(league: str, event: dict) -> None:
    if not isinstance(event, dict):
        print(f"[prop_bot] {league}: event is {type(event).__name__}, not a dict")
        return
    odds = event.get("odds")
    if isinstance(odds, dict):
        sample_key = next(iter(odds), None)
        sample = odds.get(sample_key) if sample_key is not None else None
        odds_kind, odds_count = "dict", len(odds)
    elif isinstance(odds, list):
        sample = odds[0] if odds else None
        sample_key, odds_kind, odds_count = None, "list", len(odds)
    else:
        sample, sample_key, odds_kind, odds_count = None, None, type(odds).__name__, 0
    sample_keys = sorted(sample.keys()) if isinstance(sample, dict) else None
    ou_stats: set = set()
    odd_values = odds.values() if isinstance(odds, dict) else (odds if isinstance(odds, list) else [])
    for odd_obj in odd_values:
        if isinstance(odd_obj, dict) and str(odd_obj.get("betTypeID", "")).strip().lower() in _OVER_UNDER_BET_TYPES:
            stat_id = str(odd_obj.get("statID", "")).strip()
            if stat_id:
                ou_stats.add(stat_id)
    print(
        f"[prop_bot] {league}: {odds_kind} odds x{odds_count} parsed 0 | "
        f"event keys={sorted(event.keys())} | sample oddID={sample_key} | "
        f"sample field keys={sample_keys} | "
        f"over/under statIDs seen={sorted(ou_stats)[:25]}"
    )


def get_sgo_edges():
    sgo_keys = _sgo_keys()
    print(f"[prop_bot] SGO keys loaded: {len(sgo_keys)}")
    if len(sgo_keys) < 2:
        print(f"[prop_bot] WARNING: only {len(sgo_keys)} SGO key(s) loaded. Set SGO_API_KEY_2 and SGO_API_KEY_3 secrets to avoid 429s.")
    if not sgo_keys:
        return [], [], {"reason": "SGO_API_KEY missing"}

    soft_list = {
        "fanduel",
        "draftkings",
        "betmgm",
        "espn",
        "fanatics",
        "bet365",
        "caesars",
        "betrivers",
        "bovada",
        "pick6",
        "novig",
        "dabble",
    }
    
    picks = []
    near_misses = []
    book_weights = get_book_weights()
    url = "https://api.sportsgameodds.com/v2/events"
    
    scan_stats = {
        "leagues": len(PLAYER_PROP_LEAGUES),
        "events": 0,
        "raw_odds": 0,
        "parsed_props": 0,
        "sharp_sides": 0,
        "soft_sides": 0,
        "qualified_groups": 0,
        "soft_skipped_leagues": [],
        "errored_leagues": [],
    }

    for league in PLAYER_PROP_LEAGUES:
        sport_key = LEAGUE_SPORT_KEYS.get(league, league.lower())

        try:
            data = _sgo_fetch(url, league, sgo_keys)
        except _SgoUnsupportedLeague:
            print(f"[prop_bot] {league}: skipped (unsupported on current SGO plan).")
            scan_stats["soft_skipped_leagues"].append(f"{league}:unsupported")
            continue
        except _SgoRetryExhausted:
            print(f"[prop_bot] {league}: transient retry exhaustion; skipping league.")
            scan_stats["soft_skipped_leagues"].append(f"{league}:retry")
            continue

        if data is None:
            print(f"[prop_bot] {league}: SGO keys exhausted; skipping league.")
            scan_stats["errored_leagues"].append(f"{league}:keys_exhausted")
            continue

        try:
            events_list = _extract_sgo_events(data)

            if SGO_MAX_EVENTS_PER_LEAGUE and events_list:
                events_list = events_list[:SGO_MAX_EVENTS_PER_LEAGUE]

            if not events_list:
                if isinstance(data, dict):
                    envelope = {
                        k: data.get(k)
                        for k in ("success", "error", "message")
                        if k in data
                    }
                    print(
                        f"[prop_bot] {league}: 0 events "
                        f"(response keys={sorted(data.keys())}, envelope={envelope})"
                    )
                else:
                    print(f"[prop_bot] {league}: 0 events (non-dict response type={type(data).__name__})")

            scan_stats["events"] += len(events_list)

            parsed_before = scan_stats["parsed_props"]
            for event in events_list:
                _process_sgo_event(
                    event,
                    league,
                    sport_key,
                    soft_list,
                    book_weights,
                    picks,
                    near_misses,
                    scan_stats,
                )

            if events_list and scan_stats["parsed_props"] == parsed_before:
                _log_unparsed_event_shape(league, events_list[0])
        except Exception as exc:
            print(f"[prop_bot] {league}: processing failed ({type(exc).__name__}); skipping league")
            scan_stats["errored_leagues"].append(f"{league}:{type(exc).__name__}")
            continue

        if SGO_LEAGUE_STAGGER_SECONDS > 0:
            time.sleep(SGO_LEAGUE_STAGGER_SECONDS)

    return picks, near_misses, scan_stats

def _process_sgo_event(
    event: dict,
    league: str,
    sport_key: str,
    soft_list: set,
    book_weights: dict,
    picks: List[dict],
    near_misses: List[dict],
    scan_stats: dict,
) -> None:
    matchup = _matchup_from_event(event)
    players_map = event.get("players") if isinstance(event.get("players"), dict) else {}
    market_groups: Dict[Tuple[str, str, str], Dict[str, Dict[str, dict]]] = {}
    odds_map = event.get("odds", {})
    
    if isinstance(odds_map, list):
        odds_iterable = enumerate(odds_map)
    else:
        odds_iterable = odds_map.items()
        
    for odd_key, odd_obj in odds_iterable:
        if not isinstance(odd_obj, dict):
            continue
        scan_stats["raw_odds"] += 1
        offers = _parse_prop_offers(odd_obj, players_map)
        for offer in offers:
            scan_stats["parsed_props"] += 1
            market_key = (offer["player"], offer["stat"], offer["line"])
            market_groups.setdefault(market_key, {"sharp": {}, "soft": {}})

            if offer["book"] in SHARP_PROP_BOOKS:
                market_groups[market_key]["sharp"].setdefault(offer["book"], {})[offer["side"]] = offer
                scan_stats["sharp_sides"] += 1
            elif offer["book"] in soft_list:
                current = market_groups[market_key]["soft"].get(offer["side"])
                if not current or offer["price"] > current["price"]:
                    market_groups[market_key]["soft"][offer["side"]] = offer
                scan_stats["soft_sides"] += 1
            
    for (player_name, stat_type, line_value), value in market_groups.items():
        sharp, soft = value["sharp"], value["soft"]
        if not soft:
            continue
        probabilities, probability_source, consensus_books = _consensus_from_sharp_books(sharp, stat_type, line_value)
        if not probabilities:
            continue
        scan_stats["qualified_groups"] += 1
        
        for side in ("over", "under"):
            if side not in soft:
                continue
            edge = (soft[side]["price"] * probabilities[side]) - 1
            book_weight = book_weight_for(book_weights, soft[side]["book"])
            weighted_score = edge * book_weight
            selection = f"{player_name} {side.upper()} {line_value}"
            
            if PROP_NEAR_MISS_THRESHOLD <= edge < PROP_EV_THRESHOLD:
                near_misses.append(
                    {
                        "matchup": matchup,
                        "selection": selection,
                        "book": soft[side]["book"],
                        "edge": edge,
                        "weight": book_weight,
                        "stat": STAT_LABELS.get(stat_type, stat_type.upper()),
                    }
                )
                
            if edge < PROP_EV_THRESHOLD:
                continue
            
            market = STAT_LABELS.get(stat_type, stat_type.upper())
            if is_already_logged(matchup, market, selection):
                continue
            
            confidence = min(1.0, consensus_books / PROP_CONFIDENCE_FULL_BOOKS)
            units, adjusted_edge, adjusted_probability = uncertainty_adjusted_prop_kelly_units(
                probabilities[side],
                soft[side]["price"],
                confidence=confidence,
                fraction=PROP_KELLY_FRACTION,
                cap=PROP_MAX_UNITS,
                z_score=PROP_UNCERTAINTY_Z,
                effective_samples=PROP_UNCERTAINTY_EFFECTIVE_SAMPLES,
            )
            if units <= 0:
                continue
            was_logged = log_bet_to_db(
                matchup.strip(),
                market,
                selection,
                decimal_to_american(soft[side]["price"]),
                edge,
                f"{units:.2f}",
                decimal_to_american(1 / probabilities[side]),
                sport_key,
                str(event.get("id", "")),
                notes=(
                    f"book={soft[side]['book']};market=prop;stat={stat_type};line={line_value};"
                    f"fair_source={probability_source};consensus_books={consensus_books};"
                    f"confidence={confidence:.4f};adjusted_edge={adjusted_edge:.4f};"
                    f"adjusted_probability={adjusted_probability:.4f};"
                    f"prop_kelly_fraction={PROP_KELLY_FRACTION}"
                ),
            )
            
            if not was_logged:
                continue
            
            link = soft[side].get("prop_link") or get_dynamic_link(soft[side]["book"], player_name)
            sharp_reference = _sharp_prop_reference(sharp, side)
            picks.append(
                {
                    "score": weighted_score,
                    "msg": (
                        f"**{league} PROP ALERT**\n"
                        f"**Match:** {matchup}\n"
                        f"**Prop:** {selection} ({market})\n"
                        f"**Book:** [{soft[side]['book'].upper()}]({link}) @ {decimal_to_american(soft[side]['price'])}\n"
                        f"**Sharp Ref:** {sharp_reference}\n"
                        f"**Edge:** {edge * 100:.2f}%\n"
                        f"**Adj Edge:** {adjusted_edge * 100:.2f}% ({confidence:.0%} confidence)\n"
                        f"**Fair Price:** {decimal_to_american(1 / probabilities[side])}\n"
                        f"**Fair Source:** {probability_source} ({consensus_books} book consensus)\n"
                        f"**Suggested:** {units:.2f} Units\n"
                        f"**Book Weight:** {book_weight:.2f}x"
                        f"{build_last_ten_context_line(player_name, stat_type, line_value, side, sport_key)}"
                    ),
                }
            )

def _near_miss_summary(near_misses: List[dict]) -> str:
    if not near_misses:
        return ""
    top = sorted(near_misses, key=lambda item: item.get("edge", 0.0), reverse=True)[:3]
    preview = "; ".join(
        f"{item['selection']} [{item['stat']}] {item['book']} {item['edge'] * 100:.2f}%"
        for item in top
    )
    return f"{len(near_misses)} near misses found; top {len(top)} -> {preview}"

def main():
    if not SGO_API_KEY:
        print("SGO_API_KEY missing. Skipping player prop bot.")
        return {"detail": "SGO_API_KEY missing", "count": 0, "label": "alerts"}
        
    picks, near_misses, scan_stats = get_sgo_edges()
    picks = sorted(picks, key=lambda item: item.get("score", 0.0), reverse=True)
    
    for index, pick in enumerate(picks):
        send_discord_alert(
            {"embeds": [{"description": pick["msg"], "color": 15158332}]},
            source="bot_propodds_nba",
            alert_type="bet_alert",
            dedupe_key=pick["msg"][:200],
            webhook_url=DISCORD_WEBHOOK_URL,
            add_bee_image=index == len(picks) - 1,
        )
        
    reason = scan_stats.get("reason")
    if reason:
        detail = reason
    else:
        detail = (
            f"prop bot scanned {scan_stats.get('events', 0)} events "
            f"across {scan_stats.get('leagues', 0)} leagues, "
            f"{scan_stats.get('parsed_props', 0)} parsed props, "
            f"{scan_stats.get('qualified_groups', 0)} sharp markets"
        )
        soft_skipped = scan_stats.get("soft_skipped_leagues") or []
        if soft_skipped:
            detail += f"; soft-skipped leagues: {', '.join(soft_skipped)}"
        errored = scan_stats.get("errored_leagues") or []
        if errored:
            detail += f"; skipped leagues: {', '.join(errored)}"
        
    meta = {}
    near_miss_summary = _near_miss_summary(near_misses)
    if near_miss_summary:
        meta["near_miss_summary"] = near_miss_summary
        
    return {
        "detail": detail,
        "count": len(picks),
        "label": "alerts",
        "meta": meta,
    }

if __name__ == "__main__":
    main()
