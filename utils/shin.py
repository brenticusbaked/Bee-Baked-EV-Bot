"""Shin's method for removing bookmaker overround.

Standard multiplicative vig removal assumes equal overround across all
outcomes. Shin's model recognises that bookmakers load more vig onto
longshots (where informed-trader adverse-selection risk is higher),
producing significantly more accurate fair probabilities for
syndicate-grade pricing.

Reference:
    Shin, H. S. (1993). "Measuring the Incidence of Insider Trading
    in a Market for State-Contingent Claims", The Economic Journal.
"""

from __future__ import annotations

from typing import Dict, Sequence, Tuple


def _shin_z(implied: Sequence[float], tol: float = 1e-12, max_iter: int = 200) -> float:
    """Solve for Shin's z parameter via Newton-Raphson.

    z represents the proportion of informed traders in the market.
    With z = 0 the model collapses to simple multiplicative scaling.
    """
    n = len(implied)
    if n < 2:
        return 0.0

    overround = sum(implied)
    if overround <= 1.0:
        return 0.0

    z = 0.01
    for _ in range(max_iter):
        numerator = 0.0
        denominator = 0.0
        for p in implied:
            disc = max((z * z) + (4.0 * (1.0 - z) * (p * p / overround)), 0.0)
            sqrt_disc = disc ** 0.5
            fair = ((z + sqrt_disc) / (2.0 * (1.0 - z)))
            numerator += fair
            if sqrt_disc > 0:
                denominator += p * p / (overround * sqrt_disc)

        f_val = numerator - 1.0
        f_prime = denominator
        if abs(f_prime) < tol:
            break
        z_new = z - f_val / f_prime
        z_new = max(1e-10, min(z_new, 0.99))
        if abs(z_new - z) < tol:
            z = z_new
            break
        z = z_new

    return z


def shin_probabilities(implied: Sequence[float]) -> list[float]:
    """Convert implied probabilities (with vig) to Shin-adjusted fair probabilities."""
    n = len(implied)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    overround = sum(implied)
    if overround <= 1.0:
        return list(implied)

    z = _shin_z(implied)
    if z <= 0:
        return [p / overround for p in implied]

    fair = []
    for p in implied:
        disc = max((z * z) + (4.0 * (1.0 - z) * (p * p / overround)), 0.0)
        sqrt_disc = disc ** 0.5
        fair_p = (z + sqrt_disc) / (2.0 * (1.0 - z))
        fair.append(fair_p)

    total = sum(fair)
    if total > 0 and abs(total - 1.0) > 1e-8:
        fair = [p / total for p in fair]

    return fair


def shin_fair_probabilities_from_prices(prices_by_outcome: dict) -> dict:
    """Drop-in replacement for ``fair_probabilities_from_prices`` using Shin's method.

    Groups outcomes by point-line (so Over/Under at 7.5 are devigged together),
    then applies Shin's method to each group.
    """
    grouped: Dict[str, list[Tuple[object, float]]] = {}
    for outcome_key, price in prices_by_outcome.items():
        point = outcome_key[1] if isinstance(outcome_key, tuple) and len(outcome_key) > 1 else ""
        try:
            point = str(abs(float(point))) if point not in {"", None} else point
        except (TypeError, ValueError):
            pass
        grouped.setdefault(point, []).append((outcome_key, price))

    fair_probabilities: dict = {}
    for outcomes in grouped.values():
        if len(outcomes) < 2:
            for outcome_key, price in outcomes:
                from utils.odds import decimal_implied_probability
                fair_probabilities[outcome_key] = decimal_implied_probability(price)
            continue

        implied = []
        keys = []
        for outcome_key, price in outcomes:
            from utils.odds import decimal_implied_probability
            imp = decimal_implied_probability(price)
            implied.append(imp)
            keys.append(outcome_key)

        fair = shin_probabilities(implied)
        for key, prob in zip(keys, fair):
            fair_probabilities[key] = prob

    return fair_probabilities
