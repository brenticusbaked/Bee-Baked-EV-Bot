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

    # --- Daily Calculations ---
    placed_df = df[df.get("date", "").astype(str) == report_date].copy()
    settled_df = df[_local_date_mask(df, "graded_at", report_date)].copy()
    if settled_df.empty:
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
    risked_units = 0.0
    if not settled_df.empty:
        settled_results = settled_df.get("result", "").astype(str).str.upper()
        net_units = sum(
            profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
            for _, row in settled_df.iterrows()
        )
        risked_units = sum(abs(float(row.get("units", 0) or 0.0)) for _, row in settled_df.iterrows())
        settled_df = settled_df.copy()
        settled_df["result"] = settled_results

    roi_pct = (net_units / risked_units * 100.0) if risked_units > 0 else 0.0
    win_pct = ((wins / (wins + losses + pushes)) * 100.0) if (wins + losses + pushes) > 0 else 0.0

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

    # --- Lifetime Calculations ---
    lifetime_settled = df[df.get("result", "").astype(str).str.upper().isin(["WIN", "LOSS", "PUSH"])].copy()
    lifetime_wins = int((lifetime_settled.get("result", "").astype(str).str.upper() == "WIN").sum())
    lifetime_losses = int((lifetime_settled.get("result", "").astype(str).str.upper() == "LOSS").sum())
    lifetime_pushes = int((lifetime_settled.get("result", "").astype(str).str.upper() == "PUSH").sum())
    
    lifetime_net_units = sum(
        profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
        for _, row in lifetime_settled.iterrows()
    )
    lifetime_risked = sum(abs(float(row.get("units", 0) or 0.0)) for _, row in lifetime_settled.iterrows())
    lifetime_roi = (lifetime_net_units / lifetime_risked * 100.0) if lifetime_risked > 0 else 0.0
    
    # Financial translation (Using your $3 standard unit base)
    unit_base = 3.00
    daily_profit_dollars = net_units * unit_base
    lifetime_profit_dollars = lifetime_net_units * unit_base

    if placed_df.empty and settled_df.empty and clv_df.empty:
        return f"**BEE BAKED DAILY SLIPS**\nDate: {report_date}\nNo slip, settlement, or CLV activity found today."

    return (
        f"**BEE BAKED DAILY SLIPS**\n"
        f"Date: {report_date}\n"
        f"**DAILY RECAP**\n"
        f"Placed Slips: {len(placed_df)}\n"
        f"Settled Record: {wins}-{losses}"
        + (f"-{pushes}" if pushes else "")
        + "\n"
        f"Win%: {win_pct:.1f}%\n"
        f"ROI: {roi_pct:+.1f}%\n"
        f"Net Units: {net_units:+.2f}u (${daily_profit_dollars:+.2f})\n"
        f"CLV Updates: {len(clv_df)}\n"
        f"CLV Beaten: {clv_beaten_rate:.1f}%\n"
        f"Avg CLV Edge: {avg_clv:+.2f}%\n"
        f"Placed Sources: {source_text}\n"
        f"--------------------------\n"
        f"**ALL-TIME PERFORMANCE**\n"
        f"Lifetime Record: {lifetime_wins}-{lifetime_losses}"
        + (f"-{lifetime_pushes}" if lifetime_pushes else "")
        + "\n"
        f"Lifetime ROI: {lifetime_roi:+.1f}%\n"
        f"Lifetime Profit: {lifetime_net_units:+.2f}u (${lifetime_profit_dollars:+.2f})\n"
    )


def send_daily_slips_report():
    report = build_daily_slips_report()
    ok = post_discord(
        {"embeds": [{"description": report, "color": 10181046}]},
        webhook_url=DISCORD_DAILY_SLIPS_WEBHOOK_URL,
        add_bee_image=True,
        bee_image_slot="daily_slips",
    )
    return {"detail": "daily slips report complete", "count": 1, "label": "updates", "sent": ok}


if __name__ == "__main__":
    result = send_daily_slips_report()
    report_text = build_daily_slips_report()
    print("--- GENERATED REPORT PREVIEW ---")
    print(report_text)
    print("--------------------------------")
    print(f"Discord send status: {result.get('detail', 'unknown')} | sent={result.get('sent', False)}")