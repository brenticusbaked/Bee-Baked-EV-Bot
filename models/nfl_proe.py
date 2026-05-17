"""NFL PROE and per-play success model for mispriced totals and spreads.

Pass Rate Over Expected (PROE) measures how aggressively a team passes
relative to the league average.  Combined with per-play success metrics
(yards per play, completion efficiency, defensive scoring rate), this
captures coaching intent and schematic advantages before the market
adjusts.

High PROE + high offensive efficiency -> explosive passing -> OVER lean
Low PROE  + strong defense -> ball control, clock drain -> UNDER lean
"""

import logging
from typing import Dict, Optional

from services.http_client import get_json
from utils.thresholds import env_float

logger = logging.getLogger(__name__)

PROE_WEIGHT = env_float("PROE_WEIGHT", 0.20)
NFL_LEAGUE_AVG_PPG = env_float("NFL_LEAGUE_AVG_PPG", 22.5)
NFL_LEAGUE_AVG_PASS_RATE = 0.58
PROE_MIN_SIGNAL = env_float("PROE_MIN_SIGNAL", 0.02)


class PROEContext:
    """Pre-computed PROE and per-play success data for NFL teams."""

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

def _fetch_nfl_standings() -> Dict[str, Dict[str, float]]:
    """Fetch NFL team stats from ESPN standings."""
    stats: Dict[str, Dict[str, float]] = {}

    try:
        url = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
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

                if ppg > 100:
                    ppg = ppg / gp
                    papg = papg / gp

                if ppg <= 0:
                    continue

                off_eff = ppg / NFL_LEAGUE_AVG_PPG
                def_eff = NFL_LEAGUE_AVG_PPG / papg if papg > 0 else 1.0
                proe_proxy = (off_eff - 1.0) * 0.15

                stats[team_name] = {
                    "ppg": ppg,
                    "papg": papg,
                    "offensive_efficiency": off_eff,
                    "defensive_efficiency": def_eff,
                    "proe": proe_proxy,
                    "success_signal": _success_signal(off_eff, def_eff, proe_proxy),
                }
    except Exception as exc:
        logger.warning("Failed to fetch NFL standings: %s", exc)

    return stats


def _fetch_scoreboard_stats() -> Dict[str, Dict[str, float]]:
    """Extract per-team season stats from the ESPN scoreboard if available."""
    stats: Dict[str, Dict[str, float]] = {}

    try:
        url = (
            "https://site.api.espn.com/apis/site/v2/sports/football/"
            "nfl/scoreboard"
        )
        data = get_json(url)

        for event in data.get("events", []):
            for competition in event.get("competitions", []):
                for competitor in competition.get("competitors", []):
                    team = competitor.get("team", {})
                    team_name = team.get("displayName", "")
                    if not team_name or team_name in stats:
                        continue

                    inline: Dict[str, float] = {}
                    for stat in competitor.get("statistics", []):
                        name = stat.get("name", "")
                        try:
                            inline[name] = float(stat.get("displayValue", 0))
                        except (TypeError, ValueError):
                            pass

                    pass_ypg = inline.get(
                        "netPassingYardsPerGame",
                        inline.get("passingYardsPerGame", 0),
                    )
                    rush_ypg = inline.get("rushingYardsPerGame", 0)
                    total_ypg = inline.get(
                        "totalYardsPerGame",
                        pass_ypg + rush_ypg,
                    )

                    if total_ypg > 0 and pass_ypg > 0:
                        pass_rate = pass_ypg / total_ypg
                        proe = pass_rate - NFL_LEAGUE_AVG_PASS_RATE
                        ypp = total_ypg / 65.0
                        off_eff = total_ypg / 340.0

                        stats[team_name] = {
                            "ppg": inline.get("avgPointsFor", 0),
                            "papg": inline.get("avgPointsAgainst", 0),
                            "pass_rate": pass_rate,
                            "proe": proe,
                            "yards_per_play": ypp,
                            "offensive_efficiency": off_eff,
                            "defensive_efficiency": 1.0,
                            "success_signal": _success_signal(off_eff, 1.0, proe),
                        }
    except Exception as exc:
        logger.warning("NFL scoreboard stat fetch failed: %s", exc)

    return stats


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def _success_signal(
    off_eff: float,
    def_eff: float,
    proe: float,
) -> float:
    """Composite signal combining PROE and per-play success.

    Returns a value in [-1, 1]:
      Positive = high-scoring, aggressive scheme -> OVER lean
      Negative = conservative, low-scoring -> UNDER lean
    """
    scheme_component = 0.0
    if abs(proe) >= PROE_MIN_SIGNAL:
        scheme_component = max(-1.0, min(1.0, proe / 0.06))

    efficiency = ((off_eff - 1.0) + (def_eff - 1.0)) / 2
    efficiency_component = max(-1.0, min(1.0, efficiency / 0.15))

    return 0.55 * scheme_component + 0.45 * efficiency_component


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def build_proe_context() -> PROEContext:
    """Build PROE context for NFL teams.

    Tries the scoreboard for detailed per-play stats first, then
    falls back to standings for PPG-based proxy.
    """
    ctx = PROEContext()

    scoreboard_stats = _fetch_scoreboard_stats()
    standings_stats = _fetch_nfl_standings()

    merged = standings_stats.copy()
    merged.update(scoreboard_stats)

    if not merged:
        return ctx

    ctx.teams = merged
    ctx.loaded = True
    logger.info("PROE context built for %d NFL teams", len(ctx.teams))

    return ctx


# ---------------------------------------------------------------------------
# Probability adjustments
# ---------------------------------------------------------------------------

def proe_total_adjustment(
    fair_probability: float,
    home_data: Dict[str, float],
    away_data: Dict[str, float],
    is_over: bool,
) -> float:
    """Adjust game-total probability based on combined PROE/success signals.

    Two aggressive, efficient offenses -> OVER lean.
    Two conservative or inefficient offenses -> UNDER lean.
    """
    combined = (
        home_data["success_signal"] + away_data["success_signal"]
    ) / 2
    max_adj = PROE_WEIGHT * 0.05
    adjustment = combined * max_adj

    if is_over:
        return fair_probability + adjustment
    return fair_probability - adjustment


def proe_spread_adjustment(
    fair_probability: float,
    home_data: Dict[str, float],
    away_data: Dict[str, float],
    is_home_selection: bool,
) -> float:
    """Adjust spread probability based on schematic advantage delta.

    The team with the higher success signal has a structural edge
    that the market may not fully price in.
    """
    delta = home_data["success_signal"] - away_data["success_signal"]
    max_adj = PROE_WEIGHT * 0.03
    adjustment = delta * max_adj

    if is_home_selection:
        return fair_probability + adjustment
    return fair_probability - adjustment
