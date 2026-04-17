import os
import urllib.parse

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.http_client import get_json, post_discord
from utils.odds import decimal_to_american
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def get_dynamic_link(bookmaker, target_string):
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(" ", "")
    links = {
        "draftkings": f"https://sportsbook.draftkings.com/search?q={query}",
        "fanduel": f"https://sportsbook.fanduel.com/navigation/search?q={query}",
        "betmgm": f"https://sports.betmgm.com/en/sports/search?q={query}",
        "bet365": f"https://www.bet365.com/#/search?q={query}",
        "espn": f"https://espnbet.com/search?q={query}",
        "fanatics": f"https://sportsbook.fanatics.com/search?q={query}",
    }
    return links.get(book, f"https://www.google.com/search?q={bookmaker}+sportsbook")


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


def run_nhl_model():
    try:
        standings = get_json("https://api-web.nhle.com/v1/standings/now")
        team_goal_diff = {team["teamName"]["default"]: team["goalDifferential"] for team in standings.get("standings", [])}

        today = get_local_now().strftime("%Y-%m-%d")
        schedule = get_json(f"https://api-web.nhle.com/v1/schedule/{today}")
        alerts = []

        if "gameWeek" not in schedule or not schedule["gameWeek"]:
            return

        for game in schedule["gameWeek"][0].get("games", []):
            away = game["awayTeam"]["placeName"]["default"]
            home = game["homeTeam"]["placeName"]["default"]
            matchup = f"{away} @ {home}"

            away_gd = next((gd for name, gd in team_goal_diff.items() if away in name), None)
            home_gd = next((gd for name, gd in team_goal_diff.items() if home in name), None)
            if away_gd is None or home_gd is None:
                continue

            gd_diff = abs(away_gd - home_gd)
            if gd_diff < 40:
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

            log_bet_to_db(matchup, "MODEL_NHL_PUCKLINE", selection, best_odds, gd_diff, "1.00", "MODEL", "icehockey_nhl", event_id)
            alerts.append(
                (
                    f"**NHL MODEL MISMATCH DETECTED**\n"
                    f"**Game:** {matchup}\n"
                    f"**Advantage:** {better_team}\n"
                    f"{better_team} GD: **{better_gd}**\n"
                    f"{worse_team} GD: **{worse_gd}**\n"
                    f"**Net Gap:** {gd_diff} Goals\n"
                    f"**Best Puck Line:** [{best_book}]({bet_link}) | **-1.5 ({best_odds})**"
                )
            )

        for index, message in enumerate(alerts):
            post_discord(
                {"embeds": [{"description": message, "color": 3447003}]},
                webhook_url=DISCORD_WEBHOOK_URL,
                add_bee_image=index == len(alerts) - 1,
            )
    except Exception as exc:
        print(f"Error running NHL model: {exc}")


if __name__ == "__main__":
    run_nhl_model()
