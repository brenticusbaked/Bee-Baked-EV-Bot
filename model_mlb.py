import os
import urllib.parse

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.http_client import get_json, post_discord
from utils.odds import decimal_to_american
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"


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


def get_best_f5_moneyline(target_team):
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty or failed to load.")
        return None, None, None, None

    best_price = 0.0
    best_book = "Unknown"
    best_book_title = "Unknown"
    event_id = None

    for game in cache.get("baseball_mlb", []):
        if target_team not in game["home_team"] and target_team not in game["away_team"]:
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker["key"] == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                if market["key"] != "h2h_1st_half":
                    continue
                for outcome in market["outcomes"]:
                    if target_team in outcome["name"]:
                        price = float(outcome["price"])
                        if price > best_price:
                            best_price = price
                            best_book = bookmaker["key"]
                            best_book_title = bookmaker["title"]
                            event_id = game["id"]

    if best_price > 0:
        return best_book_title, decimal_to_american(best_price), get_dynamic_link(best_book, target_team), event_id
    return None, None, None, None


def get_advanced_pitcher_stats(pitcher_id):
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}?hydrate=stats(group=[pitching],type=[season])"
    try:
        person = get_json(url).get("people", [{}])[0]
        splits = person.get("stats", [{}])[0].get("splits", [{}])
        if not splits:
            return None, None
        stats = splits[0].get("stat", {})
        k9 = float(stats.get("strikeOutsPer9Inn", 0))
        bb9 = float(stats.get("walksPer9Inn", 0))
        hr9 = float(stats.get("homeRunsPer9", 0))
        era = float(stats.get("era", 9.99))
        est_fip = ((13 * hr9) + (3 * bb9) - (2 * k9)) / 9 + 3.20
        return est_fip, era
    except Exception as exc:
        print(f"Error fetching stats for Pitcher ID {pitcher_id}: {exc}")
        return None, None


def run_mlb_model():
    today = get_local_now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"

    try:
        data = get_json(url)
        dates = data.get("dates", [])
        if not dates:
            print(f"No MLB games scheduled for {today}.")
            return

        alerts = []
        for game in dates[0].get("games", []):
            away_team = game["teams"]["away"]["team"]["name"]
            home_team = game["teams"]["home"]["team"]["name"]
            matchup = f"{away_team} @ {home_team}"

            away_pitcher = game["teams"]["away"].get("probablePitcher")
            home_pitcher = game["teams"]["home"].get("probablePitcher")
            if not away_pitcher or not home_pitcher:
                continue

            away_fip, away_era = get_advanced_pitcher_stats(away_pitcher["id"])
            home_fip, home_era = get_advanced_pitcher_stats(home_pitcher["id"])
            if away_fip is None or home_fip is None:
                continue

            fip_diff = abs(away_fip - home_fip)
            if fip_diff < 1.25:
                continue

            better_team = away_team if away_fip < home_fip else home_team
            if is_already_logged(matchup, "MODEL_MLB_F5", better_team):
                continue

            book, odds, link, event_id = get_best_f5_moneyline(better_team)
            if not book or not event_id:
                continue

            log_bet_to_db(matchup, "MODEL_MLB_F5", better_team, odds, fip_diff, "1.00", "MODEL", "baseball_mlb", event_id)
            alerts.append(
                (
                    f"**MLB ADVANCED METRIC MISMATCH**\n"
                    f"**Game:** {matchup}\n"
                    f"**Advantage:** {better_team} (First 5 Innings)\n"
                    f"{away_pitcher['fullName']} FIP: **{away_fip:.2f}** (ERA: {away_era:.2f})\n"
                    f"{home_pitcher['fullName']} FIP: **{home_fip:.2f}** (ERA: {home_era:.2f})\n"
                    f"**Best F5 ML:** [{book}]({link}) @ {odds}"
                )
            )

        for message in alerts:
            post_discord(
                {"embeds": [{"description": message, "color": 3066993, "image": {"url": FOOTER_IMG}}]},
                webhook_url=DISCORD_WEBHOOK_URL,
            )
    except Exception as exc:
        print(f"Error running MLB model: {exc}")


if __name__ == "__main__":
    run_mlb_model()
