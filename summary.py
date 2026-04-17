import os

import pandas as pd

from db_manager import get_all_bets
from services.http_client import post_discord
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"


def calculate_clv_report():
    data = get_all_bets()
    if not data:
        return "**Database is empty.** No bets recorded yet."

    try:
        df = pd.DataFrame(data)
        total_bets = len(df)
        df["edge_val"] = pd.to_numeric(df["edge"].astype(str).str.replace("%", ""), errors="coerce")
        avg_ev = df["edge_val"].dropna().mean()

        df["Odds_Num"] = pd.to_numeric(df["odds"].astype(str).str.replace("+", ""), errors="coerce")
        df["CLV_Num"] = pd.to_numeric(df["closing_line_pinnacle"].astype(str).str.replace("+", ""), errors="coerce")
        beat_clv_count = len(df.dropna(subset=["Odds_Num", "CLV_Num"])[df["Odds_Num"] > df["CLV_Num"]])
        clv_pct = (beat_clv_count / total_bets) * 100 if total_bets > 0 else 0

        return (
            f"**WEEKLY $BEE BAKED CLV REPORT**\n"
            f"**Generated:** {get_local_now().strftime('%Y-%m-%d')}\n"
            f"**Total Alerts Logged:** {total_bets}\n"
            f"**Avg. Expected Value:** {avg_ev:.2f}%\n"
            f"**Beat Closing Line (CLV):** {clv_pct:.1f}%\n"
            f"*A CLV over 50% confirms the bot is still finding market-dislocated entries.*"
        )
    except Exception as exc:
        return f"**Error processing logs:** {exc}"


def main():
    report_msg = calculate_clv_report()
    post_discord(
        {"embeds": [{"title": "Weekly Market Performance", "description": report_msg, "color": 10181046, "image": {"url": FOOTER_IMG}}]},
        webhook_url=DISCORD_WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
