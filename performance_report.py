import re
from typing import Dict

import pandas as pd

from db_manager import get_all_bets
from services.discord_channels import RESULTS_WEBHOOK_URL
from services.http_client import post_discord


DISCORD_STATUS_WEBHOOK_URL = RESULTS_WEBHOOK_URL


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


def _compute_roi(graded: pd.DataFrame) -> float:
    if graded.empty or "units" not in graded.columns:
        return 0.0
    from utils.odds import profit_for_result

    net = sum(
        profit_for_result(row.get("odds", 0), row.get("units", 0), row.get("result", ""))
        for _, row in graded.iterrows()
    )
    risked = sum(abs(float(row.get("units", 0))) for _, row in graded.iterrows())
    return (net / risked * 100.0) if risked > 0 else 0.0


def _markdown_table(headers: list, rows: list) -> str:
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    sep_line = "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"
    body_lines = [
        "| " + " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([header_line, sep_line] + body_lines)


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

    overall_graded = df[df["is_graded"]]
    overall_clv = df.dropna(subset=["clv_edge_num"])
    overall_roi = _compute_roi(overall_graded)
    overall_net = _compute_stats(df)["net_units"]
    overall_clv_beaten = (overall_clv["beat_clv"].mean() * 100.0) if not overall_clv.empty else 0.0
    avg_clv = overall_clv["clv_edge_num"].mean() if not overall_clv.empty else 0.0
    win_pct = (overall_graded["is_win"].mean() * 100.0) if not overall_graded.empty else 0.0

    summary = (
        f"**BEE BAKED PERFORMANCE REPORT**\n"
        f"Total Bets: {len(df)} | Graded: {len(overall_graded)}\n"
        f"Record: {int(overall_graded['is_win'].sum()) if not overall_graded.empty else 0}W-"
        f"{int((overall_graded['result'].astype(str) == 'LOSS').sum()) if not overall_graded.empty else 0}L\n"
        f"Win%: {win_pct:.1f} | ROI: **{overall_roi:+.1f}%** | Net: **{overall_net:+.2f}u**\n"
        f"CLV Beaten: **{overall_clv_beaten:.1f}%** | Avg CLV Edge: {avg_clv:+.2f}%\n"
    )

    bucket_rows = []
    grouped = df.groupby("ev_bucket", dropna=False)
    for label in BUCKET_LABELS:
        group = grouped.get_group(label) if label in grouped.groups else pd.DataFrame()
        if group.empty:
            continue
        stats = _compute_stats(group)
        graded_group = group[group["is_graded"]]
        roi = _compute_roi(graded_group)
        bucket_rows.append([
            label,
            str(int(stats["bets"])),
            f"{stats['win_rate']:.1f}",
            f"{roi:+.1f}",
            f"{stats['clv_beaten_rate']:.1f}",
            f"{stats['avg_clv_edge']:+.2f}",
            f"{stats['net_units']:+.2f}",
        ])

    bucket_table = _markdown_table(
        ["Bucket", "Bets", "Win%", "ROI%", "CLV%", "AvgCLV", "Units"],
        bucket_rows,
    )

    book_rows = []
    for book, group in df.groupby("sportsbook", dropna=False):
        if group.empty or book == "unknown":
            continue
        stats = _compute_stats(group)
        graded_group = group[group["is_graded"]]
        roi = _compute_roi(graded_group)
        book_rows.append((stats["bets"], [
            str(book),
            str(int(stats["bets"])),
            f"{stats['win_rate']:.1f}",
            f"{roi:+.1f}",
            f"{stats['clv_beaten_rate']:.1f}",
            f"{stats['net_units']:+.2f}",
        ]))
    book_rows = [row for _, row in sorted(book_rows, key=lambda item: item[0], reverse=True)[:8]]

    book_table = _markdown_table(
        ["Book", "Bets", "Win%", "ROI%", "CLV%", "Units"],
        book_rows,
    ) if book_rows else ""

    source_rows = []
    for source, group in df.groupby("bet_source", dropna=False):
        if group.empty:
            continue
        stats = _compute_stats(group)
        graded_group = group[group["is_graded"]]
        roi = _compute_roi(graded_group)
        source_rows.append((stats["bets"], [
            str(source),
            str(int(stats["bets"])),
            f"{stats['win_rate']:.1f}",
            f"{roi:+.1f}",
            f"{stats['clv_beaten_rate']:.1f}",
            f"{stats['net_units']:+.2f}",
        ]))
    source_rows = [row for _, row in sorted(source_rows, key=lambda item: item[0], reverse=True)]

    source_table = _markdown_table(
        ["Source", "Bets", "Win%", "ROI%", "CLV%", "Units"],
        source_rows,
    ) if source_rows else ""

    sections = [summary, "**EV Buckets**\n" + bucket_table]
    if book_table:
        sections.append("**By Book**\n" + book_table)
    if source_table:
        sections.append("**By Source**\n" + source_table)

    return "\n\n".join(sections)


def send_performance_report() -> str:
    report = build_performance_report()
    post_discord(
        {"embeds": [{"description": report, "color": 10181046}]},
        webhook_url=DISCORD_STATUS_WEBHOOK_URL,
    )
    return report


if __name__ == "__main__":
    send_performance_report()
