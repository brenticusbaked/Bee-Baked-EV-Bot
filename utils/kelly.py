"""Dynamic Quarter-Kelly sizing with bankroll awareness and daily exposure caps."""

from typing import List

from utils.odds import profit_for_result
from utils.thresholds import env_float


BANKROLL_UNITS = env_float("BANKROLL_UNITS", 100.0)
DAILY_EXPOSURE_CAP = env_float("DAILY_EXPOSURE_CAP", 15.0)
KELLY_FRACTION = env_float("KELLY_FRACTION", 0.25)
KELLY_MAX_UNIT_CAP = env_float("KELLY_MAX_UNIT_CAP", 5.0)
KELLY_DRAWDOWN_SCALE_THRESHOLD = env_float("KELLY_DRAWDOWN_SCALE_THRESHOLD", 0.15)


def _current_bankroll(graded_bets: List[dict]) -> float:
    net = sum(
        profit_for_result(bet.get("odds", 0), bet.get("units", 0), bet.get("result", ""))
        for bet in graded_bets
    )
    return BANKROLL_UNITS + net


def _drawdown_factor(bankroll: float) -> float:
    """Scale sizing down when bankroll drops below the drawdown threshold."""
    if bankroll <= 0:
        return 0.0
    ratio = bankroll / BANKROLL_UNITS
    if ratio >= (1.0 - KELLY_DRAWDOWN_SCALE_THRESHOLD):
        return 1.0
    return max(ratio, 0.1)


def _daily_remaining(today_bets: List[dict]) -> float:
    used = sum(abs(float(bet.get("units", 0))) for bet in today_bets)
    return max(DAILY_EXPOSURE_CAP - used, 0.0)


def dynamic_kelly_units(
    edge: float,
    offered_decimal: float,
    graded_bets: List[dict],
    today_bets: List[dict],
    cap: float = 0.0,
) -> float:
    """Bankroll-aware Kelly sizing with daily exposure cap and drawdown scaling.

    Returns suggested units clamped to the daily remaining exposure and adjusted
    for bankroll drawdown.
    """
    if offered_decimal <= 1.0 or edge <= 0:
        return 0.0

    max_cap = cap if cap > 0 else KELLY_MAX_UNIT_CAP
    kelly_pct = (edge / (offered_decimal - 1.0)) * KELLY_FRACTION
    bankroll = _current_bankroll(graded_bets)
    raw_units = kelly_pct * bankroll
    scaled = raw_units * _drawdown_factor(bankroll)
    remaining = _daily_remaining(today_bets)
    return max(0.0, min(scaled, max_cap, remaining))
