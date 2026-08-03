import math
import os
from datetime import datetime, timezone

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from models.nfl_proe import build_proe_context
from services.alerts import send_discord_alert
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import get_json
from utils.model_pricing import fair_american_from_probability
from utils.odds import decimal_to_american, quarter_kelly_units
from utils.thresholds import env_float
from utils.venue_coordinates import lookup_nfl_team
from utils.weather import fetch_open_meteo_weather, nfl_weather_total_shift


DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
NFL_MODEL_EDGE_THRESHOLD = env_float("NFL_MODEL_EDGE_THRESHOLD", 0.02)
NFL_MODEL_MAX_UNITS = env_float("NFL_MODEL_MAX_UNITS", 1.25)


def _is_pregame(event: dict) -> bool:
    try:
        return str(event["status"]["type"]["state"]).lower() == "pre"
    except Exception:
        return False


def _team_name(team: dict) -> str:
    return team.get("displayName") or team.get("name") or team.get("abbreviation") or ""


def _game_total_from_cache(home: str, away: str):
    """Return (offered_line, offered_decimal_price, book_title) for a game total over."""
    cache = get_master_cache()
    if not cache:
        return None, None, None

    for game in cache.get("americanfootball_nfl", []):
        if home not in (game["home_team"], game["away_team"]) and away not in (
            game["home_team"],
            game["away_team"],
        ):
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker["key"] == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if str(outcome.get("name") or "").lower() == "over":
                        try:
                            point = float(outcome.get("point", 0))
                            price = float(outcome.get("price", 0))
                            return point, price, bookmaker.get("title", "Unknown")
                        except (TypeError, ValueError):
                            continue
    return None, None, None


def _model_over_probability(expected_total: float, offered_line: float) -> float:
    """Logistic probability that the game goes over the offered total."""
    if offered_line <= 0:
        return 0.5
    return max(0.05, min(0.95, 1.0 / (1.0 + math.exp(-0.15 * (expected_total - offered_line)))))


def run_nfl_model():
    print("Initializing NFL weather + PROE total model...")
    proe_ctx = build_proe_context()
    if not proe_ctx.loaded:
        print("PROE context not loaded; NFL model cannot run.")
        return {"detail": "nfl model skipped", "count": 0, "alerts": []}

    scoreboard = get_json("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard")
    events = scoreboard.get("events", []) if scoreboard else []
    alerts = []

    for event in events:
        if not _is_pregame(event):
            continue

        competition = event.get("competitions", [{}])[0]
        home = away = None
        for competitor in competition.get("competitors", []):
            team_name = _team_name(competitor.get("team", {}))
            if competitor.get("homeAway") == "home":
                home = team_name
            else:
                away = team_name
        if not home or not away:
            continue

        home_data = proe_ctx.get(home)
        away_data = proe_ctx.get(away)
        if not home_data or not away_data:
            continue

        coords = lookup_nfl_team(home)
        weather = fetch_open_meteo_weather(*coords) if coords else None

        base_total = (
            (home_data.get("ppg", 0.0) + away_data.get("papg", 0.0))
            + (away_data.get("ppg", 0.0) + home_data.get("papg", 0.0))
        ) / 2.0

        proe_shift = (
            home_data.get("success_signal", 0.0) + away_data.get("success_signal", 0.0)
        ) * 1.5
        weather_shift = nfl_weather_total_shift(weather or {})
        expected_total = base_total + proe_shift + weather_shift

        offered_line, offered_price, book = _game_total_from_cache(home, away)
        if not offered_line or not offered_price:
            continue

        matchup = f"{away} @ {home}"
        selection = f"Over {offered_line}"
        if is_already_logged(matchup, "MODEL_NFL_TOTAL", selection):
            continue

        model_probability = _model_over_probability(expected_total, offered_line)
        edge = offered_price * model_probability - 1.0
        if edge < NFL_MODEL_EDGE_THRESHOLD:
            continue

        units = quarter_kelly_units(offered_price, model_probability, cap=NFL_MODEL_MAX_UNITS)
        if units <= 0:
            continue

        fair_price = fair_american_from_probability(model_probability)
        was_logged = log_bet_to_db(
            matchup,
            "MODEL_NFL_TOTAL",
            selection,
            decimal_to_american(offered_price),
            edge,
            f"{units:.2f}",
            fair_price,
            "americanfootball_nfl",
            book,
        )
        if not was_logged:
            continue

        weather_bits = []
        if weather:
            if weather.get("temp_f") is not None:
                weather_bits.append(f"{weather['temp_f']:.0f}F")
            if weather.get("wind_mph") is not None:
                weather_bits.append(f"Wind {weather['wind_mph']:.0f} mph")
            if weather.get("precipitation_mm", 0) > 0:
                weather_bits.append(f"Precip {weather['precipitation_mm']:.1f} mm")

        embed = {
            "title": "+EV NFL Total Alert",
            "color": 0x2ECC71,
            "fields": [
                {"name": "Matchup", "value": matchup[:256], "inline": False},
                {"name": "Play", "value": f"Over {offered_line} @ {book}", "inline": False},
                {
                    "name": "Model",
                    "value": f"Expected total {expected_total:.1f} | Prob {model_probability:.1%}",
                    "inline": False,
                },
                {"name": "Edge / Units", "value": f"{edge:.2%} | {units:.2f}u", "inline": False},
                {
                    "name": "Weather",
                    "value": " | ".join(weather_bits) if weather_bits else "n/a",
                    "inline": False,
                },
            ],
            "footer": {"text": "BEE BAKED BETS | NFL Weather Model"},
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        alerts.append({"matchup": matchup, "embed": embed})
        send_discord_alert(
            payload={"embeds": [embed]},
            source="nfl_weather_model",
            alert_type="NFL_TOTAL",
            dedupe_key=f"{matchup}|{offered_line}",
            webhook_url=DISCORD_WEBHOOK_URL,
        )

    return {
        "detail": "nfl model execution complete",
        "count": len(alerts),
        "alerts": alerts,
    }


if __name__ == "__main__":
    run_nfl_model()
