"""NBA adjusted lineup pace model for identifying mispriced totals.

Teams with higher pace (possessions per game) create more scoring
opportunities.  When the market total doesn't fully account for a
high-pace or low-pace matchup, this model identifies the mispricing.

High combined pace -> OVER lean
Low combined pace  -> UNDER lean
"""

import logging
from typing import Dict, Optional

from services.http_client import get_json
from utils.thresholds import env_float
from utils.time import get_local_now

logger = logging.getLogger(__name__)

PACE_REGRESSION_WEIGHT = env_float("PACE_REGRESSION_WEIGHT", 0.20)
LEAGUE_AVG_PPG = env_float("LEAGUE_AVG_PPG", 113.0)
PACE_MIN_SIGNAL = env_float("PACE_MIN_SIGNAL", 2.0)


class PaceContext:
    """Pre-computed pace data for NBA teams."""

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

def _fetch_nba_team_stats() -> Dict[str, Dict[str, float]]:
    """Fetch NBA team scoring stats from ESPN standings."""
    stats: Dict[str, Dict[str, float]] = {}

    try:
        url = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
        data = get_json(url)

        for child in data.get("children", []):
            for entry_block in child.get("standings", {}).get("entries", []):
                team = entry_block.get("team", {})
                team_name = team.get("displayName", "")
                if not team_name:
                    continue

                raw: Dict[str, float] = {}
                for stat in entry_block.get("stats", []):
                    stat_name = stat.get("name", "")
                    try:
                        raw[stat_name] = float(stat.get("value", 0))
                    except (TypeError, ValueError):
                        pass

                ppg = raw.get("pointsFor", raw.get("avgPointsFor", 0))
                papg = raw.get("pointsAgainst", raw.get("avgPointsAgainst", 0))
                gp = raw.get("gamesPlayed", 1) or 1

                if ppg > 200:
                    ppg = ppg / gp
                    papg = papg / gp

                if ppg <= 0:
                    continue

                estimated_pace = (ppg + papg) / 2

                stats[team_name] = {
                    "ppg": ppg,
                    "papg": papg,
                    "estimated_pace": estimated_pace,
                    "pace_signal": _pace_signal(estimated_pace),
                }
    except Exception as exc:
        logger.warning("Failed to fetch NBA team stats from standings: %s", exc)

    if not stats:
        stats = _fetch_from_scoreboard()

    return stats


def _fetch_from_scoreboard() -> Dict[str, Dict[str, float]]:
    """Fallback: extract team info from today's scoreboard."""
    stats: Dict[str, Dict[str, float]] = {}
    today = get_local_now().strftime("%Y%m%d")

    try:
        url = (
            "https://site.api.espn.com/apis/site/v2/sports/basketball/"
            f"nba/scoreboard?dates={today}"
        )
        data = get_json(url)

        for event in data.get("events", []):
            for competition in event.get("competitions", []):
                for competitor in competition.get("competitors", []):
                    team = competitor.get("team", {})
                    team_name = team.get("displayName", "")
                    if team_name and team_name not in stats:
                        stats[team_name] = {
                            "ppg": LEAGUE_AVG_PPG,
                            "papg": LEAGUE_AVG_PPG,
                            "estimated_pace": LEAGUE_AVG_PPG,
                            "pace_signal": 0.0,
                        }
    except Exception as exc:
        logger.warning("Scoreboard fallback failed: %s", exc)

    return stats


# ---------------------------------------------------------------------------
# Pace signal
# ---------------------------------------------------------------------------

def _pace_signal(estimated_pace: float) -> float:
    """Convert pace deviation into a signal in [-1, 1].

    Positive = high pace -> OVER lean
    Negative = low pace  -> UNDER lean
    """
    deviation = estimated_pace - LEAGUE_AVG_PPG
    if abs(deviation) < PACE_MIN_SIGNAL:
        return 0.0
    return max(-1.0, min(1.0, deviation / 8.0))


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_pace_context() -> PaceContext:
    """Build pace context for all NBA teams."""
    ctx = PaceContext()

    team_stats = _fetch_nba_team_stats()
    if not team_stats:
        return ctx

    ctx.teams = team_stats
    ctx.loaded = True
    logger.info("Pace context built for %d NBA teams", len(ctx.teams))

    return ctx


# ---------------------------------------------------------------------------
# Probability adjustment
# ---------------------------------------------------------------------------

def pace_total_adjustment(
    fair_probability: float,
    home_data: Dict[str, float],
    away_data: Dict[str, float],
    is_over: bool,
) -> float:
    """Adjust totals probability based on matchup pace signals.

    Two fast teams -> strong OVER lean.
    Two slow teams -> strong UNDER lean.
    """
    combined = (home_data["pace_signal"] + away_data["pace_signal"]) / 2
    max_adj = PACE_REGRESSION_WEIGHT * 0.05
    adjustment = combined * max_adj

    if is_over:
        return fair_probability + adjustment
    return fair_probability - adjustment
