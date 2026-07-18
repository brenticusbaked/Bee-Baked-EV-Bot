"""Pure EV/ de-vig helpers used by the /ev Discord slash command."""

import os
from typing import Dict, Optional

from dotenv import load_dotenv

from utils.odds import (
    american_to_decimal,
    decimal_to_american,
    fair_probabilities_from_prices,
)
from utils.kelly import dynamic_kelly_units
from utils.thresholds import env_float

load_dotenv()

COLOR_POSITIVE = 0x2ECC71
COLOR_MARGINAL = 0xF1C40F
COLOR_NEGATIVE = 0xE74C3C


def format_american(american_odds: int) -> str:
    return f"{american_odds:+d}"


def _validate_american(american_odds: int) -> float:
    if american_odds == 0:
        raise ValueError("American odds cannot be zero")
    decimal_odds = american_to_decimal(american_odds)
    if decimal_odds <= 1.0:
        raise ValueError(f"Invalid American odds: {american_odds}")
    return decimal_odds


def compute_ev_response(
    my_odds: int,
    pinnacle_odds_1: int,
    pinnacle_odds_2: int,
    devig_method: Optional[str] = None,
    fire_threshold: Optional[float] = None,
) -> Dict[str, object]:
    if devig_method is None:
        devig_method = os.getenv("DEVIG_METHOD", "power")
    if fire_threshold is None:
        fire_threshold = env_float("EV_COMMAND_FIRE_THRESHOLD", 0.02)

    offered_decimal = _validate_american(my_odds)
    pin1_decimal = _validate_american(pinnacle_odds_1)
    pin2_decimal = _validate_american(pinnacle_odds_2)

    pinnacle_prices = {
        ("side_1", ""): pin1_decimal,
        ("side_2", ""): pin2_decimal,
    }

    fair_probabilities = fair_probabilities_from_prices(pinnacle_prices, method=devig_method)
    true_probability = float(fair_probabilities[("side_1", "")])

    if true_probability <= 0.0 or true_probability >= 1.0:
        raise ValueError("Pinnacle odds do not form a valid two-way market")

    fair_decimal = 1.0 / true_probability
    fair_american = decimal_to_american(fair_decimal)
    ev_pct = (true_probability * offered_decimal) - 1.0
    units = dynamic_kelly_units(ev_pct, offered_decimal, [], [])

    if ev_pct >= fire_threshold:
        recommendation = "Fire"
        color = COLOR_POSITIVE
    elif ev_pct > 0.0:
        recommendation = "Pass / Marginal"
        color = COLOR_MARGINAL
    else:
        recommendation = "Pass"
        color = COLOR_NEGATIVE

    return {
        "offered_decimal": offered_decimal,
        "fair_decimal": fair_decimal,
        "fair_probability": true_probability,
        "fair_american": fair_american,
        "ev_pct": ev_pct,
        "units": units,
        "recommendation": recommendation,
        "color": color,
    }
