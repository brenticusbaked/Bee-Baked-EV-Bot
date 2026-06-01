import os
from typing import Optional


def decimal_implied_probability(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability. Returns 0.0 for invalid odds."""
    try:
        decimal_odds = float(decimal_odds)
        if decimal_odds <= 1.0:
            return 0.0
        return 1.0 / decimal_odds
    except (TypeError, ValueError):
        return 0.0


def _group_prices_by_point(prices_by_outcome: dict) -> dict:
    grouped = {}
    for outcome_key, price in prices_by_outcome.items():
        point = outcome_key[1] if isinstance(outcome_key, tuple) and len(outcome_key) > 1 else ""
        try:
            point = str(abs(float(point))) if point not in {"", None} else point
        except (TypeError, ValueError):
            pass
        grouped.setdefault(point, []).append((outcome_key, price))
    return grouped


def multiplicative_unvig(implied_probabilities: list[float]) -> list[float]:
    overround = sum(implied_probabilities)
    if not implied_probabilities:
        return []
    if overround <= 0:
        return [0.0 for _ in implied_probabilities]
    if len(implied_probabilities) == 1:
        return [1.0]
    return [probability / overround for probability in implied_probabilities]


def power_unvig(implied_probabilities: list[float], tol: float = 1e-12, max_iter: int = 100) -> list[float]:
    if not implied_probabilities:
        return []
    if len(implied_probabilities) == 1:
        return [1.0]
    if any(probability <= 0 for probability in implied_probabilities):
        return multiplicative_unvig(implied_probabilities)

    total = sum(implied_probabilities)
    if total <= 0:
        return [0.0 for _ in implied_probabilities]
    if abs(total - 1.0) <= tol:
        return list(implied_probabilities)

    low = 0.01
    high = 10.0
    for _ in range(max_iter):
        mid = (low + high) / 2.0
        powered_total = sum(probability ** mid for probability in implied_probabilities)
        if abs(powered_total - 1.0) <= tol:
            break
        if powered_total > 1.0:
            low = mid
        else:
            high = mid

    exponent = (low + high) / 2.0
    fair = [probability ** exponent for probability in implied_probabilities]
    fair_total = sum(fair)
    if fair_total <= 0:
        return multiplicative_unvig(implied_probabilities)
    return [probability / fair_total for probability in fair]


def devig_probabilities(implied_probabilities: list[float], method: str = "power") -> list[float]:
    method_key = str(method or "power").strip().lower()
    if method_key in {"multiplicative", "mult", "proportional", "normalize"}:
        return multiplicative_unvig(implied_probabilities)
    if method_key in {"shin"}:
        from utils.shin import shin_probabilities
        return shin_probabilities(implied_probabilities)
    return power_unvig(implied_probabilities)


def fair_probabilities_from_prices(prices_by_outcome: dict, method: Optional[str] = None) -> dict:
    method_key = method or os.getenv("DEVIG_METHOD", "power")
    grouped = _group_prices_by_point(prices_by_outcome)

    fair_probabilities = {}
    for outcomes in grouped.values():
        keys = []
        implied = []
        for outcome_key, price in outcomes:
            keys.append(outcome_key)
            implied.append(decimal_implied_probability(price))

        if len(implied) >= 2:
            fair = devig_probabilities(implied, method_key)
        else:
            fair = implied

        for outcome_key, probability in zip(keys, fair):
            fair_probabilities[outcome_key] = probability
    return fair_probabilities


def decimal_to_american(decimal_odds: float) -> str:
    decimal_odds = float(decimal_odds)
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds must be greater than 1.0")
    if decimal_odds >= 2.0:
        return f"+{int(round((decimal_odds - 1) * 100))}"
    return str(int(round(-100 / (decimal_odds - 1))))


def american_to_decimal(american_odds) -> float:
    odds = float(str(american_odds).replace("+", "").strip())
    if odds > 0:
        return (odds / 100.0) + 1.0
    if odds < 0:
        return (100.0 / abs(odds)) + 1.0
    raise ValueError("American odds cannot be zero")


def quarter_kelly_units(edge: float, offered_decimal: float, cap: float = 5.0) -> float:
    """Calculates suggested units based on 1/4 Kelly Criterion."""
    if offered_decimal <= 1.0:
        return 0.0
    if edge <= 0:
        return 0.0
    # Kelly % = Edge / (Odds - 1)
    # 1/4 Kelly = Kelly % / 4
    units = (float(edge) / (float(offered_decimal) - 1.0)) / 4.0 * 100.0
    return max(0.0, min(units, cap))


def profit_for_result(american_odds, units: float, result: str) -> float:
    result = str(result).upper().strip()
    try:
        units = float(units)
    except (TypeError, ValueError):
        units = 0.0
    if result == "PUSH":
        return 0.0
    if result != "WIN":
        return -units
    try:
        odds = float(str(american_odds).replace("+", "").strip())
    except (TypeError, ValueError):
        return 0.0
    if odds > 0:
        return units * (odds / 100.0)
    return units * (100.0 / abs(odds))


def parse_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
