import math
import os
from datetime import timedelta

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import get_json
from services.odds_reference import format_pinnacle_reference
from services.last_ten import build_last_ten_context_line
from utils.links import sportsbook_search_link
from utils.model_pricing import fair_american_from_probability, model_edge_from_probability, model_units_from_probability
from utils.odds import decimal_to_american
from utils.thresholds import env_float
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
NHL_GD_GAP_THRESHOLD = env_float("NHL_GD_GAP_THRESHOLD", 45.0)
NHL_MODEL_EDGE_THRESHOLD = env_float("NHL_MODEL_EDGE_THRESHOLD", 0.02)
NHL_MODEL_MAX_UNITS = env_float("NHL_MODEL_MAX_UNITS", 1.25)


def get_dynamic_link(bookmaker, target_string):
    return sportsbook_search_link(bookmaker, target_string)


def get_best_puckline(target_team):
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty or failed to load.")
        return None, None, None, None

    best_price = 0.0
    best_book = "Unknown"
    best_book_title = "Unknown"
    event_id = None

    for game in cache.get("icehockey_nhl", []):
        if target_team not in game["home_team"] and target_team not in game["away_team"]:
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker["key"] == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                if market["key"] != "spreads":
                    continue
                for outcome in market["outcomes"]:
                    if target_team in outcome["name"] and outcome.get("point") == -1.5:
                        price = float(outcome["price"])
                        if price > best_price:
                            best_price = price
                            best_book = bookmaker["key"]
                            best_book_title = bookmaker["title"]
                            event_id = game["id"]

    if best_price > 0:
        return best_book_title, decimal_to_american(best_price), get_dynamic_link(best_book, target_team), event_id
    return None, None, None, None


def _is_pregame_game(game):
    try:
        return game["gameState"] == "FUT" or game["gameState"] == "PRE"
    except Exception:
        try:
            return game["status"]["type"]["state"] == "pre"
        except Exception:
            return False


def _poisson_pmf(k: int, lam: float) -> float:
    """Poisson probability mass function P(X = k)."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _puckline_prob(lambda_fav: float, lambda_dog: float, spread: float = 1.5) -> float:
    """Probability the favorite wins by more than the given spread using Poisson goals."""
    prob = 0.0
    max_goals = 15
    for fav_goals in range(max_goals + 1):
        p_fav = _poisson_pmf(fav_goals, lambda_fav)
        if p_fav <= 0:
            continue
        for dog_goals in range(max_goals + 1):
            if fav_goals - dog_goals <= spread:
                continue
            p_dog = _poisson_pmf(dog_goals, lambda_dog)
            prob += p_fav * p_dog
    return float(max(0.05, min(0.85, prob)))


def run_nhl_model():
    try:
        standings = get_json("https://api-web.nhle.com/v1/standings/now")
        team_stats = {
            team["teamName"]["default"]: {
                "gf": float(team.get("goalsFor", 0) or 0),
                "ga": float(team.get("goalsAgainst", 0) or 0),
                "gp": float(team.get("gamesPlayed", 1) or 1),
            }
            for team in standings.get("standings", [])
        }

        alerts = []

        for day in (get_local_now(), get_local_now() + timedelta(days=1)):
            today = day.strftime("%Y-%m-%d")
            schedule = get_json(f"https://api-web.nhle.com/v1/schedule/{today}")

            if "gameWeek" not in schedule or not schedule["gameWeek"]:
                continue

            for game in schedule["gameWeek"][0].get("games", []):
                if not _is_pregame_game(game):
                    continue

                away = game["awayTeam"]["placeName"]["default"]
                home = game["homeTeam"]["placeName"]["default"]
                matchup = f"{away} @ {home}"

                away_stats = next((stats for name, stats in team_stats.items() if away in name), None)
                home_stats = next((stats for name, stats in team_stats.items() if home in name), None)
                if away_stats is None or home_stats is None:
                    continue

                away_gd = away_stats["gf"] - away_stats["ga"]
                home_gd = home_stats["gf"] - home_stats["ga"]
                gd_diff = abs(away_gd - home_gd)
                if gd_diff < NHL_GD_GAP_THRESHOLD:
                    continue

                better_team = away if away_gd > home_gd else home
                better_gd = max(away_gd, home_gd)
                worse_team = home if away_gd > home_gd else away
                worse_gd = min(away_gd, home_gd)
                selection = f"{better_team} -1.5"

                if is_already_logged(matchup, "MODEL_NHL_PUCKLINE", selection):
                    continue

                best_book, best_odds, bet_link, event_id = get_best_puckline(better_team)
                if not best_book or not event_id:
                    continue
                pinnacle_reference = format_pinnacle_reference(
                    get_master_cache() or {},
                    "icehockey_nhl",
                    event_id,
                    "MODEL_NHL_PUCKLINE",
                    selection,
                )

                fav_stats = away_stats if away_gd > home_gd else home_stats
                dog_stats = home_stats if away_gd > home_gd else away_stats
                fav_offense = fav_stats["gf"] / fav_stats["gp"]
                fav_defense = fav_stats["ga"] / fav_stats["gp"]
                dog_offense = dog_stats["gf"] / dog_stats["gp"]
                dog_defense = dog_stats["ga"] / dog_stats["gp"]
                lambda_f = max(0.5, (fav_offense + dog_defense) / 2.0)
                lambda_u = max(0.5, (dog_offense + fav_defense) / 2.0)
                model_probability = _puckline_prob(lambda_f, lambda_u)
                fair_price = fair_american_from_probability(model_probability)
                edge = model_edge_from_probability(model_probability, best_odds)
                units = model_units_from_probability(model_probability, best_odds, cap=NHL_MODEL_MAX_UNITS)
                if edge < NHL_MODEL_EDGE_THRESHOLD or units <= 0:
                    continue

                was_logged = log_bet_to_db(
                    matchup,
                    "MODEL_NHL_PUCKLINE",
                    selection,
                    best_odds,
                    edge,
                    f"{units:.2f}",
                    fair_price,
                    "icehockey_nhl",
                    event_id,
                    notes=f"book={best_book};model=nhl_goal_diff;probability={model_probability:.4f};gap={gd_diff};slate={today}",
                )
                if not was_logged:
                    print(f"Skipping NHL model alert because DB log failed for {selection}.")
                    continue
                alerts.append(
                    (
                        f"**NHL MODEL MISMATCH DETECTED**\n"
                        f"**Game:** {matchup}\n"
                        f"**Advantage:** {better_team}\n"
                        f"{better_team} GD: **{better_gd}**\n"
                        f"{worse_team} GD: **{worse_gd}**\n"
                        f"**Net Gap:** {gd_diff} Goals\n"
                        f"**Best Puck Line:** [{best_book}]({bet_link}) | **-1.5 ({best_odds})**\n"
                        f"**Pinnacle:** {pinnacle_reference}\n"
                        f"**Fair Value:** {fair_price}\n"
                        f"**Model Edge:** {edge * 100:.2f}%\n"
                        f"**Suggested:** {units:.2f} Units"
                        f"{build_last_ten_context_line(better_team, 'spreads', '-1.5', 'over', 'icehockey_nhl', opponent=worse_team)}"
                    )
                )

        for index, message in enumerate(alerts):
            send_discord_alert(
                {"embeds": [{"description": message, "color": 3447003}]},
                source="model_nhl",
                alert_type="bet_alert",
                dedupe_key=message[:200],
                webhook_url=DISCORD_WEBHOOK_URL,
                add_bee_image=index == len(alerts) - 1,
            )
        return {"detail": "nhl model complete", "count": len(alerts), "label": "alerts"}
    except Exception as exc:
        print(f"Error running NHL model: {exc}")
        return {"detail": f"nhl model error: {exc}", "count": 0, "label": "alerts"}


if __name__ == "__main__":
    run_nhl_model()
