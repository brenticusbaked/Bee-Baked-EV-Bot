"""Market efficiency scoring for syndicate-grade opportunity prioritisation.

Not all +EV edges are equal. An edge found in a tight, liquid market
(low overround, many books posting) is far more reliable than the same
edge in a thin, wide-vig market. This module scores each opportunity
by market quality so the syndicate can prioritise capital deployment.

Efficiency factors:
    1. Overround (vig) — tighter markets = more accurate fair price
    2. Book count — more books posting = better price discovery
    3. Price agreement — lower variance across books = less noise
    4. Edge magnitude relative to vig — edge >> vig = higher confidence
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Sequence

from utils.odds import decimal_implied_probability
from utils.thresholds import env_float


EFFICIENCY_VIG_WEIGHT = env_float("EFFICIENCY_VIG_WEIGHT", 0.35)
EFFICIENCY_BOOK_COUNT_WEIGHT = env_float("EFFICIENCY_BOOK_COUNT_WEIGHT", 0.25)
EFFICIENCY_AGREEMENT_WEIGHT = env_float("EFFICIENCY_AGREEMENT_WEIGHT", 0.25)
EFFICIENCY_EDGE_VIG_RATIO_WEIGHT = env_float("EFFICIENCY_EDGE_VIG_RATIO_WEIGHT", 0.15)
EFFICIENCY_MIN_BOOKS = 3


@dataclass(frozen=True)
class MarketEfficiency:
    overround: float
    book_count: int
    price_std: float
    edge_to_vig_ratio: float
    score: float


def _overround_from_sharp(sharp_prices: Sequence[float]) -> float:
    if len(sharp_prices) < 2:
        return 0.0
    implied = [decimal_implied_probability(p) for p in sharp_prices]
    return max(0.0, sum(implied) - 1.0)


def _price_std(prices: Sequence[float]) -> float:
    if len(prices) < 2:
        return 0.0
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    return sqrt(max(variance, 0.0))


def score_market_efficiency(
    sharp_prices: Sequence[float],
    soft_prices: Sequence[float],
    edge: float,
) -> MarketEfficiency:
    """Score market efficiency on a 0-1 scale (1 = most efficient/reliable)."""
    overround = _overround_from_sharp(sharp_prices)
    book_count = len(soft_prices)
    price_std = _price_std(list(soft_prices) + list(sharp_prices))

    vig_score = max(0.0, 1.0 - (overround / 0.10)) if overround > 0 else 1.0

    book_score = min(1.0, book_count / max(EFFICIENCY_MIN_BOOKS, 1))

    agreement_score = max(0.0, 1.0 - (price_std / 0.15))

    edge_vig_ratio = (edge / overround) if overround > 0.001 else min(edge * 50, 1.0)
    evr_score = min(1.0, edge_vig_ratio / 3.0)

    composite = (
        EFFICIENCY_VIG_WEIGHT * vig_score
        + EFFICIENCY_BOOK_COUNT_WEIGHT * book_score
        + EFFICIENCY_AGREEMENT_WEIGHT * agreement_score
        + EFFICIENCY_EDGE_VIG_RATIO_WEIGHT * evr_score
    )

    return MarketEfficiency(
        overround=round(overround, 6),
        book_count=book_count,
        price_std=round(price_std, 6),
        edge_to_vig_ratio=round(edge_vig_ratio, 4),
        score=round(max(0.0, min(1.0, composite)), 4),
    )
