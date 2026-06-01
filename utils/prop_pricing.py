import math
from typing import Dict, Iterable, Optional

from utils.odds import decimal_implied_probability, devig_probabilities, quarter_kelly_units


def no_vig_binary_probabilities(over_decimal: float, under_decimal: float, method: str = "multiplicative") -> Dict[str, float]:
    implied = [decimal_implied_probability(over_decimal), decimal_implied_probability(under_decimal)]
    fair = devig_probabilities(implied, method=method)
    if len(fair) != 2:
        return {}
    return {"over": fair[0], "under": fair[1]}


def consensus_probabilities(book_pairs: Iterable[dict], method: str = "multiplicative") -> Dict[str, float]:
    over_values = []
    under_values = []
    for pair in book_pairs:
        probabilities = no_vig_binary_probabilities(pair["over"]["price"], pair["under"]["price"], method=method)
        if not probabilities:
            continue
        over_values.append(probabilities["over"])
        under_values.append(probabilities["under"])
    if not over_values or not under_values:
        return {}
    over_probability = sum(over_values) / len(over_values)
    under_probability = sum(under_values) / len(under_values)
    total = over_probability + under_probability
    if total <= 0:
        return {}
    return {"over": over_probability / total, "under": under_probability / total}


def poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0
    return sum((lam ** i) * math.exp(-lam) / math.factorial(i) for i in range(k + 1))


def poisson_prop_probabilities(line: float, projected_mean: Optional[float]) -> Dict[str, float]:
    if projected_mean is None or projected_mean <= 0:
        return {}
    under_cutoff = math.floor(line)
    under_probability = poisson_cdf(under_cutoff, projected_mean)
    over_probability = 1.0 - under_probability
    return {"over": over_probability, "under": under_probability}


def infer_mean_from_over_probability(line: float, over_probability: float, low: float = 0.01, high: float = 20.0) -> Optional[float]:
    if not 0.0 < over_probability < 1.0:
        return None
    for _ in range(80):
        mid = (low + high) / 2.0
        midpoint_over = poisson_prop_probabilities(line, mid).get("over")
        if midpoint_over is None:
            return None
        if midpoint_over < over_probability:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def prop_kelly_units(edge: float, offered_decimal: float, fraction: float = 0.125, cap: float = 2.0) -> float:
    quarter_units = quarter_kelly_units(edge, offered_decimal, cap=cap)
    return max(0.0, min(quarter_units * (fraction / 0.25), cap))
