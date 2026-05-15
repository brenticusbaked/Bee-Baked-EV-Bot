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


def fair_probabilities_from_prices(prices_by_outcome: dict) -> dict:
    grouped = {}
    for outcome_key, price in prices_by_outcome.items():
        point = outcome_key[1] if isinstance(outcome_key, tuple) and len(outcome_key) > 1 else ""
        try:
            point = str(abs(float(point))) if point not in {"", None} else point
        except (TypeError, ValueError):
            pass
        grouped.setdefault(point, []).append((outcome_key, price))

    fair_probabilities = {}
    for outcomes in grouped.values():
        implied = [
            (outcome_key, decimal_implied_probability(price))
            for outcome_key, price in outcomes
        ]
        overround = sum(prob for _, prob in implied)
        for outcome_key, probability in implied:
            if overround > 0 and len(implied) >= 2:
                fair_probabilities[outcome_key] = probability / overround
            else:
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
