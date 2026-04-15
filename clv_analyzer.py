import os
import requests
from db_manager import get_all_clv_bets

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def american_to_decimal(american_str):
    try:
        american = float(american_str.replace('+', '').strip())
        if american > 0: return (american / 100.0) + 1.0
        elif american < 0: return (100.0 / abs(american)) + 1.0
        return 1.0
    except ValueError:
        return 0.0

def run_clv_analysis():
    bets = get_all_clv_bets()
    if not bets:
        print("Not enough CLV data to analyze yet.")
        return

    total_bets_with_clv = len(bets)
    clv_beaten = 0
    total_clv_value = 0.0

    for bet in bets:
        taken_dec = american_to_decimal(bet['odds'])
        closing_dec = american_to_decimal(bet['closing_line_pinnacle'])
        
        if taken_dec > 0 and closing_dec > 0:
            if taken_dec > closing_dec:
                clv_beaten += 1
            total_clv_value += (taken_dec / closing_dec) - 1

    if total_bets_with_clv > 0:
        win_rate = (clv_beaten / total_bets_with_clv) * 100
        avg_clv_edge = (total_clv_value / total_bets_with_clv) * 100
        
        if DISCORD_WEBHOOK_URL:
            color = 5763719 if win_rate >= 50 else 15158332
            msg = (
                f"📈 **$BEE BAKED SHARP METRICS** 📈\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"**Total Bets Tracked:** {total_bets_with_clv}\n"
                f"**CLV Beaten Rate:** {win_rate:.1f}%\n"
                f"**Avg Edge vs Close:** {avg_clv_edge:+.2f}%\n\n"
                f"*Note: Consistently beating the Pinnacle close > 50% guarantees a long-term mathematical advantage.*"
            )
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": color, "image": {"url": FOOTER_IMG}}]})

if __name__ == "__main__":
    run_clv_analysis()