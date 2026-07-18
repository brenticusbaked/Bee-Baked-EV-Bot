import re
from typing import Dict

import pandas as pd

from db_manager import get_all_bets
from services.history_calibration import book_factor_for, history_book_factors
from utils.book_names import normalize_book


def _extract_book(notes: str) -> str:
    if not isinstance(notes, str) or not notes:
        return "unknown"
    match = re.search(r"book=([^;]+)", notes)
    if not match:
        return "unknown"
    return match.group(1).strip()


# Books known for higher liquidity and faster line movement
HIGH_LIQUIDITY_BOOKS = {"fanduel", "draftkings", "betmgm"}
LIQUIDITY_BONUS = 0.03


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

        liquidity_score = LIQUIDITY_BONUS if str(book).lower() in HIGH_LIQUIDITY_BOOKS else 0.0

        sample_boost = min(len(group) / 50.0, 1.0)
        weight = 1.0 + ((clv_score + win_score) * sample_boost) + liquidity_score
        weights[normalize_book(str(book))] = max(0.85, min(weight, 1.20))

    # Overlay realized-ROI factors from the personal betting history so books
    # the user actually beats are favored (and chronic losers are dampened).
    for book, factor in history_book_factors().items():
        base = weights.get(book, 1.0)
        weights[book] = max(0.80, min(base * factor, 1.25))

    return weights


def book_weight_for(weights: Dict[str, float], name: str) -> float:
    """Look up a book weight tolerant of raw titles vs. canonical keys."""
    if name in weights:
        return weights[name]
    canonical = normalize_book(name)
    if canonical in weights:
        return weights[canonical]
    # No graded/history rows for this book yet: still apply any history factor.
    return book_factor_for(name)
