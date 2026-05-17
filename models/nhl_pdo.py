"""NHL PDO regression model for identifying mispriced totals.

PDO = team shooting % + team save %.  League average is always ~1.000.
Teams with extreme PDOs are expected to regress toward the mean, creating
opportunities to bet against their recent scoring trends.

High PDO -> team running hot -> expect fewer goals -> UNDER lean
Low PDO  -> team running cold -> expect more goals -> OVER lean
"""

import logging
from typing import Dict, Optional

from services.http_client import get_json
from utils.thresholds import env_float

logger = logging.getLogger(__name__)

LEAGUE_AVG_PDO = 1.000
PDO_REGRESSION_WEIGHT = env_float("PDO_REGRESSION_WEIGHT", 0.20)
PDO_MIN_SIGNAL = env_float("PDO_MIN_SIGNAL", 0.008)


class PDOContext:
    """Pre-computed PDO regression data for NHL teams."""

    def __init__(self) -> None:
        self.teams: Dict[str, Dict[str, float]] = {}
        self.loaded = False

    def get(self, team_name: str) -> Optional[Dict[str, float]]:
        result = self.teams.get(team_name)
        if result:
            return result
        lower = team_name.strip().lower()
        for k, v in self.teams.items():
            if lower in k.lower() or k.lower() in lower:
                return v
        return None


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_nhl_standings() -> list:
    """Fetch NHL standings with shooting/save percentages."""
    try:
        data = get_json("https://api-web.nhle.com/v1/standings/now")
        return data.get("standings", [])
    except Exception as exc:
        logger.warning("Failed to fetch NHL standings for PDO: %s", exc)
        return []


# ---------------------------------------------------------------------------
# PDO calculation
# ---------------------------------------------------------------------------

def _pdo_regression_signal(pdo: float) -> float:
    """Convert PDO deviation into a regression signal in [-1, 1].

    Positive = running hot, expect regression DOWN (fewer goals) -> UNDER
    Negative = running cold, expect regression UP (more goals) -> OVER
    """
    deviation = pdo - LEAGUE_AVG_PDO
    if abs(deviation) < PDO_MIN_SIGNAL:
        return 0.0
    return max(-1.0, min(1.0, deviation / 0.04))


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_pdo_context() -> PDOContext:
    """Build PDO context for all NHL teams from standings data."""
    ctx = PDOContext()

    standings = _fetch_nhl_standings()
    if not standings:
        return ctx

    for team in standings:
        name = team.get("teamName", {}).get("default", "")
        abbrev = team.get("teamAbbrev", {}).get("default", "")
        if not name:
            continue

        shooting_pct = float(team.get("shootingPctg", 0) or 0)
        save_pct = float(team.get("savePctg", 0) or 0)
        gp = float(team.get("gamesPlayed", 1) or 1)
        gf = float(team.get("goalFor", 0) or team.get("goalsFor", 0) or 0)
        ga = float(team.get("goalAgainst", 0) or team.get("goalsAgainst", 0) or 0)

        if shooting_pct > 0 and save_pct > 0:
            pdo = shooting_pct + save_pct
        elif gp > 0 and gf > 0:
            league_avg_gpg = 3.1
            shooting_pct = (gf / gp) / (league_avg_gpg * 10)
            save_pct = 1.0 - ((ga / gp) / (league_avg_gpg * 10))
            pdo = shooting_pct + save_pct
        else:
            continue

        gf_pg = gf / max(gp, 1)
        ga_pg = ga / max(gp, 1)

        ctx.teams[name] = {
            "pdo": pdo,
            "shooting_pct": shooting_pct,
            "save_pct": save_pct,
            "regression_signal": _pdo_regression_signal(pdo),
            "gf_per_game": gf_pg,
            "ga_per_game": ga_pg,
            "abbrev": abbrev,
        }

    ctx.loaded = bool(ctx.teams)
    if ctx.loaded:
        logger.info("PDO context built for %d NHL teams", len(ctx.teams))

    return ctx


# ---------------------------------------------------------------------------
# Probability adjustment
# ---------------------------------------------------------------------------

def pdo_total_adjustment(
    fair_probability: float,
    home_data: Dict[str, float],
    away_data: Dict[str, float],
    is_over: bool,
) -> float:
    """Adjust totals probability based on combined PDO regression signals.

    Two high-PDO teams -> strong UNDER lean (both due to regress down).
    Two low-PDO teams  -> strong OVER lean (both due to regress up).
    """
    combined = (
        home_data["regression_signal"] + away_data["regression_signal"]
    ) / 2
    max_adj = PDO_REGRESSION_WEIGHT * 0.05
    adjustment = combined * max_adj

    if is_over:
        return fair_probability - adjustment
    return fair_probability + adjustment
