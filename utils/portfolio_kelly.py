"""Simultaneous Kelly sizing for a portfolio of correlated bets.

Standard single-bet Kelly ignores that a syndicate places many bets
concurrently. Simultaneous Kelly accounts for:
    1. Total bankroll allocation across all open positions
    2. Correlation between bets (same-game, same-team)
    3. Diminishing marginal value of additional exposure

This implementation uses a simplified analytical approach that scales
individual Kelly fractions by portfolio concentration, rather than
requiring a full covariance matrix optimisation (which is impractical
at scan-time speeds).
"""

from __future__ import annotations

from typing import List

from utils.thresholds import env_float


PORTFOLIO_KELLY_FRACTION = env_float("PORTFOLIO_KELLY_FRACTION", 0.25)
PORTFOLIO_MAX_TOTAL_EXPOSURE = env_float("PORTFOLIO_MAX_TOTAL_EXPOSURE", 20.0)
PORTFOLIO_CONCENTRATION_PENALTY = env_float("PORTFOLIO_CONCENTRATION_PENALTY", 0.10)
PORTFOLIO_DIMINISHING_FACTOR = env_float("PORTFOLIO_DIMINISHING_FACTOR", 0.85)


def single_kelly(edge: float, odds_decimal: float) -> float:
    """Raw Kelly fraction for a single bet."""
    if edge <= 0 or odds_decimal <= 1.0:
        return 0.0
    b = odds_decimal - 1.0
    p = edge / b + 1.0 / odds_decimal
    q = 1.0 - p
    if b <= 0 or q <= 0:
        return 0.0
    return max(0.0, (b * p - q) / b)


def simultaneous_kelly_units(
    edges: List[float],
    odds: List[float],
    bankroll: float,
    existing_exposure: float = 0.0,
    kelly_fraction: float = 0.0,
    max_total: float = 0.0,
) -> List[float]:
    """Compute portfolio-adjusted Kelly units for a batch of simultaneous bets.

    Args:
        edges: List of edge values for each bet.
        odds: List of decimal odds for each bet.
        bankroll: Current bankroll in units.
        existing_exposure: Units already committed to open positions.
        kelly_fraction: Override for Kelly fraction (default from env).
        max_total: Override for max total exposure (default from env).

    Returns:
        List of suggested unit sizes, one per bet, in the same order.
    """
    frac = kelly_fraction if kelly_fraction > 0 else PORTFOLIO_KELLY_FRACTION
    max_exp = max_total if max_total > 0 else PORTFOLIO_MAX_TOTAL_EXPOSURE

    if bankroll <= 0 or not edges:
        return [0.0] * len(edges)

    raw_fractions = [single_kelly(e, o) for e, o in zip(edges, odds)]
    raw_units = [f * bankroll * frac for f in raw_fractions]

    n_bets = len([u for u in raw_units if u > 0])
    if n_bets <= 1:
        remaining = max(0.0, max_exp - existing_exposure)
        return [min(u, remaining) for u in raw_units]

    concentration_scale = PORTFOLIO_DIMINISHING_FACTOR ** (n_bets - 1)
    penalty = 1.0 - (PORTFOLIO_CONCENTRATION_PENALTY * (n_bets - 1))
    portfolio_scale = max(0.1, min(1.0, concentration_scale * penalty))

    adjusted = [u * portfolio_scale for u in raw_units]

    remaining = max(0.0, max_exp - existing_exposure)

    if sum(adjusted) > remaining and sum(adjusted) > 0:
        scale_down = remaining / sum(adjusted)
        adjusted = [u * scale_down for u in adjusted]

    return [round(max(0.0, u), 4) for u in adjusted]
