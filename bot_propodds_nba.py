import os
import re
from typing import Dict, Iterable, List, Optional, Tuple
import requests
from dotenv import load_dotenv

from db_manager import is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.book_weights import get_book_weights
from services.http_client import request
from utils.links import sportsbook_search_link
from utils.odds import decimal_to_american
from utils.prop_pricing import consensus_probabilities, infer_mean_from_over_probability, poisson_prop_probabilities, prop_kelly_units
from utils.thresholds import env_float

# Load local environment variables for manual runs
load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY")

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
}

PROP_EV_THRESHOLD = env_float("PROP_EV_THRESHOLD", 0.01)
PROP_NEAR_MISS_THRESHOLD = env_float("PROP_NEAR_MISS_THRESHOLD", 0.005)
PROP_CONSENSUS_MIN_BOOKS = max(1, int(os.getenv("PROP_CONSENSUS_MIN_BOOKS", "1")))
PROP_DEVIG_METHOD = os.getenv("PROP_DEVIG_METHOD", "multiplicative")
PROP_KELLY_FRACTION = env_float("PROP_KELLY_FRACTION", 0.125)
PROP_MAX_UNITS = env_float("PROP_MAX_UNITS", 2.0)

LOW_COUNT_POISSON_STATS = {"assists", "rebounds", "three_pointers", "steals", "blocks", "turnovers"}

SHARP_PROP_BOOKS = {
    book.strip().lower()
    for book in os.getenv("PROP_SHARP_BOOKS", "pinnacle,bookmaker,cris,betonline").split(",")
    if book.strip()
}

def _parse_target_stats() -> set:
    raw = os.getenv("NBA_PROP_STATS", "")
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
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")

def _normalize_stat_name(value: str) -> Optional[str]:
    if not value:
        return None
    key = _slugify(value)
    return STAT_ALIASES.get(key)

TARGET_STATS = _parse_target_stats()

def _normalize_side(value: str) -> Optional[str]:
    text = str(value or "").lower()
    if "over" in text:
        return "over"
    if "under" in text:
        return "under"
    return None

def _clean_player_name(value: str) -> str:
    text = str(value or "").replace("_1_", " ")
    text = text.replace("_", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text.title()

def _extract_player_name(parts: Iterable[str], odd_obj: dict) -> str:
    for field in ("playerName", "player", "participantName", "name"):
        candidate = str(odd_obj.get(field, "")).strip()
        if candidate and "over" not in candidate.lower() and "under" not in candidate.lower():
            return _clean_player_name(candidate)
    parts_list = list(parts)
    if len(parts_list) > 1:
        return _clean_player_name(parts_list[1])
    return ""

def _extract_line(odd_obj: dict, parts: Iterable[str]) -> Optional[str]:
    for field in ("handicap", "line", "points", "value"):
        value = odd_obj.get(field)
        if value not in (None, ""):
            return str(value)
    for part in reversed(list(parts)):
        part_text = str(part).strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", part_text):
            return part_text
    return None

def _extract_stat(odd_obj: dict, parts: Iterable[str]) -> Optional[str]:
    candidates: List[str] = []
    parts_list = list(parts)
    if parts_list:
        candidates.append(parts_list[0])
    for field in ("marketName", "marketType", "propType", "statType", "stat", "market", "label"):
        value = odd_obj.get(field)
        if value:
            candidates.append(str(value))
    for candidate in candidates:
        normalized = _normalize_stat_name(candidate)
        if normalized:
            return normalized
    return None

def _extract_side(odd_obj: dict, parts: Iterable[str]) -> Optional[str]:
    for field in ("side", "selection", "label", "name", "description"):
        value = odd_obj.get(field)
        side = _normalize_side(value)
        if side:
            return side
    for part in reversed(list(parts)):
        side = _normalize_side(part)
        if side:
            return side
    return None

def _parse_prop_offer(odd_key: str, odd_obj: dict) -> Optional[dict]:
    odd_id = str(odd_obj.get("oddID", odd_key) or odd_key)
    parts = [part for part in odd_id.split("-") if part]
    stat = _extract_stat(odd_obj, parts)
    if stat not in TARGET_STATS:
        return None
    player = _extract_player_name(parts, odd_obj)
    side = _extract_side(odd_obj, parts)
    line = _extract_line(odd_obj, parts)
    book = str(odd_obj.get("bookmakerID", "unknown")).strip().lower()
    if not player or not side or line is None or not book:
        return None
    return {
        "stat": stat,
        "player": player,
        "side": side,
        "line": str(line),
        "book": book,
        "price": to_decimal(odd_obj.get("price")),
        "prop_link": odd_obj.get("deepLink"),
    }

def _consensus_from_sharp_books(sharp_by_book: Dict[str, Dict[str, dict]], stat_type: str, line_value: str) -> tuple[Dict[str, float], str, int]:
    book_pairs = [
        sides for sides in sharp_by_book.values()
        if "over" in sides and "under" in sides
    ]
    if len(book_pairs) < PROP_CONSENSUS_MIN_BOOKS:
        return {}, "none", len(book_pairs)
    probabilities = consensus_probabilities(book_pairs, method=PROP_DEVIG_METHOD)
    source = f"consensus_{PROP_DEVIG_METHOD}"
    if not probabilities:
        return {}, "none", len(book_pairs)
    if stat_type in LOW_COUNT_POISSON_STATS:
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

def get_sgo_edges():
    if not SGO_API_KEY:
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
        "prizepicks",
        "pick6",
        "novig",
        "dabble",
    }
    
    picks = []
    near_misses = []
    book_weights = get_book_weights()
    url = "https://api.sportsgameodds.com/v2/events"
    params = {"apiKey": SGO_API_KEY, "leagueID": "NBA", "oddsAvailable": "true"}
    
    scan_stats = {
        "events": 0,
        "raw_odds": 0,
        "parsed_props": 0,
        "sharp_sides": 0,
        "soft_sides": 0,
        "qualified_groups": 0,
    }
    
    try:
        # UPDATED: retry_on_429 set to True to respect rate limits with exponential backoff
        data = request("GET", url, params=params, timeout=15, retry_on_429=True).json()
        
        if isinstance(data, dict):
            events_list = data.get("events", [])
        else:
            events_list = data if isinstance(data, list) else []
            
        scan_stats["events"] = len(events_list)
        
        for event in events_list:
            matchup = event.get("name", "Unknown Matchup")
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
                offer = _parse_prop_offer(str(odd_key), odd_obj)
                if not offer:
                    continue
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
                    book_weight = book_weights.get(soft[side]["book"], 1.0)
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
                    
                    units = prop_kelly_units(edge, soft[side]["price"], fraction=PROP_KELLY_FRACTION, cap=PROP_MAX_UNITS)
                    was_logged = log_bet_to_db(
                        matchup.strip(),
                        market,
                        selection,
                        decimal_to_american(soft[side]["price"]),
                        edge,
                        f"{units:.2f}",
                        decimal_to_american(1 / probabilities[side]),
                        "basketball_nba",
                        str(event.get("id", "")),
                        notes=(
                            f"book={soft[side]['book']};market=prop;stat={stat_type};line={line_value};"
                            f"fair_source={probability_source};consensus_books={consensus_books};"
                            f"prop_kelly_fraction={PROP_KELLY_FRACTION}"
                        ),
                    )
                    
                    if not was_logged:
                        continue
                    
                    link = soft[side].get("prop_link") or get_dynamic_link(soft[side]["book"], player_name)
                    picks.append(
                        {
                            "score": weighted_score,
                            "msg": (
                                f"**NBA PROP ALERT**\n"
                                f"**Match:** {matchup}\n"
                                f"**Prop:** {selection} ({market})\n"
                                f"**Book:** [{soft[side]['book'].upper()}]({link}) @ {decimal_to_american(soft[side]['price'])}\n"
                                f"**Edge:** {edge * 100:.2f}%\n"
                                f"**Fair Price:** {decimal_to_american(1 / probabilities[side])}\n"
                                f"**Fair Source:** {probability_source} ({consensus_books} book consensus)\n"
                                f"**Suggested:** {units:.2f} Units\n"
                                f"**Book Weight:** {book_weight:.2f}x"
                            ),
                        }
                    )
                    
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        print(f"Prop Bot Error: {exc}")
        if status_code == 429:
            return [], [], {"reason": "SGO rate limited", "status_code": 429}
        return [], [], {"reason": f"error: {exc}", "status_code": status_code}
    except Exception as exc:
        print(f"Prop Bot Error: {exc}")
        return [], [], {"reason": f"error: {exc}"}
        
    return picks, near_misses, scan_stats

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
        print("SGO_API_KEY missing. Skipping NBA prop bot.")
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
            f"prop bot scanned {scan_stats.get('events', 0)} events, "
            f"{scan_stats.get('parsed_props', 0)} parsed props, "
            f"{scan_stats.get('qualified_groups', 0)} sharp markets"
        )
        
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