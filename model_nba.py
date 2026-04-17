import os
import urllib.parse
from datetime import timedelta

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.http_client import get_json, post_discord
from utils.odds import decimal_to_american
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def get_dynamic_link(bookmaker, target_string):
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(" ", "").replace("sportsbook", "")
    links = {
        "draftkings": f"https://sportsbook.draftkings.com/search?q={query}",
        "fanduel": f"https://sportsbook.fanduel.com/navigation/search?q={query}",
        "betmgm": f"https://sports.betmgm.com/en/sports/search?q={query}",
        "bet365": f"https://www.bet365.com/#/search?q={query}",
        "caesars": f"https://sportsbook.caesars.com/us/ky/bet/search?q={query}",
        "betrivers": f"https://betrivers.com/?page=search&q={query}",
        "bovada": f"https://www.bovada.lv/sports?search={query}",
    }
    return links.get(book, f"https://www.google.com/search?q={bookmaker}+sportsbook")


def get_best_spread(target_team):
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty or failed to load.")
        return None, None, None, None, None

    best_price, best_point, best_book, best_title, event_id = 0.0, "", "Unknown", "Unknown", ""
    for game in cache.get("basketball_nba", []):
        if target_team not in game["home_team"] and target_team not in game["away_team"]:
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker["key"] == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                if market["key"] != "spreads":
                    continue
                for outcome in market["outcomes"]:
                    if target_team in outcome["name"] and float(outcome["price"]) > best_price:
                        best_price = float(outcome["price"])
                        best_point = outcome["point"]
                        best_book = bookmaker["key"]
                        best_title = bookmaker["title"]
                        event_id = game["id"]

    if best_price > 0:
        return best_title, decimal_to_american(best_price), str(best_point), get_dynamic_link(best_book, target_team), event_id
    return None, None, None, None, None


def get_espn_schedule(date_str):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    try:
        return get_json(url).get("events", [])
    except Exception as exc:
        print(f"ESPN schedule fetch failed: {exc}")
        return []


def run_nba_model():
    today = get_local_now()
    today_str = today.strftime("%Y%m%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y%m%d")

    teams_yesterday = {}
    for game in get_espn_schedule(yesterday_str):
        for competitor in game.get("competitions", [{}])[0].get("competitors", []):
            teams_yesterday[competitor["team"]["displayName"]] = competitor["homeAway"]

    for game in get_espn_schedule(today_str):
        competition = game["competitions"][0]
        away = next(item for item in competition["competitors"] if item["homeAway"] == "away")["team"]["displayName"]
        home = next(item for item in competition["competitors"] if item["homeAway"] == "home")["team"]["displayName"]

        if away not in teams_yesterday or home in teams_yesterday:
            continue

        book, odds, line, link, event_id = get_best_spread(home)
        selection = f"{home} {line}"
        if not book or is_already_logged(f"{away} @ {home}", "MODEL_NBA_SPREAD", selection):
            continue

        log_bet_to_db(
            f"{away} @ {home}",
            "MODEL_NBA_SPREAD",
            selection,
            odds,
            "FATIGUE",
            "1.0",
            "MODEL",
            "basketball_nba",
            event_id,
        )
        post_discord(
            {
                "embeds": [
                    {
                        "description": (
                            f"**NBA FATIGUE ALERT**\n"
                            f"Advantage: **{home}** vs {away} (Road B2B)\n"
                            f"Odds: [{book}]({link}) @ {odds}"
                        ),
                        "color": 16734003,
                    }
                ]
            },
            webhook_url=DISCORD_WEBHOOK_URL,
            add_bee_image=True,
        )


if __name__ == "__main__":
    run_nba_model()
