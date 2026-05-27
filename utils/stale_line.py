"""Stale-line detection for syndicate-grade edge confirmation.

A line is "stale" when the sharp market has moved but a soft book has
not adjusted its price. These represent the highest-confidence +EV
opportunities because the edge comes from bookmaker latency rather than
model uncertainty.

Detection works by comparing the implied move in the sharp price against
the soft price's lack of movement, producing a staleness score in [0, 1].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from utils.odds import decimal_implied_probability
from utils.thresholds import env_float


STALE_MOVE_THRESHOLD = env_float("STALE_MOVE_THRESHOLD", 0.015)
STALE_CONFIDENCE_BONUS = env_float("STALE_CONFIDENCE_BONUS", 0.25)


@dataclass(frozen=True)
class StalenessSignal:
    sharp_implied_move: float
    soft_implied_move: float
    staleness_score: float
    is_stale: bool


def _implied_move(old_price: float, new_price: float) -> float:
    old_prob = decimal_implied_probability(old_price)
    new_prob = decimal_implied_probability(new_price)
    if old_prob <= 0:
        return 0.0
    return abs(new_prob - old_prob)


def detect_stale_line(
    sharp_price: float,
    sharp_opening_price: float,
    soft_price: float,
    soft_opening_price: Optional[float] = None,
    threshold: float = 0.0,
) -> StalenessSignal:
    """Score how stale a soft book line is relative to sharp movement.

    Returns a StalenessSignal with score in [0, 1] where 1 = maximally stale.
    """
    move_threshold = threshold if threshold > 0 else STALE_MOVE_THRESHOLD
    sharp_move = _implied_move(sharp_opening_price, sharp_price)
    soft_move = _implied_move(
        soft_opening_price or soft_price,
        soft_price,
    )

    if sharp_move < 0.001:
        return StalenessSignal(
            sharp_implied_move=sharp_move,
            soft_implied_move=soft_move,
            staleness_score=0.0,
            is_stale=False,
        )

    if soft_move >= sharp_move:
        ratio = 0.0
    else:
        ratio = 1.0 - (soft_move / sharp_move)

    score = min(1.0, ratio * (sharp_move / max(move_threshold, 0.001)))
    return StalenessSignal(
        sharp_implied_move=round(sharp_move, 6),
        soft_implied_move=round(soft_move, 6),
        staleness_score=round(min(score, 1.0), 6),
        is_stale=score >= 0.5 and sharp_move >= move_threshold,
    )


def score_opportunities_by_staleness(
    opportunities: Sequence[dict],
    sharp_prices: Dict[str, float],
    sharp_opening_prices: Dict[str, float],
) -> list[dict]:
    """Enrich a list of opportunity dicts with staleness metadata.

    Each opportunity dict is expected to have at minimum:
        - outcome_key: tuple identifying the outcome
        - price: the soft book's current decimal price
        - opening_price (optional): the soft book's opening price

    Returns a new list with added 'staleness' key containing the signal.
    """
    enriched = []
    for opp in opportunities:
        opp_copy = dict(opp)
        outcome_key = opp.get("outcome_key")
        if outcome_key is None:
            opp_copy["staleness"] = None
            enriched.append(opp_copy)
            continue

        key_str = str(outcome_key)
        sharp_current = sharp_prices.get(key_str)
        sharp_opening = sharp_opening_prices.get(key_str)
        if sharp_current is None or sharp_opening is None:
            opp_copy["staleness"] = None
            enriched.append(opp_copy)
            continue

        signal = detect_stale_line(
            sharp_price=sharp_current,
            sharp_opening_price=sharp_opening,
            soft_price=opp["price"],
            soft_opening_price=opp.get("opening_price"),
        )
        opp_copy["staleness"] = signal
        enriched.append(opp_copy)

    return enriched


def staleness_edge_bonus(staleness: Optional[StalenessSignal]) -> float:
    """Return an additive edge bonus for confirmed stale lines."""
    if staleness is None or not staleness.is_stale:
        return 0.0
    return staleness.staleness_score * STALE_CONFIDENCE_BONUS
