"""Turn the personal betting-history summary into decision-engine overlays.

Consumes the aggregate produced by ``bet_history.summarize`` and exposes three
neutral-by-default overlays:

  * ``history_book_factors``   -> per-book multiplicative weight (shrunk ROI)
  * ``validated_ev_floor``     -> the lowest EV bucket that was actually profitable
  * ``history_clv_baseline``   -> realized avg CLV per book

Every function returns an empty/neutral value when no summary is available, so
the engine behaves exactly as before until history is loaded.
"""

import os
from functools import lru_cache
from typing import Dict, Optional

from bet_history import load_summary
from utils.book_names import normalize_book

# Shrinkage constant: a book needs ~SHRINK_K settled bets before its realized
# ROI is trusted at ~half strength. Keeps small samples from swinging weights.
SHRINK_K = int(os.getenv("HISTORY_SHRINK_K", "150"))
MIN_BOOK_SAMPLE = int(os.getenv("HISTORY_MIN_BOOK_SAMPLE", "50"))
# Cap how far realized ROI can move a book weight (multiplicative).
MAX_BOOK_ADJUST = float(os.getenv("HISTORY_MAX_BOOK_ADJUST", "0.10"))
# Scales ROI (fraction) into a weight delta before clamping.
ROI_TO_WEIGHT_GAIN = float(os.getenv("HISTORY_ROI_WEIGHT_GAIN", "1.0"))
# An EV bucket must clear this ROI and sample to "validate" as profitable.
EV_FLOOR_MIN_ROI = float(os.getenv("HISTORY_EV_FLOOR_MIN_ROI", "0.0"))
EV_FLOOR_MIN_SAMPLE = int(os.getenv("HISTORY_EV_FLOOR_MIN_SAMPLE", "200"))

# Lower EV bound (fraction) implied by each bucket label.
_BUCKET_FLOOR = {"neg": None, "0-2%": 0.0, "2-5%": 0.02, "5-10%": 0.05, "10%+": 0.10}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


@lru_cache(maxsize=1)
def history_book_factors() -> Dict[str, float]:
    """Canonical-book -> multiplicative weight from shrunk realized ROI."""
    summary = load_summary()
    if not summary:
        return {}
    factors: Dict[str, float] = {}
    for book, stats in summary.get("by_book", {}).items():
        n = int(stats.get("n", 0))
        if n < MIN_BOOK_SAMPLE:
            continue
        roi = float(stats.get("roi", 0.0))
        shrunk = roi * (n / (n + SHRINK_K))
        delta = _clamp(shrunk * ROI_TO_WEIGHT_GAIN, -MAX_BOOK_ADJUST, MAX_BOOK_ADJUST)
        factors[book] = round(1.0 + delta, 4)
    return factors


@lru_cache(maxsize=1)
def validated_ev_floor() -> Optional[float]:
    """Lowest EV bound whose realized ROI was profitable on a decent sample.

    Returns ``None`` when history is absent so callers keep their own default.
    """
    summary = load_summary()
    if not summary:
        return None
    buckets = summary.get("ev_buckets", {})
    best_floor: Optional[float] = None
    for label, stats in buckets.items():
        floor = _BUCKET_FLOOR.get(label)
        if floor is None:
            continue
        if int(stats.get("n", 0)) < EV_FLOOR_MIN_SAMPLE:
            continue
        if float(stats.get("roi", 0.0)) < EV_FLOOR_MIN_ROI:
            continue
        if best_floor is None or floor < best_floor:
            best_floor = floor
    return best_floor


@lru_cache(maxsize=1)
def history_clv_baseline() -> Dict[str, float]:
    """Canonical-book -> realized average CLV percent."""
    summary = load_summary()
    if not summary:
        return {}
    return {
        book: float(stats.get("avg_clv_pct", 0.0))
        for book, stats in summary.get("clv_by_book", {}).items()
    }


def book_factor_for(name: str) -> float:
    """History weight for a raw book title/key (1.0 when unknown)."""
    return history_book_factors().get(normalize_book(name), 1.0)


def clv_baseline_for(name: str) -> Optional[float]:
    """Realized avg CLV percent for a raw book title/key, or None."""
    return history_clv_baseline().get(normalize_book(name))


def reset_cache() -> None:
    """Clear memoized overlays (used after reloading the summary / in tests)."""
    history_book_factors.cache_clear()
    validated_ev_floor.cache_clear()
    history_clv_baseline.cache_clear()
