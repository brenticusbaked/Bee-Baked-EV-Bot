import os
import urllib.parse

from db_manager import is_already_logged, log_bet_to_db
from services.http_client import post_discord, request
from utils.odds import decimal_to_american, quarter_kelly_units


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"
TARGET_STATS = ["points", "assists", "rebounds"]


def to_decimal(price):
    try:
        price = float(price)
        if price > 100:
            return (price / 100) + 1
        if price < -100:
            return (100 / abs(price)) + 1
        return price
    except Exception:
        return 1.909


def get_dynamic_link(bookmaker, target_string):
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(" ", "").replace("sportsbook", "")
    app_schemes = {
        "draftkings": f"draftkings://sportsbook/search?q={query}",
        "fanduel": f"fanduel://sportsbook/navigation/search?q={query}",
        "betmgm": f"betmgm://sportsbook/search?q={query}",
        "caesars": f"caesars://sportsbook/search?q={query}",
        "prizepicks": f"https://app.prizepicks.com/search/{query}",
    }
    return app_schemes.get(book, f"https://www.google.com/search?q={bookmaker}+{query}")


def get_sgo_edges():
    if not SGO_API_KEY:
        return []

    soft_list = [
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
    ]
    picks = []
    url = "https://api.sportsgameodds.com/v2/events"
    params = {"apiKey": SGO_API_KEY, "leagueID": "NBA", "oddsAvailable": "true"}

    try:
        data = request("GET", url, params=params, timeout=15).json()
        for event in data:
            matchup = event.get("name", "Unknown Matchup")
            market_groups = {}
            for odd_key, odd_obj in event.get("odds", {}).items():
                parts = odd_obj.get("oddID", odd_key).split("-")
                if len(parts) < 5 or parts[0] not in TARGET_STATS:
                    continue

                stat, player_raw, side = parts[0], parts[1], parts[4]
                book = odd_obj.get("bookmakerID", "unknown")
                price = to_decimal(odd_obj.get("price"))
                line = odd_obj.get("handicap")
                prop_link = odd_obj.get("deepLink")
                player = player_raw.split("_1_")[0].replace("_", " ").title()
                uid = f"{player}_{stat}_{line}"
                market_groups.setdefault(uid, {"sharp": {}, "soft": {}})

                if book == "pinnacle":
                    market_groups[uid]["sharp"][side] = price
                elif book in soft_list and price > market_groups[uid]["soft"].get(side, {}).get("price", 0):
                    market_groups[uid]["soft"][side] = {"price": price, "book": book, "line": line, "prop_link": prop_link}

            for uid, value in market_groups.items():
                sharp, soft = value["sharp"], value["soft"]
                if "over" not in sharp or "under" not in sharp:
                    continue
                vig = (1 / sharp["over"]) + (1 / sharp["under"])
                probabilities = {"over": (1 / sharp["over"]) / vig, "under": (1 / sharp["under"]) / vig}
                for side in ["over", "under"]:
                    if side not in soft:
                        continue
                    edge = (soft[side]["price"] * probabilities[side]) - 1
                    if edge <= 0.02:
                        continue
                    player_name, stat_type, line_value = uid.split("_")
                    market = stat_type.upper()
                    selection = f"{player_name} {side.upper()} {line_value}"
                    if is_already_logged(matchup, market, selection):
                        continue

                    units = quarter_kelly_units(edge, soft[side]["price"])
                    log_bet_to_db(
                        matchup.strip(),
                        market,
                        selection,
                        decimal_to_american(soft[side]["price"]),
                        edge,
                        f"{units:.2f}",
                        decimal_to_american(1 / probabilities[side]),
                        "basketball_nba",
                        str(event.get("id", "")),
                    )
                    link = soft[side].get("prop_link") or get_dynamic_link(soft[side]["book"], player_name)
                    picks.append(
                        {
                            "msg": (
                                f"**NBA PROP ALERT**\n"
                                f"**Match:** {matchup}\n"
                                f"**Prop:** {selection}\n"
                                f"**Book:** [{soft[side]['book'].upper()}]({link}) @ {decimal_to_american(soft[side]['price'])}\n"
                                f"**Edge:** {edge * 100:.2f}%"
                            )
                        }
                    )
    except Exception as exc:
        print(f"Prop Bot Error: {exc}")
    return picks


def main():
    picks = get_sgo_edges()
    for pick in picks:
        post_discord(
            {"embeds": [{"description": pick["msg"], "color": 15158332, "image": {"url": FOOTER_IMG}}]},
            webhook_url=DISCORD_WEBHOOK_URL,
        )


if __name__ == "__main__":
    main()
