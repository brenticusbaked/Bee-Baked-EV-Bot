"""True talent evaluation and bullpen fatigue modeling for MLB matchups.

Combines team-level batting/pitching metrics with starting pitcher skill data
and bullpen workload to produce adjusted win probabilities that isolate
true talent from statistical noise.
"""

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

import requests

from db_manager import load_tracker_state
from utils.thresholds import env_float
from utils.time import get_local_now

logger = logging.getLogger(__name__)

SHARP_WEIGHT = env_float("TALENT_SHARP_WEIGHT", 0.80)
MODEL_WEIGHT = 1.0 - SHARP_WEIGHT
FATIGUE_MAX_ADJUSTMENT = env_float("TALENT_FATIGUE_MAX_ADJ", 0.03)
BULLPEN_LOOKBACK_DAYS = 3
LEAGUE_AVG_OPS = 0.710
LEAGUE_AVG_FIP = 4.00
_MLB_HTTP = requests.Session()
_MLB_HTTP.trust_env = False
_MLB_HTTP.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
)


def _mlb_get_json(url: str, timeout: int = 6):
    response = _MLB_HTTP.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Team stats
# ---------------------------------------------------------------------------

def _fetch_all_team_stats() -> Dict[str, Dict[str, float]]:
    """Fetch season batting and pitching stats for all MLB teams."""
    stats: Dict[str, Dict[str, float]] = {}

    try:
        url = "https://statsapi.mlb.com/api/v1/teams/stats?stats=season&group=hitting&sportIds=1"
        data = _mlb_get_json(url)
        for record in data.get("stats", []):
            for split in record.get("splits", []):
                team_name = split.get("team", {}).get("name", "")
                stat = split.get("stat", {})
                stats.setdefault(team_name, {}).update({
                    "ops": float(stat.get("ops", LEAGUE_AVG_OPS)),
                    "obp": float(stat.get("obp", 0.320)),
                    "slg": float(stat.get("slg", 0.390)),
                })
    except Exception as exc:
        logger.warning("Failed to fetch team batting stats: %s", exc)

    try:
        url = "https://statsapi.mlb.com/api/v1/teams/stats?stats=season&group=pitching&sportIds=1"
        data = _mlb_get_json(url)
        for record in data.get("stats", []):
            for split in record.get("splits", []):
                team_name = split.get("team", {}).get("name", "")
                stat = split.get("stat", {})
                k9 = float(stat.get("strikeoutsPer9Inn", 8.0))
                bb9 = float(stat.get("walksPer9Inn", 3.5))
                stats.setdefault(team_name, {}).update({
                    "era": float(stat.get("era", LEAGUE_AVG_FIP)),
                    "whip": float(stat.get("whip", 1.30)),
                    "k9": k9,
                    "bb9": bb9,
                    "k_bb_pct": (k9 - bb9) / 9.0,
                })
    except Exception as exc:
        logger.warning("Failed to fetch team pitching stats: %s", exc)

    return stats


# ---------------------------------------------------------------------------
# Starting pitcher skill
# ---------------------------------------------------------------------------

def _get_pitcher_fip(
    pitcher_id: Optional[int],
    fip_cache: Dict[str, Any],
) -> Optional[float]:
    """Get a starting pitcher's FIP from cache or a live Stats API lookup."""
    if not pitcher_id:
        return None

    str_id = str(pitcher_id)
    if fip_cache and str_id in fip_cache:
        return fip_cache[str_id].get("fip")

    try:
        url = (
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}"
            "?hydrate=stats(group=[pitching],type=[season])"
        )
        person = _mlb_get_json(url, timeout=4).get("people", [{}])[0]
        splits = person.get("stats", [{}])[0].get("splits", [{}])
        if splits:
            stat = splits[0].get("stat", {})
            k9 = float(stat.get("strikeOutsPer9Inn", 0))
            bb9 = float(stat.get("walksPer9Inn", 0))
            hr9 = float(stat.get("homeRunsPer9", 0))
            return ((13 * hr9) + (3 * bb9) - (2 * k9)) / 9 + 3.20
    except Exception as exc:
        logger.warning("Failed to estimate FIP for pitcher %s: %s", pitcher_id, exc)

    return LEAGUE_AVG_FIP


def _fetch_probable_pitchers() -> Dict[str, Dict[str, Any]]:
    """Fetch today's probable pitchers keyed by 'away @ home' matchup."""
    today = get_local_now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"
    pitchers: Dict[str, Dict[str, Any]] = {}

    try:
        data = _mlb_get_json(url)
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                away = game["teams"]["away"]
                home = game["teams"]["home"]
                away_name = away["team"]["name"]
                home_name = home["team"]["name"]
                key = f"{away_name} @ {home_name}"
                pitchers[key] = {
                    "away_pitcher_id": away.get("probablePitcher", {}).get("id"),
                    "home_pitcher_id": home.get("probablePitcher", {}).get("id"),
                    "away_team": away_name,
                    "home_team": home_name,
                }
    except Exception as exc:
        logger.warning("Failed to fetch probable pitchers: %s", exc)

    return pitchers


# ---------------------------------------------------------------------------
# Bullpen fatigue
# ---------------------------------------------------------------------------

def _fetch_recent_schedule() -> Dict[str, List[Dict[str, int]]]:
    """Fetch completed games in the lookback window grouped by team name."""
    today = get_local_now()
    start = (today - timedelta(days=BULLPEN_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start}&endDate={end}"

    team_games: Dict[str, List[Dict[str, int]]] = {}
    try:
        data = _mlb_get_json(url)
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                status = game.get("status", {}).get("abstractGameCode", "")
                if status != "F":
                    continue
                scheduled = int(game.get("scheduledInnings", 9))
                actual = int(
                    game.get("linescore", {}).get("currentInning", scheduled)
                )
                innings = max(actual, scheduled)
                for side in ("away", "home"):
                    name = game["teams"][side]["team"]["name"]
                    team_games.setdefault(name, []).append({"innings": innings})
    except Exception as exc:
        logger.warning("Failed to fetch recent schedule for fatigue: %s", exc)

    return team_games


def _bullpen_fatigue_score(games: List[Dict[str, int]]) -> float:
    """Score from 0.0 (fresh) to 1.0 (exhausted).

    Considers game count and extra-inning games in the lookback window.
    """
    if not games:
        return 0.0

    game_count = len(games)
    extra_innings = sum(1 for g in games if g.get("innings", 9) > 9)
    base = min(game_count * 0.18, 0.55)
    extra_bonus = min(extra_innings * 0.15, 0.30)
    return min(base + extra_bonus, 1.0)


# ---------------------------------------------------------------------------
# Skill rating and probability
# ---------------------------------------------------------------------------

def _skill_rating(
    team_ops: float,
    opponent_pitcher_fip: Optional[float],
    team_k_bb: float,
) -> float:
    """Composite skill rating centred around 1.0 (league average).

    Components:
      - Batting (40%): team OPS relative to league average
      - Pitching matchup (35%): opponent SP FIP relative to league average
      - Staff quality (25%): team K-BB% contribution
    """
    batting = team_ops / LEAGUE_AVG_OPS if LEAGUE_AVG_OPS > 0 else 1.0

    if opponent_pitcher_fip is not None and opponent_pitcher_fip > 0:
        pitching_adv = LEAGUE_AVG_FIP / opponent_pitcher_fip
    else:
        pitching_adv = 1.0

    pitching_quality = 1.0 + (team_k_bb * 0.5)

    return (batting * 0.40) + (pitching_adv * 0.35) + (pitching_quality * 0.25)


def _log5_probability(home_rating: float, away_rating: float) -> float:
    """Log5 win probability for the home team with home-field advantage."""
    total = home_rating + away_rating
    if total <= 0:
        return 0.5
    home_pct = home_rating / total
    home_pct += 0.03
    return max(0.35, min(0.65, home_pct))


# ---------------------------------------------------------------------------
# Main interface
# ---------------------------------------------------------------------------

class TalentContext:
    """Pre-computed talent data for all MLB matchups in a scan."""

    def __init__(self) -> None:
        self.adjustments: Dict[str, Dict[str, float]] = {}
        self.loaded = False

    def get(self, home_team: str, away_team: str) -> Optional[Dict[str, float]]:
        key = f"{away_team} @ {home_team}"
        adj = self.adjustments.get(key)
        if adj:
            return adj
        home_lower = home_team.strip().lower()
        away_lower = away_team.strip().lower()
        for k, v in self.adjustments.items():
            parts = k.split(" @ ", 1)
            if len(parts) == 2:
                a, h = parts
                if home_lower in h.lower() or h.lower() in home_lower:
                    if away_lower in a.lower() or a.lower() in away_lower:
                        return v
        return None


def build_talent_context() -> TalentContext:
    """Build talent context for all of today's MLB games.

    API calls: team batting (1), team pitching (1), probable pitchers (1),
    recent schedule (1), plus per-pitcher FIP lookups when not cached.
    """
    ctx = TalentContext()

    try:
        team_stats = _fetch_all_team_stats()
        pitchers = _fetch_probable_pitchers()
        fip_cache = load_tracker_state("mlb_fip_cache", "fip_cache.json") or {}
        recent_games = _fetch_recent_schedule()

        for matchup_key, info in pitchers.items():
            away_team = info["away_team"]
            home_team = info["home_team"]

            away_stats = team_stats.get(away_team, {})
            home_stats = team_stats.get(home_team, {})

            away_sp_fip = _get_pitcher_fip(info.get("away_pitcher_id"), fip_cache)
            home_sp_fip = _get_pitcher_fip(info.get("home_pitcher_id"), fip_cache)

            home_rating = _skill_rating(
                team_ops=home_stats.get("ops", LEAGUE_AVG_OPS),
                opponent_pitcher_fip=away_sp_fip,
                team_k_bb=home_stats.get("k_bb_pct", 0.0),
            )
            away_rating = _skill_rating(
                team_ops=away_stats.get("ops", LEAGUE_AVG_OPS),
                opponent_pitcher_fip=home_sp_fip,
                team_k_bb=away_stats.get("k_bb_pct", 0.0),
            )

            model_prob_home = _log5_probability(home_rating, away_rating)

            home_fatigue = _bullpen_fatigue_score(recent_games.get(home_team, []))
            away_fatigue = _bullpen_fatigue_score(recent_games.get(away_team, []))
            fatigue_diff = away_fatigue - home_fatigue
            fatigue_adj = fatigue_diff * FATIGUE_MAX_ADJUSTMENT
            model_prob_home = max(0.35, min(0.65, model_prob_home + fatigue_adj))

            ctx.adjustments[matchup_key] = {
                "model_prob_home": model_prob_home,
                "model_prob_away": 1.0 - model_prob_home,
                "home_rating": home_rating,
                "away_rating": away_rating,
                "home_fatigue": home_fatigue,
                "away_fatigue": away_fatigue,
                "away_sp_fip": away_sp_fip,
                "home_sp_fip": home_sp_fip,
            }

        ctx.loaded = True
        logger.info(
            "Talent context built for %d MLB matchups", len(ctx.adjustments)
        )
    except Exception as exc:
        logger.error("Failed to build talent context: %s", exc)

    return ctx


def adjusted_fair_probability(
    sharp_prob: float,
    model_prob: float,
) -> float:
    """Blend sharp-book probability with model probability.

    Default weighting: 80 % sharp / 20 % model (configurable via
    TALENT_SHARP_WEIGHT env var).
    """
    return (SHARP_WEIGHT * sharp_prob) + (MODEL_WEIGHT * model_prob)
