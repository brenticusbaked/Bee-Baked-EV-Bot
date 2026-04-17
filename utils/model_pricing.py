from utils.odds import american_to_decimal, decimal_to_american, quarter_kelly_units


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def fair_decimal_from_probability(probability: float) -> float:
    probability = clamp(probability, 0.01, 0.99)
    return 1.0 / probability


def fair_american_from_probability(probability: float) -> str:
    return decimal_to_american(fair_decimal_from_probability(probability))


def model_edge_from_probability(probability: float, offered_american: str) -> float:
    offered_decimal = american_to_decimal(offered_american)
    return (offered_decimal * probability) - 1.0


def model_units_from_probability(probability: float, offered_american: str, cap: float = 3.0) -> float:
    offered_decimal = american_to_decimal(offered_american)
    edge = model_edge_from_probability(probability, offered_american)
    return quarter_kelly_units(edge, offered_decimal, cap=cap)
