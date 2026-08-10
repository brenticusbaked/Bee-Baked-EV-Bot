"""ParlayAPI player prop +EV scanner.

Fetches player props from ParlayAPI's /v1/sports/{sport}/props endpoint,
builds a sharp (Pinnacle-first) fair probability for each prop, compares soft
book prices, and sends the top +EV alerts to Discord.
"""

import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from db_manager import is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from utils.model_pricing import (
    fair_american_from_probability,
    model_edge_from_probability,
    model_units_from_probability,
)
from utils.odds import american_to_decimal, decimal_to_american
from utils.prop_pricing import consensus_probabilities
from utils.scratch_guard import safe_parse_commence_time
from utils.thresholds import env_float, env_int

load_dotenv()

PARLAY_BASE_URL = "https://parlay-api.com/v1"
PARLAY_KEYS = [
    key.strip().replace("\r", "").replace("\n", "")
    for key in (
        os.getenv("PARLAYAPI_KEY"),
        os.getenv("PARLAYAPI_KEY_2"),
        os.getenv("PARLAYAPI_KEY_3"),
    )
    if key and key.strip()
]
PARLAY_SPORTS = [
    s.strip()
    for s in os.getenv(
        "PARLAYAPI_SPORTS",
        "baseball_mlb,basketball_nba,americanfootball_nfl,icehockey_nhl,basketball_wnba",
    ).split(",")
    if s.strip()
]
PARLAY_MARKETS = os.getenv("PARLAYAPI_MARKETS", "").strip() or None
PARLAY_SHARP_BOOKS = [
    book.strip().lower()
    for book in os.getenv(
        "PARLAY_SHARP_BOOKS",
        "pinnacle,circa,cris,bookmaker,draftkings,fanduel,betmgm,caesars,pointsbet,bet365,williamhill_us",
    ).split(",")
    if book.strip()
]
PARLAY_EV_THRESHOLD = env_float("PARLAY_EV_THRESHOLD", 0.0)
PARLAY_MAX_ALERTS = env_int("PARLAY_MAX_ALERTS", 5)
PARLAY_MAX_UNITS = env_float("PARLAY_MAX_UNITS", 5.0)
PARLAY_LIMIT = env_int("PARLAY_LIMIT", 100)
PARLAY_MAX_AGE_SECONDS = env_int("PARLAY_MAX_AGE_SECONDS", 300)
PARLAY_REQUEST_DELAY = env_float("PARLAY_REQUEST_DELAY", 1.0)


def _american_to_decimal_safe(american_odds: Any) -> Optional[float]:
    try:
        return float(american_to_decimal(american_odds))
    except Exception:
        return None


def _not_yet_started(commence_time: Any) -> bool:
    commence = safe_parse_commence_time(str(commence_time or ""))
    if not commence:
        return True
    return datetime.now(timezone.utc) < commence


def _fetch_props(sport: str, api_key: str) -> Any:
    url = f"{PARLAY_BASE_URL}/sports/{sport}/props"
    params: Dict[str, Any] = {"limit": PARLAY_LIMIT}
    if PARLAY_MARKETS:
        params["markets"] = PARLAY_MARKETS
    if PARLAY_MAX_AGE_SECONDS > 0:
        params["maxAgeSec"] = PARLAY_MAX_AGE_SECONDS
    headers = {"X-API-Key": api_key}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
        if response.status_code == 429:
            print(f"ParlayAPI rate limit (429) for {sport}.")
            return {"rate_limited": True}
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            return []
        return data
    except Exception as exc:
        print(f"ParlayAPI props fetch failed for {sport}: {exc}")
        return []


def _group_props(
    rows: List[Dict[str, Any]],
) -> Dict[Tuple[str, str, str, Any], List[Dict[str, Any]]]:
    groups: Dict[Tuple[str, str, str, Any], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        event_id = str(row.get("canonical_event_id") or row.get("event_id") or "unknown")
        player = str(row.get("player") or "").strip()
        market = str(row.get("market_key") or "").strip()
        line = row.get("line")
        if not player or not market or line is None:
            continue
        key = (event_id, player, market, line)
        groups[key].append(row)
    return groups


def _sharp_fair_probability(
    group_rows: List[Dict[str, Any]],
) -> Optional[Dict[str, float]]:
    prices_by_book: Dict[str, Dict[str, Any]] = {}
    for row in group_rows:
        book = str(row.get("bookmaker") or "").strip().lower()
        if not book:
            continue
        over = _american_to_decimal_safe(row.get("over_price"))
        under = _american_to_decimal_safe(row.get("under_price"))
        if over is None or under is None:
            continue
        if book not in prices_by_book:
            prices_by_book[book] = {}
        prices_by_book[book]["over"] = over
        prices_by_book[book]["under"] = under

    book_pairs = []
    source_books = []
    for book in PARLAY_SHARP_BOOKS:
        sides = prices_by_book.get(book)
        if not sides or "over" not in sides or "under" not in sides:
            continue
        if book == "pinnacle":
            book_pairs = [
                {
                    "over": {"price": sides["over"]},
                    "under": {"price": sides["under"]},
                }
            ]
            source_books = ["pinnacle"]
            break
        book_pairs.append(
            {
                "over": {"price": sides["over"]},
                "under": {"price": sides["under"]},
            }
        )
        source_books.append(book)

    if not book_pairs:
        return None

    probabilities = consensus_probabilities(book_pairs, method="power")
    if not probabilities:
        return None
    return probabilities


def _process_group(
    sport: str,
    event_id: str,
    away_team: str,
    home_team: str,
    player: str,
    market_key: str,
    market_display: str,
    line: Any,
    group_rows: List[Dict[str, Any]],
    fair: Dict[str, float],
) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []
    matchup = f"{away_team} @ {home_team}"
    market = str(market_key).replace("_", " ").upper()
    sharp_books = set(PARLAY_SHARP_BOOKS)
    sharp_source = ",".join(
        sorted(
            {
                str(row.get("bookmaker_title") or row.get("bookmaker") or "").strip()
                for row in group_rows
                if str(row.get("bookmaker") or "").strip().lower() in sharp_books
            }
        )
    ) or "consensus"

    for row in group_rows:
        book = str(row.get("bookmaker") or "").strip().lower()
        book_title = str(row.get("bookmaker_title") or book).strip()
        if not book or book in sharp_books:
            continue
        for side in ("over", "under"):
            price = row.get(f"{side}_price")
            if price in (None, ""):
                continue
            true_prob = fair.get(side)
            if not true_prob:
                continue
            try:
                ev = model_edge_from_probability(true_prob, price)
            except Exception:
                continue
            if ev < PARLAY_EV_THRESHOLD:
                continue
            try:
                units = model_units_from_probability(
                    true_prob, price, cap=PARLAY_MAX_UNITS
                )
            except Exception:
                continue
            if units <= 0.0:
                continue

            selection = f"{player} {side.upper()} {line}"
            try:
                already = is_already_logged(sport, event_id, market, selection)
            except Exception:
                already = is_already_logged(matchup, market, selection)
            if already:
                continue

            decimal_price = _american_to_decimal_safe(price)
            if not decimal_price:
                continue

            fair_american = fair_american_from_probability(true_prob)
            logged = log_bet_to_db(
                matchup,
                market,
                selection,
                price,
                ev,
                f"{units:.2f}",
                fair_american,
                sport,
                event_id,
                bookmaker=book_title,
                odds_decimal=decimal_price,
                fair_prob=true_prob,
                edge_pct=ev * 100.0,
            )
            if not logged:
                continue

            picks.append(
                {
                    "sport": sport,
                    "matchup": matchup,
                    "market": market,
                    "market_display": market_display,
                    "selection": selection,
                    "player": player,
                    "side": side,
                    "line": line,
                    "book": book_title,
                    "book_key": book,
                    "price": price,
                    "decimal_odds": decimal_price,
                    "true_prob_pct": true_prob * 100.0,
                    "ev_pct": ev,
                    "units": units,
                    "sharp_book": sharp_source,
                    "event_id": event_id,
                }
            )
    return picks


def _format_discord_embeds(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    embeds: List[Dict[str, Any]] = []
    for pick in picks[:PARLAY_MAX_ALERTS]:
        fair_american = "N/A"
        try:
            fair_decimal = 1.0 / (pick["true_prob_pct"] / 100.0)
            fair_american = decimal_to_american(fair_decimal)
        except Exception:
            pass

        ev_percent = pick["ev_pct"] * 100.0
        embed = {
            "title": "🐝 +EV Prop Alert from The Hive",
            "color": 0x2ECC71,
            "fields": [
                {
                    "name": "Sport and Matchup",
                    "value": f"{pick['sport']} | {pick['matchup']}",
                    "inline": False,
                },
                {
                    "name": "The +EV Play",
                    "value": (
                        f"**{pick['selection']} ({pick['market_display']})**\n"
                        f"Book: {pick['book']}\n"
                        f"Odds: {pick['price']}"
                    ),
                    "inline": False,
                },
                {
                    "name": "Sharp Baseline",
                    "value": (
                        f"Sharp: {pick['sharp_book']}\n"
                        f"True Probability: {pick['true_prob_pct']:.2f}%\n"
                        f"Fair Value: {fair_american}"
                    ),
                    "inline": False,
                },
                {
                    "name": "The Math",
                    "value": (
                        f"EV%: {ev_percent:.2f}%\n"
                        f"Recommend: {pick['units']:.2f}% of bankroll"
                    ),
                    "inline": False,
                },
            ],
            "footer": {
                "text": "BEE BAKED BETS | The Hive +EV Scanner | ParlayAPI Props"
            },
            "timestamp": now,
        }
        embeds.append(embed)
    return embeds


def run_parlay_scan() -> None:
    if not PARLAY_KEYS:
        print("No PARLAYAPI_KEY configured. Skipping Parlay prop scan.")
        return
    if not BET_ALERTS_WEBHOOK_URL:
        print("No bet-alerts webhook configured. Skipping Parlay prop scan.")
        return

    all_picks: List[Dict[str, Any]] = []
    for sport in PARLAY_SPORTS:
        data = None
        for api_key in PARLAY_KEYS:
            data = _fetch_props(sport, api_key)
            if not data:
                continue
            if isinstance(data, dict) and data.get("rate_limited"):
                time.sleep(1.0)
                continue
            break

        if not data or not isinstance(data, list):
            continue

        rows = [r for r in data if _not_yet_started(r.get("commence_time"))]
        groups = _group_props(rows)
        sport_picks = 0
        for (event_id, player, market_key, line), group_rows in groups.items():
            fair = _sharp_fair_probability(group_rows)
            if not fair:
                continue
            away_team = group_rows[0].get("away_team", "?")
            home_team = group_rows[0].get("home_team", "?")
            market_display = str(group_rows[0].get("market") or market_key)
            picks = _process_group(
                sport,
                str(event_id),
                str(away_team),
                str(home_team),
                player,
                market_key,
                market_display,
                line,
                group_rows,
                fair,
            )
            all_picks.extend(picks)
            sport_picks += len(picks)

        print(
            f"[parlay] {sport}: {len(groups)} prop markets scanned, "
            f"{sport_picks} +EV picks"
        )

        if PARLAY_REQUEST_DELAY > 0.0:
            time.sleep(PARLAY_REQUEST_DELAY)

    if not all_picks:
        print("No +EV ParlayAPI prop opportunities met the threshold.")
        return

    all_picks.sort(key=lambda item: item.get("ev_pct", 0.0), reverse=True)
    embeds = _format_discord_embeds(all_picks)
    if not embeds:
        return

    send_discord_alert(
        {"embeds": embeds},
        source="bot_parlay_props",
        alert_type="bet_alert",
        webhook_url=BET_ALERTS_WEBHOOK_URL,
    )
    print(f"Sent {len(embeds)} ParlayAPI +EV prop alert(s) to Discord.")


if __name__ == "__main__":
    run_parlay_scan()
