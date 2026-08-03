import math
from typing import Dict, Iterable, Optional

from utils.odds import decimal_implied_probability, devig_probabilities, quarter_kelly_units


def no_vig_binary_probabilities(over_decimal: float, under_decimal: float, method: str = "power") -> Dict[str, float]:
    implied = [decimal_implied_probability(over_decimal), decimal_implied_probability(under_decimal)]
    fair = devig_probabilities(implied, method=method)
    if len(fair) != 2:
        return {}
    return {"over": fair[0], "under": fair[1]}


def consensus_probabilities(book_pairs: Iterable[dict], method: str = "power") -> Dict[str, float]:
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


def _negative_binomial_size(projected_mean: float, variance_multiplier: float) -> Optional[float]:
    if projected_mean <= 0:
        return None
    if variance_multiplier <= 1.0:
        return None
    return projected_mean / (variance_multiplier - 1.0)


def negative_binomial_pmf(k: int, projected_mean: float, variance_multiplier: float) -> float:
    size = _negative_binomial_size(projected_mean, variance_multiplier)
    if k < 0:
        return 0.0
    if size is None:
        return ((projected_mean ** k) * math.exp(-projected_mean) / math.factorial(k)) if projected_mean > 0 else 0.0

    p = size / (size + projected_mean)
    log_coeff = math.lgamma(k + size) - math.lgamma(size) - math.lgamma(k + 1)
    log_prob = log_coeff + size * math.log(p) + k * math.log1p(-p)
    return math.exp(log_prob)


def negative_binomial_cdf(k: int, projected_mean: float, variance_multiplier: float) -> float:
    if k < 0:
        return 0.0
    if projected_mean <= 0:
        return 1.0
    if variance_multiplier <= 1.0:
        return poisson_cdf(k, projected_mean)
    return min(1.0, sum(negative_binomial_pmf(i, projected_mean, variance_multiplier) for i in range(k + 1)))


def negative_binomial_prop_probabilities(
    line: float,
    projected_mean: Optional[float],
    variance_multiplier: float = 1.35,
) -> Dict[str, float]:
    if projected_mean is None or projected_mean <= 0:
        return {}
    under_cutoff = math.floor(line)
    under_probability = negative_binomial_cdf(under_cutoff, projected_mean, variance_multiplier)
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


def infer_negative_binomial_mean_from_over_probability(
    line: float,
    over_probability: float,
    variance_multiplier: float = 1.35,
    low: float = 0.01,
    high: float = 60.0,
) -> Optional[float]:
    if not 0.0 < over_probability < 1.0:
        return None
    for _ in range(80):
        mid = (low + high) / 2.0
        midpoint_over = negative_binomial_prop_probabilities(line, mid, variance_multiplier).get("over")
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


def conservative_probability(
    fair_probability: float,
    confidence: float,
    z_score: float = 0.35,
    effective_samples: float = 24.0,
) -> float:
    confidence = max(0.0, min(float(confidence), 1.0))
    probability = max(0.0, min(float(fair_probability), 1.0))
    sample_size = max(float(effective_samples) * max(confidence, 0.05), 1.0)
    standard_error = math.sqrt(max(probability * (1.0 - probability), 0.0) / sample_size)
    return max(0.0, min(probability - (z_score * standard_error), 1.0))


def uncertainty_adjusted_prop_kelly_units(
    fair_probability: float,
    offered_decimal: float,
    confidence: float,
    fraction: float = 0.125,
    cap: float = 2.0,
    z_score: float = 0.35,
    effective_samples: float = 24.0,
) -> tuple[float, float, float]:
    adjusted_probability = conservative_probability(
        fair_probability,
        confidence=confidence,
        z_score=z_score,
        effective_samples=effective_samples,
    )
    adjusted_edge = (offered_decimal * adjusted_probability) - 1.0
    return (
        prop_kelly_units(adjusted_edge, offered_decimal, fraction=fraction, cap=cap),
        adjusted_edge,
        adjusted_probability,
    )
