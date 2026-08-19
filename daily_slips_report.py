from datetime import timedelta

import pandas as pd

from db_manager import get_all_bets
from services.discord_channels import DAILY_SLIPS_WEBHOOK_URL, STATUS_WEBHOOK_URL
from services.http_client import post_discord
from utils.odds import profit_for_result
from utils.results import (
    GRADED_RESULTS,
    LOSS,
    PUSH,
    WIN,
    book_from_notes,
    normalize_result,
)
from utils.time import DEFAULT_TZ, get_local_now

DISCORD_DAILY_SLIPS_WEBHOOK_URL = DAILY_SLIPS_WEBHOOK_URL


def _column(df: pd.DataFrame, name: str, default="") -> pd.Series:
    """The named column, or a filled series when the table predates it."""
    if name in df.columns:
        return df[name]
    return pd.Series(default, index=df.index, dtype="object")


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


def _book_record_lines(settled: pd.DataFrame, limit: int = 8) -> list:
    """Per-book W-L and units for an already-settled frame.

    The book lives in ``notes`` as ``book=``/``book_key=``; rows without one
    (hand-entered or imported without a sportsbook) group under ``unknown``.
    """
    if settled.empty:
        return []
    frame = settled.copy()
    frame["book"] = _column(frame, "notes").apply(book_from_notes)
    rows = []
    for book, group in frame.groupby("book", dropna=False):
        wins = int((group["result"] == WIN).sum())
        losses = int((group["result"] == LOSS).sum())
        pushes = int((group["result"] == PUSH).sum())
        net = sum(
            profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
            for _, row in group.iterrows()
        )
        record = f"{wins}-{losses}" + (f"-{pushes}" if pushes else "")
        rows.append((len(group), f"`{book}` {record} ({net:+.2f}u)"))
    return [line for _, line in sorted(rows, key=lambda item: item[0], reverse=True)[:limit]]


def build_daily_slips_report() -> str:
    all_bets = get_all_bets()
    if not all_bets:
        return "**BEE BAKED DAILY SLIPS**\nNo bets logged yet."

    df = pd.DataFrame(all_bets)
    if df.empty:
        return "**BEE BAKED DAILY SLIPS**\nNo bets logged yet."

    # One vocabulary for results: the CSV import wrote lower case, the graders
    # write upper case, and the comparisons below only ever matched the latter.
    df["result"] = _column(df, "result").apply(normalize_result)

    report_date = (get_local_now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # --- Daily Calculations ---
    placed_df = df[df.get("date", "").astype(str) == report_date].copy()
    settled_df = df[_local_date_mask(df, "graded_at", report_date)].copy()
    if settled_df.empty:
        settled_df = df[
            df.get("date", "").astype(str).eq(report_date) & df["result"].isin(GRADED_RESULTS)
        ].copy()
    else:
        settled_df = settled_df[settled_df["result"].isin(GRADED_RESULTS)].copy()

    clv_df = df[_local_date_mask(df, "clv_tracked_at", report_date)].copy()
    if clv_df.empty:
        clv_df = df[
            df.get("date", "").astype(str).eq(report_date)
            & _safe_numeric(df.get("clv_edge_pct")).notna()
        ].copy()

    wins = int((settled_df["result"] == WIN).sum()) if not settled_df.empty else 0
    losses = int((settled_df["result"] == LOSS).sum()) if not settled_df.empty else 0
    pushes = int((settled_df["result"] == PUSH).sum()) if not settled_df.empty else 0

    net_units = 0.0
    risked_units = 0.0
    if not settled_df.empty:
        net_units = sum(
            profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
            for _, row in settled_df.iterrows()
        )
        risked_units = sum(abs(float(row.get("units", 0) or 0.0)) for _, row in settled_df.iterrows())

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
    lifetime_settled = df[df["result"].isin(GRADED_RESULTS)].copy()
    lifetime_wins = int((lifetime_settled["result"] == WIN).sum())
    lifetime_losses = int((lifetime_settled["result"] == LOSS).sum())
    lifetime_pushes = int((lifetime_settled["result"] == PUSH).sum())

    daily_book_lines = _book_record_lines(settled_df)
    lifetime_book_lines = _book_record_lines(lifetime_settled)

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
        + ("By Book: " + " | ".join(daily_book_lines) + "\n" if daily_book_lines else "")
        + f"--------------------------\n"
        f"**ALL-TIME PERFORMANCE**\n"
        f"Lifetime Record: {lifetime_wins}-{lifetime_losses}"
        + (f"-{lifetime_pushes}" if lifetime_pushes else "")
        + "\n"
        f"Lifetime ROI: {lifetime_roi:+.1f}%\n"
        f"Lifetime Profit: {lifetime_net_units:+.2f}u (${lifetime_profit_dollars:+.2f})\n"
        + ("Lifetime By Book: " + " | ".join(lifetime_book_lines) + "\n" if lifetime_book_lines else "")
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


def build_overall_status_summary() -> str:
    """Compact lifetime win/loss/ROI summary for the moderator/status channel."""
    all_bets = get_all_bets()
    if not all_bets:
        return "**BEE BAKED OVERALL RECORD**\nNo bets logged yet."

    df = pd.DataFrame(all_bets)
    df["result"] = _column(df, "result").apply(normalize_result)
    lifetime_settled = df[df["result"].isin(GRADED_RESULTS)].copy()
    lifetime_wins = int((lifetime_settled["result"] == WIN).sum())
    lifetime_losses = int((lifetime_settled["result"] == LOSS).sum())
    lifetime_pushes = int((lifetime_settled["result"] == PUSH).sum())

    lifetime_net_units = sum(
        profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
        for _, row in lifetime_settled.iterrows()
    )
    lifetime_risked = sum(abs(float(row.get("units", 0) or 0.0)) for _, row in lifetime_settled.iterrows())
    lifetime_roi = (lifetime_net_units / lifetime_risked * 100.0) if lifetime_risked > 0 else 0.0
    win_pct = (
        (lifetime_wins / (lifetime_wins + lifetime_losses + lifetime_pushes)) * 100.0
        if (lifetime_wins + lifetime_losses + lifetime_pushes) > 0
        else 0.0
    )
    unit_base = 3.00
    lifetime_profit_dollars = lifetime_net_units * unit_base

    record = f"{lifetime_wins}-{lifetime_losses}"
    if lifetime_pushes:
        record += f"-{lifetime_pushes}"

    book_lines = _book_record_lines(lifetime_settled)
    return (
        f"**BEE BAKED OVERALL RECORD**\n"
        f"Record: {record}\n"
        f"Win%: {win_pct:.1f}%\n"
        f"ROI: {lifetime_roi:+.1f}%\n"
        f"Net: {lifetime_net_units:+.2f}u (${lifetime_profit_dollars:+.2f})"
        + ("\nBy Book: " + " | ".join(book_lines) if book_lines else "")
    )


def send_status_win_loss_report():
    report = build_overall_status_summary()
    ok = post_discord(
        {"embeds": [{"description": report, "color": 10181046}]},
        webhook_url=STATUS_WEBHOOK_URL,
        add_bee_image=False,
    )
    return {"detail": "overall win/loss/roi status posted", "count": 1, "label": "updates", "sent": ok}


if __name__ == "__main__":
    result = send_daily_slips_report()
    report_text = build_daily_slips_report()
    print("--- GENERATED REPORT PREVIEW ---")
    print(report_text)
    print("--------------------------------")
    print(f"Discord send status: {result.get('detail', 'unknown')} | sent={result.get('sent', False)}")

    status_result = send_status_win_loss_report()
    print(f"Status channel: {status_result.get('detail', 'unknown')} | sent={status_result.get('sent', False)}")