import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from db_manager import is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from utils.odds import american_to_decimal, decimal_to_american, quarter_kelly_units
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
    for s in os.getenv("PARLAYAPI_SPORTS", "baseball_mlb").split(",")
    if s.strip()
]
PARLAY_MARKETS = os.getenv("PARLAYAPI_MARKETS", "").strip() or None
PARLAY_EV_THRESHOLD = env_float("PARLAY_EV_THRESHOLD", 0.0)
PARLAY_EDGE_THRESHOLD = env_float("PARLAY_EDGE_THRESHOLD", 0.0)
PARLAY_MAX_ALERTS = env_int("PARLAY_MAX_ALERTS", 5)
PARLAY_MAX_UNITS = env_float("PARLAY_MAX_UNITS", 5.0)
PARLAY_LIMIT = env_int("PARLAY_LIMIT", 50)
PARLAY_REQUEST_DELAY = env_float("PARLAY_REQUEST_DELAY", 1.0)


def _american_to_decimal_safe(american_odds: Any) -> float:
    try:
        return float(american_to_decimal(american_odds))
    except Exception:
        return 0.0


def _format_selection(opp: dict) -> str:
    side = str(opp.get("side") or "Unknown").strip()
    player = str(opp.get("player") or "").strip()
    if player and player != side:
        return f"{player} {side}"
    return side


def _format_market(opp: dict) -> str:
    market_key = str(opp.get("market_key") or "h2h").strip()
    return market_key.replace("_", " ").upper()


def _not_yet_started(opp: dict) -> bool:
    commence = safe_parse_commence_time(str(opp.get("commence_time") or ""))
    if not commence:
        return True
    return datetime.now(timezone.utc) < commence


def _fetch_ev_for_sport(sport: str, api_key: str) -> Optional[Dict[str, Any]]:
    url = f"{PARLAY_BASE_URL}/sports/{sport}/ev"
    params: Dict[str, Any] = {"limit": PARLAY_LIMIT}
    if PARLAY_MARKETS:
        params["markets"] = PARLAY_MARKETS
    headers = {"X-API-Key": api_key}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
        if response.status_code == 429:
            print(f"ParlayAPI rate limit (429) for {sport}.")
            return {"rate_limited": True}
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"ParlayAPI fetch failed for {sport}: {exc}")
        return {}


def _opportunity_ev(opp: dict) -> Optional[Dict[str, Any]]:
    price = opp.get("price")
    fair_prob_pct = opp.get("fair_prob_pct")
    book_implied_pct = opp.get("book_implied_pct")
    if price is None or fair_prob_pct is None:
        return None

    decimal_odds = _american_to_decimal_safe(price)
    if decimal_odds <= 1.0:
        return None

    true_prob = float(fair_prob_pct) / 100.0
    ev_pct = (true_prob * decimal_odds) - 1.0
    if ev_pct < PARLAY_EV_THRESHOLD:
        return None

    if book_implied_pct is not None:
        edge_pp = float(fair_prob_pct) - float(book_implied_pct)
        if edge_pp < PARLAY_EDGE_THRESHOLD:
            return None

    units = quarter_kelly_units(ev_pct, decimal_odds, cap=PARLAY_MAX_UNITS)
    if units <= 0.0:
        return None

    return {
        "decimal_odds": decimal_odds,
        "true_prob": true_prob,
        "ev_pct": ev_pct,
        "units": units,
    }


def _log_and_collect(sport: str, opp: dict, math: dict) -> Optional[Dict[str, Any]]:
    selection = _format_selection(opp)
    market = _format_market(opp)
    matchup = f"{opp.get('away_team', '?')} @ {opp.get('home_team', '?')}"
    book = str(opp.get("book") or "Unknown").strip()
    event_id = str(opp.get("canonical_event_id") or opp.get("event_id") or "").strip()
    price = opp.get("price")

    if event_id:
        already = is_already_logged(sport, event_id, market, selection)
    else:
        already = is_already_logged(matchup, market, selection)
    if already:
        return None

    ev_percent = math["ev_pct"] * 100.0
    logged = log_bet_to_db(
        matchup,
        market,
        selection,
        sport=sport,
        event_id=event_id or None,
        bookmaker=book,
        odds=price,
        odds_decimal=math["decimal_odds"],
        fair_prob=math["true_prob"],
        edge=ev_percent,
        edge_pct=ev_percent,
        units=math["units"],
    )
    if not logged:
        return None

    return {
        "sport": sport,
        "matchup": matchup,
        "market": market,
        "selection": selection,
        "book": book,
        "price": price,
        "decimal_odds": math["decimal_odds"],
        "true_prob_pct": float(opp.get("fair_prob_pct") or 0.0),
        "ev_pct": math["ev_pct"],
        "units": math["units"],
        "sharp_anchor": str(opp.get("sharp_anchor") or "pinnacle"),
    }


def _process_opportunities(opportunities: List[dict], sport: str) -> List[Dict[str, Any]]:
    picks: List[Dict[str, Any]] = []
    for opp in opportunities:
        if not _not_yet_started(opp):
            continue
        math = _opportunity_ev(opp)
        if not math:
            continue
        pick = _log_and_collect(sport, opp, math)
        if pick:
            picks.append(pick)
    return picks


def _format_discord_embeds(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    embeds: List[Dict[str, Any]] = []
    for pick in picks[:PARLAY_MAX_ALERTS]:
        true_prob = pick["true_prob_pct"]
        fair_american = "N/A"
        if true_prob and true_prob > 0.0:
            fair_decimal = 1.0 / (true_prob / 100.0)
            try:
                fair_american = decimal_to_american(fair_decimal)
            except Exception:
                fair_american = "N/A"

        ev_percent = pick["ev_pct"] * 100.0
        embed = {
            "title": "🐝 +EV Alert from The Hive",
            "color": 0x2ECC71,
            "fields": [
                {
                    "name": "Sport and Matchup",
                    "value": f"{pick['sport']} | {pick['matchup']}",
                    "inline": False,
                },
                {
                    "name": "The +EV Play",
                    "value": f"**{pick['selection']}**\nBook: {pick['book']}\nOdds: {pick['price']}",
                    "inline": False,
                },
                {
                    "name": "Sharp Baseline",
                    "value": (
                        f"Sharp: {pick['sharp_anchor']}\n"
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
                "text": "BEE BAKED BETS | The Hive +EV Scanner | ParlayAPI"
            },
            "timestamp": now,
        }
        embeds.append(embed)
    return embeds


def run_parlay_scan() -> None:
    if not PARLAY_KEYS:
        print("No PARLAYAPI_KEY configured. Skipping Parlay scan.")
        return
    if not BET_ALERTS_WEBHOOK_URL:
        print("No bet-alerts webhook configured. Skipping Parlay scan.")
        return

    all_picks: List[Dict[str, Any]] = []
    for sport in PARLAY_SPORTS:
        data: Optional[Dict[str, Any]] = None
        for api_key in PARLAY_KEYS:
            data = _fetch_ev_for_sport(sport, api_key)
            if not data:
                continue
            if data.get("rate_limited"):
                time.sleep(1.0)
                continue
            break

        if not data or not isinstance(data, dict):
            continue

        opportunities = data.get("opportunities", [])
        if not isinstance(opportunities, list):
            continue

        picks = _process_opportunities(opportunities, sport)
        all_picks.extend(picks)
        print(f"[parlay] {sport}: {len(picks)} alerts")

        if PARLAY_REQUEST_DELAY > 0.0:
            time.sleep(PARLAY_REQUEST_DELAY)

    if not all_picks:
        print("No +EV ParlayAPI opportunities met the threshold.")
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
    print(f"Sent {len(embeds)} ParlayAPI +EV alert(s) to Discord.")


if __name__ == "__main__":
    run_parlay_scan()
