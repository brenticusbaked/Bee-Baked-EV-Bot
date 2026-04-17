import re
from typing import Dict

import pandas as pd

from db_manager import get_all_bets


def _extract_book(notes: str) -> str:
    if not isinstance(notes, str) or not notes:
        return "unknown"
    match = re.search(r"book=([^;]+)", notes)
    if not match:
        return "unknown"
    return match.group(1).strip()


def get_book_weights(min_sample: int = 5) -> Dict[str, float]:
    bets = get_all_bets()
    if not bets:
        return {}

    df = pd.DataFrame(bets)
    if df.empty:
        return {}

    df["sportsbook"] = df.get("notes", "").apply(_extract_book)
    df["clv_edge_num"] = pd.to_numeric(df.get("clv_edge_pct"), errors="coerce")
    df["is_win"] = df.get("result", "").astype(str).eq("WIN")
    df["is_graded"] = df.get("result", "").astype(str).isin(["WIN", "LOSS", "PUSH"])

    weights: Dict[str, float] = {}
    for book, group in df.groupby("sportsbook", dropna=False):
        if book == "unknown" or len(group) < min_sample:
            continue

        clv_group = group.dropna(subset=["clv_edge_num"])
        graded_group = group[group["is_graded"]]

        clv_score = 0.0
        if not clv_group.empty:
            clv_score = max(min(clv_group["clv_edge_num"].mean() / 2.0, 0.15), -0.15)

        win_score = 0.0
        if not graded_group.empty:
            win_rate = graded_group["is_win"].mean()
            win_score = max(min((win_rate - 0.5) * 0.2, 0.10), -0.10)

        sample_boost = min(len(group) / 50.0, 1.0)
        weight = 1.0 + ((clv_score + win_score) * sample_boost)
        weights[str(book)] = max(0.85, min(weight, 1.20))

    return weights
