import os

from db_manager import get_all_clv_bets
from services.http_client import post_discord
from utils.odds import american_to_decimal


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"


def run_clv_analysis():
    bets = get_all_clv_bets()
    if not bets:
        return

    total_bets_with_clv = len(bets)
    clv_beaten = 0
    total_clv_value = 0.0

    for bet in bets:
        try:
            taken_decimal = american_to_decimal(bet["odds"])
            closing_decimal = american_to_decimal(bet["closing_line_pinnacle"])
        except Exception:
            continue

        if taken_decimal > closing_decimal:
            clv_beaten += 1
        total_clv_value += (taken_decimal / closing_decimal) - 1

    if total_bets_with_clv <= 0:
        return

    win_rate = (clv_beaten / total_bets_with_clv) * 100
    avg_clv_edge = (total_clv_value / total_bets_with_clv) * 100
    color = 5763719 if win_rate >= 50 else 15158332
    message = (
        f"**$BEE BAKED SHARP METRICS**\n"
        f"**Total Bets Tracked:** {total_bets_with_clv}\n"
        f"**CLV Beaten Rate:** {win_rate:.1f}%\n"
        f"**Avg Edge vs Close:** {avg_clv_edge:+.2f}%\n\n"
        f"*Consistently beating the Pinnacle close is the best health check for the edge pipeline.*"
    )
    post_discord(
        {"embeds": [{"description": message, "color": color, "image": {"url": FOOTER_IMG}}]},
        webhook_url=DISCORD_WEBHOOK_URL,
    )


if __name__ == "__main__":
    run_clv_analysis()
