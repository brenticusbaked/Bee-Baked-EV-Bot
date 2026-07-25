"""Sport season calendar for skipping off-season API calls."""

from __future__ import annotations

from datetime import date
from typing import Dict, List


# Approximate season windows (month, day) inclusive.
# Playoffs / postseason are included in these ranges.
# Seasons that cross the new year (e.g. Oct-Jun) are split into two entries.
SEASON_WINDOWS: Dict[str, List[tuple]] = {
    "basketball_nba": [
        (10, 15, 12, 31),                        # mid-Oct through Dec
        (1, 1, 6, 30),                           # Jan through end-Jun (playoffs/Finals)
    ],
    "basketball_wnba": [(5, 1, 10, 31)],        # May through Oct
    "icehockey_nhl": [
        (10, 1, 12, 31),                         # Oct through Dec
        (1, 1, 6, 30),                           # Jan through end-Jun (playoffs/Cup)
    ],
    "baseball_mlb": [(3, 20, 11, 5)],            # late-Mar through early-Nov (World Series)
    "americanfootball_nfl": [
        (7, 15, 12, 31),                         # mid-Jul through Dec (preseason + regular season)
        (1, 1, 2, 15),                           # Jan through mid-Feb (playoffs + Super Bowl)
    ],
}


def is_sport_in_season(sport_key: str, today: date | None = None) -> bool:
    """Return True if the sport is currently in season."""
    today = today or date.today()
    windows = SEASON_WINDOWS.get(sport_key)
    if windows is None:
        return True

    for start_month, start_day, end_month, end_day in windows:
        start = date(today.year, start_month, start_day)
        end = date(today.year, end_month, end_day)
        if start <= today <= end:
            return True

    return False


def filter_in_season(sport_keys: List[str], today: date | None = None) -> List[str]:
    """Return only the sport keys that are currently in season."""
    today = today or date.today()
    return [k for k in sport_keys if is_sport_in_season(k, today)]


def filter_config_in_season(config: Dict[str, str], today: date | None = None) -> Dict[str, str]:
    """Return only the config entries for sports currently in season."""
    today = today or date.today()
    return {k: v for k, v in config.items() if is_sport_in_season(k, today)}
