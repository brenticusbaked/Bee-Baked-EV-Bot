import os

from db_manager import get_all_graded_bets
from services.http_client import post_discord
from utils.odds import profit_for_result


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def run_accountant():
    graded_bets = get_all_graded_bets()
    if not graded_bets:
        return

    total_profit = 0.0
    wins = 0
    graded = len(graded_bets)

    for row in graded_bets:
        result = row.get("result", "")
        units = float(row.get("units", 0) or 0)
        total_profit += profit_for_result(row.get("odds", 0), units, result)
        if result == "WIN":
            wins += 1

    if graded > 0:
        report = (
            f"**$BEE BAKED WEEKLY REPORT**\n"
            f"Net Profit: **{total_profit:+.2f} Units**\n"
            f"Win Rate: {(wins / graded) * 100:.1f}%"
        )
        post_discord(
            {"embeds": [{"description": report, "color": 5763719 if total_profit >= 0 else 15158332}]},
            webhook_url=DISCORD_WEBHOOK_URL,
        )


if __name__ == "__main__":
    run_accountant()
