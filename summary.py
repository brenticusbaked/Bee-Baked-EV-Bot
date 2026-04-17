import os

import pandas as pd

from db_manager import get_all_bets
from services.http_client import post_discord
from utils.odds import american_to_decimal
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

        if "edge_pct" in df.columns:
            df["edge_val"] = pd.to_numeric(df["edge_pct"], errors="coerce")
        else:
            df["edge_val"] = pd.to_numeric(df["edge"].astype(str).str.replace("%", ""), errors="coerce")
        avg_ev = df["edge_val"].dropna().mean()

        if "odds_decimal" not in df.columns:
            df["odds_decimal"] = df["odds"].apply(lambda x: american_to_decimal(x) if isinstance(x, str) else None)
        if "closing_line_decimal" not in df.columns:
            df["closing_line_decimal"] = df["closing_line_pinnacle"].apply(
                lambda x: american_to_decimal(x) if isinstance(x, str) and x else None
            )

        beat_clv_mask = (pd.to_numeric(df["odds_decimal"], errors="coerce") > pd.to_numeric(df["closing_line_decimal"], errors="coerce"))
        beat_clv_count = int(beat_clv_mask.fillna(False).sum())
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
