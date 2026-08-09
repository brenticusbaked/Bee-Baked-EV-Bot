"""WNBA spread model for identifying pregame misprices.

This mirrors the NBA model shape, but targets WNBA games and scans both
today's and tomorrow's slates so late-day runs can still catch the next slate.
"""

from __future__ import annotations

from datetime import timedelta

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import get_json
from services.last_ten import build_last_ten_context_line
from services.odds_reference import format_pinnacle_reference
from utils.links import sportsbook_search_link
from utils.model_pricing import fair_american_from_probability, model_edge_from_probability, model_units_from_probability
from utils.odds import decimal_to_american
from utils.thresholds import env_float
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
WNBA_MODEL_EDGE_THRESHOLD = env_float("WNBA_MODEL_EDGE_THRESHOLD", 0.02)
WNBA_MODEL_BASE_PROB = env_float("WNBA_MODEL_BASE_PROB", 0.53)
WNBA_MODEL_SPREAD_SLOPE = env_float("WNBA_MODEL_SPREAD_SLOPE", 0.003)
WNBA_MODEL_PROB_CAP = env_float("WNBA_MODEL_PROB_CAP", 0.59)
WNBA_MODEL_MAX_UNITS = env_float("WNBA_MODEL_MAX_UNITS", 1.25)


def get_dynamic_link(bookmaker: str, target_string: str) -> str:
    return sportsbook_search_link(bookmaker, target_string)


def _format_point_string(point_val) -> str:
    if point_val is None or point_val == "":
        return ""
    try:
        val = float(point_val)
        return f"+{val:.1f}" if val > 0 else f"{val:.1f}"
    except (ValueError, TypeError):
        return str(point_val)


def get_best_spread(target_team: str):
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty or failed to load.")
        return None, None, None, None, None

    best_price = 0.0
    best_point = ""
    best_book = "Unknown"
    best_title = "Unknown"
    event_id = ""

    for game in cache.get("basketball_wnba", []):
        if target_team not in game.get("home_team", "") and target_team not in game.get("away_team", ""):
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker.get("key") == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                if market.get("key") != "spreads":
                    continue
                for outcome in market.get("outcomes", []):
                    if target_team in str(outcome.get("name", "")) and float(outcome.get("price", 0)) > best_price:
                        best_price = float(outcome["price"])
                        best_point = outcome.get("point", "")
                        best_book = bookmaker.get("key", "Unknown")
                        best_title = bookmaker.get("title", "Unknown")
                        event_id = str(game.get("id", ""))

    if best_price > 0:
        formatted_point = _format_point_string(best_point)
        return (
            best_title,
            decimal_to_american(best_price),
            formatted_point,
            get_dynamic_link(best_book, target_team),
            event_id,
        )
    return None, None, None, None, None


_ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_espn_schedule(date_str: str):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
    try:
        return get_json(url, headers=_ESPN_HEADERS).get("events", [])
    except Exception as exc:
        print(f"ESPN WNBA schedule fetch failed: {exc}")
        return []


def _is_pregame_game(game: dict) -> bool:
    try:
        state = game["competitions"][0]["status"]["type"]["state"]
    except Exception:
        return False
    return state == "pre"


def run_wnba_model():
    now = get_local_now()
    dates = [now, now + timedelta(days=1)]
    seen_matchups: set[str] = set()
    alerts_sent = 0

    for day in dates:
        day_str = day.strftime("%Y%m%d")
        for game in get_espn_schedule(day_str):
            if not _is_pregame_game(game):
                continue

            competition = game["competitions"][0]
            away = next(item for item in competition["competitors"] if item["homeAway"] == "away")["team"]["displayName"]
            home = next(item for item in competition["competitors"] if item["homeAway"] == "home")["team"]["displayName"]
            matchup = f"{away} @ {home}"
            if matchup in seen_matchups:
                continue
            seen_matchups.add(matchup)

            book, odds, line, link, event_id = get_best_spread(home)
            selection = f"{home} {line}".strip()
            if not book or is_already_logged(matchup, "MODEL_WNBA_SPREAD", selection):
                continue

            pinnacle_reference = format_pinnacle_reference(
                get_master_cache() or {},
                "basketball_wnba",
                event_id,
                "MODEL_WNBA_SPREAD",
                selection,
            )

            spread_abs = abs(float(line)) if line not in (None, "") else 0.0
            model_probability = min(
                WNBA_MODEL_BASE_PROB + (spread_abs * WNBA_MODEL_SPREAD_SLOPE), WNBA_MODEL_PROB_CAP
            )
            fair_price = fair_american_from_probability(model_probability)
            edge = model_edge_from_probability(model_probability, odds)
            units = max(
                0.25,
                round(model_units_from_probability(model_probability, odds, cap=WNBA_MODEL_MAX_UNITS), 2),
            )
            
            if edge < WNBA_MODEL_EDGE_THRESHOLD or units <= 0:
                continue

            was_logged = log_bet_to_db(
                matchup,
                "MODEL_WNBA_SPREAD",
                selection,
                odds,
                edge,
                f"{units:.2f}",
                fair_price,
                "basketball_wnba",
                event_id,
                notes=f"book={book};model=wnba_fatigue;probability={model_probability:.4f};slate={day_str}",
            )
            if not was_logged:
                print(f"Skipping WNBA model alert because DB log failed for {selection}.")
                continue

            send_discord_alert(
                {
                    "embeds": [
                        {
                            "description": (
                                f"**WNBA SPREAD ALERT**\n\n"
                                f"**Game:** {matchup}\n"
                                f"**Bet:** {selection}\n"
                                f"**Advantage:** {home} vs {away}\n"
                                f"**Odds:** [{book}]({link}) @ {odds}\n"
                                f"**Pinnacle:** {pinnacle_reference}\n"
                                f"**Fair Value:** {fair_price}\n"
                                f"**Model Edge:** {edge * 100:.2f}%\n"
                                f"**Suggested:** {units:.2f} Units"
                                f"{build_last_ten_context_line(home, 'spreads', line, 'over', 'basketball_wnba', opponent=away)}"
                            ),
                            "color": 10038562,
                        }
                    ]
                },
                source="model_wnba",
                alert_type="bet_alert",
                dedupe_key=f"{matchup}::{selection}",
                webhook_url=DISCORD_WEBHOOK_URL,
                add_bee_image=True,
            )
            alerts_sent += 1

    return {"detail": "wnba model complete", "count": alerts_sent, "label": "alerts"}


if __name__ == "__main__":
    run_wnba_model()