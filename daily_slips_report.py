import os
from datetime import timedelta

import pandas as pd

from db_manager import get_all_bets
from services.http_client import post_discord
from utils.odds import profit_for_result
from utils.time import get_local_now


DISCORD_DAILY_SLIPS_WEBHOOK_URL = (
    os.getenv("DISCORD_DAILY_SLIPS_WEBHOOK_URL")
    or os.getenv("DISCORD_STATUS_WEBHOOK_URL")
    or os.getenv("DISCORD_WEBHOOK_URL")
)


def _safe_numeric(series, default=0.0):
    if series is None:
        return default
    cleaned = pd.to_numeric(series, errors="coerce")
    return cleaned


def build_daily_slips_report() -> str:
    all_bets = get_all_bets()
    if not all_bets:
        return "**BEE BAKED DAILY SLIPS**\nNo bets logged yet."

    df = pd.DataFrame(all_bets)
    if df.empty:
        return "**BEE BAKED DAILY SLIPS**\nNo bets logged yet."

    report_date = (get_local_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    day_df = df[df.get("date", "").astype(str) == report_date].copy()
    if day_df.empty:
        return f"**BEE BAKED DAILY SLIPS**\nDate: {report_date}\nNo slips were logged."

    graded_df = day_df[day_df.get("result", "").astype(str).isin(["WIN", "LOSS", "PUSH"])].copy()
    wins = int((graded_df.get("result", "").astype(str) == "WIN").sum()) if not graded_df.empty else 0
    losses = int((graded_df.get("result", "").astype(str) == "LOSS").sum()) if not graded_df.empty else 0
    pushes = int((graded_df.get("result", "").astype(str) == "PUSH").sum()) if not graded_df.empty else 0

    net_units = 0.0
    if not graded_df.empty:
        net_units = sum(
            profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
            for _, row in graded_df.iterrows()
        )

    day_df["clv_edge_num"] = _safe_numeric(day_df.get("clv_edge_pct"))
    clv_df = day_df.dropna(subset=["clv_edge_num"]).copy()
    clv_beaten_rate = 0.0
    avg_clv = 0.0
    if not clv_df.empty:
        clv_beaten_rate = float((clv_df["clv_edge_num"] > 0).mean() * 100.0)
        avg_clv = float(clv_df["clv_edge_num"].mean())

    by_source = []
    if "bet_source" in day_df.columns:
        for source, group in day_df.groupby(day_df["bet_source"].fillna("unknown").astype(str)):
            by_source.append(f"`{source}` {len(group)}")
    source_text = " | ".join(by_source[:6]) if by_source else "n/a"

    return (
        f"**BEE BAKED DAILY SLIPS**\n"
        f"Date: {report_date}\n"
        f"Logged Slips: {len(day_df)}\n"
        f"Record: {wins}-{losses}"
        + (f"-{pushes}" if pushes else "")
        + "\n"
        f"Net Units: {net_units:+.2f}\n"
        f"CLV Tracked: {len(clv_df)}\n"
        f"CLV Beaten: {clv_beaten_rate:.1f}%\n"
        f"Avg CLV Edge: {avg_clv:+.2f}%\n"
        f"Sources: {source_text}"
    )


def send_daily_slips_report():
    report = build_daily_slips_report()
    post_discord(
        {"embeds": [{"description": report, "color": 10181046}]},
        webhook_url=DISCORD_DAILY_SLIPS_WEBHOOK_URL,
        add_bee_image=True,
        bee_image_slot="daily_slips",
    )
    return {"detail": "daily slips report complete", "count": 1, "label": "updates"}


if __name__ == "__main__":
    send_daily_slips_report()
