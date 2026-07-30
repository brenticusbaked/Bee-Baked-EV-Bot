"""Physics-driven and true talent evaluation for MLB matchups.

Combines Statcast Expected Metrics (xBA, xSLG, EV/LA), pitch-level 
data, thermodynamic weather adjustments, and bullpen workload to 
produce adjusted win probabilities.
"""

import logging
from datetime import timedelta, datetime
from typing import Any, Dict, List, Optional
import math

import numpy as np
import pybaseball as pyb
from pybaseball import statcast_batter, statcast_pitcher

from db_manager import load_tracker_state
from services.http_client import get_json as _http_get_json
from utils.thresholds import env_float
from utils.time import get_local_now

logger = logging.getLogger(__name__)

SHARP_WEIGHT = env_float("TALENT_SHARP_WEIGHT", 0.80)
MODEL_WEIGHT = 1.0 - SHARP_WEIGHT
FATIGUE_MAX_ADJUSTMENT = env_float("TALENT_FATIGUE_MAX_ADJ", 0.03)
BULLPEN_LOOKBACK_DAYS = 3 # Retained from original logic
LEAGUE_AVG_XWOBA = 0.315
LEAGUE_AVG_XERA = 4.00

# OpenWeatherMap API Key (add to your .env)
WEATHER_API_KEY = "YOUR_OPENWEATHER_API_KEY"

# Approximate stadium elevations in meters (crucial for air density)
STADIUM_ELEVATIONS = {
    "Colorado Rockies": 1580,
    "Arizona Diamondbacks": 331,
    "Atlanta Braves": 305,
    # Add remaining MLB stadiums here...
}

# ---------------------------------------------------------------------------
# Thermodynamics & Environmental Factors
# ---------------------------------------------------------------------------

def _calculate_air_density(temp_c: float, humidity_pct: float, elevation_m: float) -> float:
    """
    Calculates air density (rho) using ideal gas law modifications.
    Lower density = less drag = higher expected total bases.
    """
    # Standard atmospheric pressure at elevation
    p0 = 101325.0
    pressure_pa = p0 * math.exp(-0.00012 * elevation_m)
    
    # Saturation vapor pressure
    es = 6.1078 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    vapor_pressure = (humidity_pct / 100.0) * es * 100.0 # Convert mb to Pa
    
    dry_air_pressure = pressure_pa - vapor_pressure
    
    temp_k = temp_c + 273.15
    Rd = 287.058 # Gas constant for dry air
    Rv = 461.495 # Gas constant for water vapor
    
    # Air density in kg/m^3
    rho = (dry_air_pressure / (Rd * temp_k)) + (vapor_pressure / (Rv * temp_k))
    return rho

def _get_game_weather(zip_code: str) -> Dict[str, float]:
    """Fetch current/forecasted weather for the stadium zip code."""
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?zip={zip_code},us&units=metric&appid={WEATHER_API_KEY}"
        data = _http_get_json(url)
        return {
            "temp_c": data["main"]["temp"],
            "humidity": data["main"]["humidity"],
        }
    except Exception as exc:
        logger.warning(f"Weather fetch failed: {exc}")
        return {"temp_c": 22.0, "humidity": 50.0} # Assume standard 72F if fail

# ---------------------------------------------------------------------------
# Statcast Advanced Metrics
# ---------------------------------------------------------------------------

def _fetch_team_xstats() -> Dict[str, Dict[str, float]]:
    """Fetch season expected metrics (xBA, xSLG, xwOBA) for all teams."""
    stats: Dict[str, Dict[str, float]] = {}
    try:
        # Pull standard team batting from pybaseball (or savant API directly)
        team_batting = pyb.team_batting_bref(get_local_now().year)
        
        for index, row in team_batting.iterrows():
            team_name = row['Tm']
            stats.setdefault(team_name, {}).update({
                "xwoba": float(row.get("xwOBA", LEAGUE_AVG_XWOBA)),
                "xslg": float(row.get("xSLG", 0.400)),
                "hard_hit_pct": float(row.get("HardHit%", 35.0)),
            })
    except Exception as exc:
        logger.warning("Failed to fetch Statcast team batting stats: %s", exc)

    return stats

def _get_pitcher_xera(pitcher_id: Optional[int]) -> float:
    """Get a starting pitcher's expected ERA (xERA) based on quality of contact."""
    if not pitcher_id:
        return LEAGUE_AVG_XERA

    try:
        # In production, query your local database populated by a daily statcast scrape
        # Pybaseball's statcast_pitcher can fetch this dynamically
        pitcher_data = pyb.statcast_pitcher_expected_stats(get_local_now().year)
        player_row = pitcher_data.loc[pitcher_data['player_id'] == pitcher_id]
        
        if not player_row.empty:
            return float(player_row.iloc[0]['xERA'])
    except Exception as exc:
        logger.warning("Failed to get xERA for pitcher %s: %s", pitcher_id, exc)

    return LEAGUE_AVG_XERA

# ---------------------------------------------------------------------------
# Skill rating and probability (Physics & Statcast Upgraded)
# ---------------------------------------------------------------------------

def _skill_rating(
    team_xwoba: float,
    opponent_pitcher_xera: float,
    air_density: float,
) -> float:
    """Composite skill rating centered around 1.0 (league average).

    Components:
      - Batting (40%): Team expected wOBA relative to league average.
      - Pitching matchup (40%): Opponent SP xERA relative to league average.
      - Environmental (20%): Air density drag modifier on expected slugging.
    """
    batting = team_xwoba / LEAGUE_AVG_XWOBA if LEAGUE_AVG_XWOBA > 0 else 1.0

    if opponent_pitcher_xera > 0:
        pitching_adv = LEAGUE_AVG_XERA / opponent_pitcher_xera
    else:
        pitching_adv = 1.0

    # Baseline sea-level air density is approx 1.225 kg/m^3
    # If density is lower, balls travel further (advantage hitting)
    environmental_modifier = 1.225 / air_density 

    return (batting * 0.40) + (pitching_adv * 0.40) + (environmental_modifier * 0.20)

def _log5_probability(home_rating: float, away_rating: float) -> float:
    """Log5 win probability for the home team."""
    total = home_rating + away_rating
    if total <= 0:
        return 0.5
    home_pct = home_rating / total
    home_pct += 0.03 # Standard home-field baseline
    return max(0.35, min(0.65, home_pct))

# ---------------------------------------------------------------------------
# Main interface
# ---------------------------------------------------------------------------

class TalentContext:
    def __init__(self) -> None:
        self.adjustments: Dict[str, Dict[str, float]] = {}
        self.loaded = False

    def get(self, home_team: str, away_team: str) -> Optional[Dict[str, float]]:
        key = f"{away_team} @ {home_team}"
        return self.adjustments.get(key)

def build_talent_context() -> TalentContext:
    ctx = TalentContext()

    try:
        team_stats = _fetch_team_xstats()
        pitchers = _fetch_probable_pitchers() # Keep original probable pitcher logic
        recent_games = _fetch_recent_schedule() # Keep original bullpen fatigue logic

        for matchup_key, info in pitchers.items():
            away_team = info["away_team"]
            home_team = info["home_team"]

            away_stats = team_stats.get(away_team, {})
            home_stats = team_stats.get(home_team, {})

            away_sp_xera = _get_pitcher_xera(info.get("away_pitcher_id"))
            home_sp_xera = _get_pitcher_xera(info.get("home_pitcher_id"))

            # Thermodynamic modeling for the stadium
            elevation = STADIUM_ELEVATIONS.get(home_team, 100) # default 100m
            weather = _get_game_weather("10451") # Example: requires mapping stadiums to zip codes
            rho = _calculate_air_density(weather["temp_c"], weather["humidity"], elevation)

            home_rating = _skill_rating(
                team_xwoba=home_stats.get("xwoba", LEAGUE_AVG_XWOBA),
                opponent_pitcher_xera=away_sp_xera,
                air_density=rho
            )
            away_rating = _skill_rating(
                team_xwoba=away_stats.get("xwoba", LEAGUE_AVG_XWOBA),
                opponent_pitcher_xera=home_sp_xera,
                air_density=rho
            )

            model_prob_home = _log5_probability(home_rating, away_rating)

            # Original fatigue logic applied
            home_fatigue = _bullpen_fatigue_score(recent_games.get(home_team, []))
            away_fatigue = _bullpen_fatigue_score(recent_games.get(away_team, []))
            fatigue_adj = (away_fatigue - home_fatigue) * FATIGUE_MAX_ADJUSTMENT
            model_prob_home = max(0.35, min(0.65, model_prob_home + fatigue_adj))

            ctx.adjustments[matchup_key] = {
                "model_prob_home": model_prob_home,
                "model_prob_away": 1.0 - model_prob_home,
                "home_rating": home_rating,
                "away_rating": away_rating,
                "home_fatigue": home_fatigue,
                "away_fatigue": away_fatigue,
                "away_sp_xera": away_sp_xera,
                "home_sp_xera": home_sp_xera,
                "stadium_air_density": rho
            }

        ctx.loaded = True
        logger.info("Advanced talent context built for %d MLB matchups", len(ctx.adjustments))
    except Exception as exc:
        logger.error("Failed to build talent context: %s", exc)

    return ctx

def adjusted_fair_probability(sharp_prob: float, model_prob: float) -> float:
    """Blend sharp-book probability with model probability."""
    return (SHARP_WEIGHT * sharp_prob) + (MODEL_WEIGHT * model_prob)