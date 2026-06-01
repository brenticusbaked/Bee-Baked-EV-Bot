from datetime import timedelta

import pandas as pd

from db_manager import get_all_bets
from services.discord_channels import DAILY_SLIPS_WEBHOOK_URL
from services.http_client import post_discord
from utils.odds import profit_for_result
from utils.time import DEFAULT_TZ, get_local_now


DISCORD_DAILY_SLIPS_WEBHOOK_URL = DAILY_SLIPS_WEBHOOK_URL


def _safe_numeric(series):
    if series is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(series, errors="coerce")


def _local_date_mask(df: pd.DataFrame, column: str, report_date: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    timestamps = pd.to_datetime(df[column], errors="coerce", utc=True)
    local_dates = timestamps.dt.tz_convert(DEFAULT_TZ).dt.strftime("%Y-%m-%d")
    return local_dates.eq(report_date).fillna(False)


def build_daily_slips_report() -> str:
    all_bets = get_all_bets()
    if not all_bets:
        return "**BEE BAKED DAILY SLIPS**\nNo bets logged yet."

    df = pd.DataFrame(all_bets)
    if df.empty:
        return "**BEE BAKED DAILY SLIPS**\nNo bets logged yet."

    report_date = (get_local_now() - timedelta(days=1)).strftime("%Y-%m-%d")

    placed_df = df[df.get("date", "").astype(str) == report_date].copy()
    settled_df = df[_local_date_mask(df, "graded_at", report_date)].copy()
    if settled_df.empty:
        # Backward-compatible fallback for older rows without graded_at.
        settled_df = df[
            df.get("date", "").astype(str).eq(report_date)
            & df.get("result", "").astype(str).isin(["WIN", "LOSS", "PUSH"])
        ].copy()

    clv_df = df[_local_date_mask(df, "clv_tracked_at", report_date)].copy()
    if clv_df.empty:
        clv_df = df[
            df.get("date", "").astype(str).eq(report_date)
            & _safe_numeric(df.get("clv_edge_pct")).notna()
        ].copy()

    wins = int((settled_df.get("result", "").astype(str) == "WIN").sum()) if not settled_df.empty else 0
    losses = int((settled_df.get("result", "").astype(str) == "LOSS").sum()) if not settled_df.empty else 0
    pushes = int((settled_df.get("result", "").astype(str) == "PUSH").sum()) if not settled_df.empty else 0

    net_units = 0.0
    if not settled_df.empty:
        net_units = sum(
            profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
            for _, row in settled_df.iterrows()
        )

    clv_beaten_rate = 0.0
    avg_clv = 0.0
    if not clv_df.empty:
        clv_df["clv_edge_num"] = _safe_numeric(clv_df.get("clv_edge_pct"))
        clv_df = clv_df.dropna(subset=["clv_edge_num"]).copy()
        if not clv_df.empty:
            clv_beaten_rate = float((clv_df["clv_edge_num"] > 0).mean() * 100.0)
            avg_clv = float(clv_df["clv_edge_num"].mean())

    placed_by_source = []
    if not placed_df.empty and "bet_source" in placed_df.columns:
        for source, group in placed_df.groupby(placed_df["bet_source"].fillna("unknown").astype(str)):
            placed_by_source.append(f"`{source}` {len(group)}")
    source_text = " | ".join(placed_by_source[:6]) if placed_by_source else "n/a"

    if placed_df.empty and settled_df.empty and clv_df.empty:
        return f"**BEE BAKED DAILY SLIPS**\nDate: {report_date}\nNo slip, settlement, or CLV activity found."

    return (
        f"**BEE BAKED DAILY SLIPS**\n"
        f"Date: {report_date}\n"
        f"Placed Slips: {len(placed_df)}\n"
        f"Settled Record: {wins}-{losses}"
        + (f"-{pushes}" if pushes else "")
        + "\n"
        f"Settled Net Units: {net_units:+.2f}\n"
        f"CLV Updates: {len(clv_df)}\n"
        f"CLV Beaten: {clv_beaten_rate:.1f}%\n"
        f"Avg CLV Edge: {avg_clv:+.2f}%\n"
        f"Placed Sources: {source_text}"
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
