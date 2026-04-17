import os

from db_manager import is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.book_weights import get_book_weights
from services.http_client import request
from utils.links import sportsbook_search_link
from utils.odds import decimal_to_american, quarter_kelly_units
from utils.thresholds import env_float


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY")
TARGET_STATS = ["points", "assists", "rebounds"]
PROP_EV_THRESHOLD = env_float("PROP_EV_THRESHOLD", 0.02)
PROP_NEAR_MISS_THRESHOLD = env_float("PROP_NEAR_MISS_THRESHOLD", 0.01)


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
    return sportsbook_search_link(bookmaker, target_string)


def get_sgo_edges():
    if not SGO_API_KEY:
        return [], []

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
    near_misses = []
    book_weights = get_book_weights()
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
                    player_name, stat_type, line_value = uid.split("_")
                    book_weight = book_weights.get(soft[side]["book"], 1.0)
                    weighted_score = edge * book_weight
                    if PROP_NEAR_MISS_THRESHOLD <= edge < PROP_EV_THRESHOLD:
                        near_misses.append(
                            {
                                "matchup": matchup,
                                "selection": f"{player_name} {side.upper()} {line_value}",
                                "book": soft[side]["book"],
                                "edge": edge,
                                "weight": book_weight,
                            }
                        )
                    if edge < PROP_EV_THRESHOLD:
                        continue
                    market = stat_type.upper()
                    selection = f"{player_name} {side.upper()} {line_value}"
                    if is_already_logged(matchup, market, selection):
                        continue

                    units = quarter_kelly_units(edge, soft[side]["price"])
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
                        notes=f"book={soft[side]['book']};market=prop",
                    )
                    if not was_logged:
                        print(f"Skipping prop alert because DB log failed for {selection}.")
                        continue
                    link = soft[side].get("prop_link") or get_dynamic_link(soft[side]["book"], player_name)
                    picks.append(
                        {
                            "score": weighted_score,
                            "msg": (
                                f"**NBA PROP ALERT**\n"
                                f"**Match:** {matchup}\n"
                                f"**Prop:** {selection}\n"
                                f"**Book:** [{soft[side]['book'].upper()}]({link}) @ {decimal_to_american(soft[side]['price'])}\n"
                                f"**Edge:** {edge * 100:.2f}%\n"
                                f"**Book Weight:** {book_weight:.2f}x"
                            )
                        }
                    )
    except Exception as exc:
        print(f"Prop Bot Error: {exc}")
    return picks, near_misses


def main():
    if not SGO_API_KEY:
        print("SGO_API_KEY missing. Skipping NBA prop bot.")
        return {"detail": "SGO_API_KEY missing", "count": 0, "label": "alerts"}

    picks, near_misses = get_sgo_edges()
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
    near_miss_text = ""
    if near_misses:
        total_near_misses = len(near_misses)
        near_misses = sorted(near_misses, key=lambda item: item["edge"], reverse=True)[:3]
        samples = " | ".join(
            f"{item['matchup']} - {item['selection']} @ {item['book']} ({item['edge'] * 100:.2f}%, {item['weight']:.2f}x)"
            for item in near_misses
        )
        near_miss_text = f"; near misses: {total_near_misses} total, top {len(near_misses)} -> {samples}"
    return {
        "detail": f"prop bot complete{near_miss_text}",
        "count": len(picks),
        "label": "alerts",
        "meta": {"near_miss_summary": near_miss_text.lstrip("; ").strip()} if near_miss_text else {},
    }
    


if __name__ == "__main__":
    main()
