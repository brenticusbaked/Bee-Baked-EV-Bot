from typing import Optional

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
    # Kelly % = Edge / (Odds - 1)
    # 1/4 Kelly = Kelly % / 4
    units = (float(edge) / (float(offered_decimal) - 1.0)) / 4.0 * 100.0
    return max(0.0, min(units, cap))

# ... [Rest of your existing odds.py functions] ...


def profit_for_result(american_odds, units: float, result: str) -> float:
    result = str(result).upper().strip()
    units = float(units)
    if result == "PUSH":
        return 0.0
    if result != "WIN":
        return -units

    odds = float(str(american_odds).replace("+", "").strip())
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
