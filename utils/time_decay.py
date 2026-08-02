"""Time-decay edge adjustments for syndicate-grade timing optimisation.

Lines become more efficient as game time approaches because:
    1. Sharp money converges toward true prices
    2. Late-breaking information (injuries, weather, lineups) gets priced in
    3. Market liquidity increases, reducing noise

This module adjusts the EV threshold based on time-to-event so that:
    - Early lines (>6h out): standard threshold (lines may still be soft)
    - Mid-range (2-6h): slightly tighter (most value already captured)
    - Close to tip (30m-2h): loosened threshold (stale lines are very high confidence)
    - Past tip: reject (handled by scratch_guard, but backstop here)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from utils.scratch_guard import safe_parse_commence_time
from utils.thresholds import env_float


DECAY_EARLY_HOURS = env_float("DECAY_EARLY_HOURS", 6.0)
DECAY_CLOSE_HOURS = env_float("DECAY_CLOSE_HOURS", 2.0)
DECAY_LOCKOUT_MINUTES = env_float("DECAY_LOCKOUT_MINUTES", 5.0)

DECAY_EARLY_MULTIPLIER = env_float("DECAY_EARLY_MULTIPLIER", 1.0)
DECAY_MID_MULTIPLIER = env_float("DECAY_MID_MULTIPLIER", 1.15)
DECAY_CLOSE_MULTIPLIER = env_float("DECAY_CLOSE_MULTIPLIER", 0.85)


@dataclass(frozen=True)
class TimeDecayContext:
    hours_to_event: float
    threshold_multiplier: float
    phase: str


def compute_time_decay(
    commence_time: Optional[str],
    now: Optional[datetime] = None,
) -> TimeDecayContext:
    """Compute threshold multiplier based on time until event start.

    Returns a multiplier applied to the base EV threshold:
        >1.0 = tighter (require more edge)
        <1.0 = looser (accept smaller edge — higher confidence window)
        0.0  = lockout (too close to or past start)
    """
    if not commence_time:
        return TimeDecayContext(
            hours_to_event=0.0,
            threshold_multiplier=1.0,
            phase="unknown",
        )

    parsed = safe_parse_commence_time(str(commence_time))
    if parsed is None:
        return TimeDecayContext(
            hours_to_event=0.0,
            threshold_multiplier=1.0,
            phase="unknown",
        )

    current = now or datetime.now(timezone.utc)
    delta = parsed - current
    hours = delta.total_seconds() / 3600.0

    if hours <= DECAY_CLOSE_HOURS:
        return TimeDecayContext(
            hours_to_event=round(hours, 4),
            threshold_multiplier=DECAY_CLOSE_MULTIPLIER,
            phase="close",
        )

    if hours <= DECAY_EARLY_HOURS:
        return TimeDecayContext(
            hours_to_event=round(hours, 4),
            threshold_multiplier=DECAY_MID_MULTIPLIER,
            phase="mid",
        )

    return TimeDecayContext(
        hours_to_event=round(hours, 4),
        threshold_multiplier=DECAY_EARLY_MULTIPLIER,
        phase="early",
    )


def adjusted_threshold(base_threshold: float, decay: TimeDecayContext) -> float:
    """Apply time-decay multiplier to a base EV threshold."""
    if decay.threshold_multiplier <= 0:
        return float("inf")
    return base_threshold * decay.threshold_multiplier
