import os
from datetime import datetime, timezone

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.http_client import post_discord
from utils.links import sportsbook_search_link
from utils.odds import decimal_implied_probability, decimal_to_american, quarter_kelly_units


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def get_mobile_app_link(book_key, selection_id, event_id, matchup):
    del selection_id, event_id
    return sportsbook_search_link(book_key, matchup)


def calculate_edge(offered_price: float, sharp_price: float) -> float:
    fair_probability = decimal_implied_probability(sharp_price)
    return (offered_price * fair_probability) - 1.0


def scan_markets():
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return {"detail": "cache empty", "count": 0, "label": "alerts"}

    alerts = []
    soft_books = ["fanduel", "draftkings", "betmgm", "bet365", "caesars", "bovada"]
    now = datetime.now(timezone.utc)

    for sport, events in cache.items():
        for event in events:
            commence_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
            if now > commence_time:
                continue

            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    market_key = market["key"]
                    markets.setdefault(market_key, {"sharp": {}, "soft": []})

                    if bookmaker["key"] == "pinnacle":
                        for outcome in market.get("outcomes", []):
                            outcome_key = (
                                outcome["name"].lower().strip(),
                                str(outcome.get("point", "")),
                            )
                            markets[market_key]["sharp"][outcome_key] = float(outcome["price"])
                    elif bookmaker["key"] in soft_books:
                        for outcome in market.get("outcomes", []):
                            markets[market_key]["soft"].append(
                                {
                                    "book": bookmaker["title"],
                                    "book_key": bookmaker["key"],
                                    "name": outcome["name"],
                                    "price": float(outcome["price"]),
                                    "point": outcome.get("point", ""),
                                    "id": outcome.get("id"),
                                }
                            )

            for market_type, data in markets.items():
                sharp = data["sharp"]
                if not sharp:
                    continue

                best_edge = {"edge": 0.0, "bet": None, "sharp_price": None}
                for soft_bet in data["soft"]:
                    outcome_key = (
                        soft_bet["name"].lower().strip(),
                        str(soft_bet.get("point", "")),
                    )
                    if outcome_key not in sharp:
                        continue

                    pinnacle_price = sharp[outcome_key]
                    edge = calculate_edge(soft_bet["price"], pinnacle_price)
                    if edge > 0.02 and edge > best_edge["edge"]:
                        best_edge = {"edge": edge, "bet": soft_bet, "sharp_price": pinnacle_price}

                final = best_edge["bet"]
                if not final:
                    continue

                selection = f"{final['name']} {final['point']}".strip()
                if is_already_logged(matchup, market_type, selection):
                    continue

                edge = best_edge["edge"]
                offered_price = final["price"]
                fair_price = best_edge["sharp_price"]
                units = quarter_kelly_units(edge, offered_price)
                fair_price_american = decimal_to_american(fair_price)

                log_bet_to_db(
                    matchup,
                    market_type,
                    selection,
                    decimal_to_american(offered_price),
                    edge,
                    f"{units:.2f}",
                    fair_price_american,
                    sport,
                    event["id"],
                )

                app_link = get_mobile_app_link(final["book_key"], final["id"], event["id"], matchup)
                alerts.append(
                    {
                        "description": (
                            f"**+EV {market_type.upper()} ALERT**\n\n"
                            f"**Match:** {matchup}\n"
                            f"**Bet:** {selection}\n"
                            f"**Book:** [{final['book']}]({app_link}) @ {decimal_to_american(offered_price)}\n"
                            f"**Fair Value:** {fair_price_american}\n"
                            f"**Edge:** {edge * 100:.2f}%\n"
                            f"**Suggested:** {units:.2f} Units"
                        )
                    }
                )

    for index, alert in enumerate(alerts):
        post_discord(
            {
                "embeds": [
                    {
                        "description": alert["description"],
                        "color": 3066993,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ]
            },
            webhook_url=DISCORD_WEBHOOK_URL,
            add_bee_image=index == len(alerts) - 1,
        )

    return {"detail": "scan complete", "count": len(alerts), "label": "alerts"}


if __name__ == "__main__":
    scan_markets()
