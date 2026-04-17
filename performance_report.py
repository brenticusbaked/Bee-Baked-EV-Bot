import os
import re
from typing import Dict

import pandas as pd

from db_manager import get_all_bets
from services.http_client import post_discord


DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")


BUCKET_LABELS = [
    "0-1%",
    "1-2%",
    "2-3%",
    "3-5%",
    "5%+",
]


def _bucket_edge(value: float) -> str:
    if value < 1.0:
        return "0-1%"
    if value < 2.0:
        return "1-2%"
    if value < 3.0:
        return "2-3%"
    if value < 5.0:
        return "3-5%"
    return "5%+"


def _format_bucket_row(label: str, stats: Dict[str, float]) -> str:
    return (
        f"`{label}` bets={int(stats['bets'])} "
        f"win%={stats['win_rate']:.1f} "
        f"clv%={stats['clv_beaten_rate']:.1f} "
        f"avg_clv={stats['avg_clv_edge']:+.2f}% "
        f"units={stats['net_units']:+.2f}"
    )


def _compute_stats(group: pd.DataFrame) -> Dict[str, float]:
    graded = group[group["is_graded"]]
    clv_group = group.dropna(subset=["clv_edge_num"])
    stats = {
        "bets": len(group),
        "win_rate": (graded["is_win"].mean() * 100.0) if not graded.empty else 0.0,
        "clv_beaten_rate": (clv_group["beat_clv"].mean() * 100.0) if not clv_group.empty else 0.0,
        "avg_clv_edge": clv_group["clv_edge_num"].mean() if not clv_group.empty else 0.0,
        "net_units": 0.0,
    }
    if "units" in graded.columns and "result" in graded.columns and "odds" in graded.columns:
        from utils.odds import profit_for_result

        stats["net_units"] = sum(
            profit_for_result(row["odds"], row.get("units", 0), row.get("result", ""))
            for _, row in graded.iterrows()
        )
    return stats


def _format_source_row(label: str, stats: Dict[str, float]) -> str:
    return (
        f"`{label}` bets={int(stats['bets'])} "
        f"win%={stats['win_rate']:.1f} "
        f"clv%={stats['clv_beaten_rate']:.1f} "
        f"avg_clv={stats['avg_clv_edge']:+.2f}% "
        f"units={stats['net_units']:+.2f}"
    )


def _extract_book(notes: str) -> str:
    if not isinstance(notes, str) or not notes:
        return "unknown"
    match = re.search(r"book=([^;]+)", notes)
    if not match:
        return "unknown"
    return match.group(1).strip()


def build_performance_report() -> str:
    bets = get_all_bets()
    if not bets:
        return "**EV Bucket Report**\nNo bets found."

    df = pd.DataFrame(bets)
    if df.empty:
        return "**EV Bucket Report**\nNo bets found."

    df["edge_pct_num"] = pd.to_numeric(df.get("edge_pct"), errors="coerce")
    if "edge_pct_num" not in df or df["edge_pct_num"].isna().all():
        df["edge_pct_num"] = pd.to_numeric(df["edge"].astype(str).str.replace("%", ""), errors="coerce")
    df = df.dropna(subset=["edge_pct_num"]).copy()
    if df.empty:
        return "**EV Bucket Report**\nNo bets with usable EV values found."

    df["ev_bucket"] = df["edge_pct_num"].apply(_bucket_edge)
    df["is_win"] = df.get("result", "").astype(str).eq("WIN")
    df["is_graded"] = df.get("result", "").astype(str).isin(["WIN", "LOSS", "PUSH"])
    df["beat_clv"] = pd.to_numeric(df.get("odds_decimal"), errors="coerce") > pd.to_numeric(df.get("closing_line_decimal"), errors="coerce")
    df["clv_edge_num"] = pd.to_numeric(df.get("clv_edge_pct"), errors="coerce")
    df["bet_source"] = df.get("bet_source", "unknown").fillna("unknown").astype(str)
    df["sportsbook"] = df.get("notes", "").apply(_extract_book)

    bucket_lines = []
    grouped = df.groupby("ev_bucket", dropna=False)
    for label in BUCKET_LABELS:
        group = grouped.get_group(label) if label in grouped.groups else pd.DataFrame()
        if group.empty:
            continue
        stats = _compute_stats(group)
        bucket_lines.append(_format_bucket_row(label, stats))

    source_lines = []
    for source, group in df.groupby("bet_source", dropna=False):
        if group.empty:
            continue
        stats = _compute_stats(group)
        source_lines.append((stats["bets"], _format_source_row(source, stats)))
    source_lines = [line for _, line in sorted(source_lines, key=lambda item: item[0], reverse=True)]

    book_lines = []
    for book, group in df.groupby("sportsbook", dropna=False):
        if group.empty or book == "unknown":
            continue
        stats = _compute_stats(group)
        book_lines.append((stats["bets"], _format_source_row(book, stats)))
    book_lines = [line for _, line in sorted(book_lines, key=lambda item: item[0], reverse=True)[:8]]

    overall_graded = df[df["is_graded"]]
    overall_clv = df.dropna(subset=["clv_edge_num"])
    overall_text = (
        f"Overall bets={len(df)} "
        f"win%={(overall_graded['is_win'].mean() * 100.0) if not overall_graded.empty else 0.0:.1f} "
        f"clv%={(overall_clv['beat_clv'].mean() * 100.0) if not overall_clv.empty else 0.0:.1f} "
        f"avg_clv={(overall_clv['clv_edge_num'].mean()) if not overall_clv.empty else 0.0:+.2f}%"
    )

    return (
        "**EV Bucket Report**\n"
        f"{overall_text}\n\n"
        "**Buckets**\n"
        + "\n".join(bucket_lines)
        + "\n\n**Sources**\n"
        + "\n".join(source_lines)
        + ("\n\n**Books**\n" + "\n".join(book_lines) if book_lines else "")
    )


def send_performance_report() -> str:
    report = build_performance_report()
    post_discord(
        {"embeds": [{"description": report, "color": 10181046}]},
        webhook_url=DISCORD_STATUS_WEBHOOK_URL,
    )
    return report


if __name__ == "__main__":
    send_performance_report()
