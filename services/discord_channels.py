import os
from typing import Optional


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


DEFAULT_WEBHOOK_URL = _first_env("DISCORD_WEBHOOK_URL")
BET_ALERTS_WEBHOOK_URL = _first_env("DISCORD_BET_ALERTS_WEBHOOK_URL", "DISCORD_WEBHOOK_URL")
LIVE_HAMMER_WEBHOOK_URL = _first_env("DISCORD_LIVE_HAMMER_WEBHOOK_URL", "DISCORD_BET_ALERTS_WEBHOOK_URL", "DISCORD_WEBHOOK_URL")
WATCHLIST_WEBHOOK_URL = _first_env(
    "DISCORD_WATCHLIST_WEBHOOK_URL",
    "DISCORD_STATUS_WEBHOOK_URL",
    "DISCORD_BET_ALERTS_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
)
MODERATOR_WEBHOOK_URL = _first_env(
    "DISCORD_MODERATOR_WEBHOOK_URL",
    "DISCORD_STATUS_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
)
ARBITRAGE_WEBHOOK_URL = _first_env("DISCORD_ARBITRAGE_WEBHOOK_URL", "DISCORD_BET_ALERTS_WEBHOOK_URL", "DISCORD_WEBHOOK_URL")
# Execution desk edges: looser threshold than a +EV alert, exchange venues
# included, so they get their own lane rather than the bet-alerts channel.
EXECUTION_DESK_WEBHOOK_URL = _first_env(
    "DISCORD_EXECUTION_DESK_WEBHOOK_URL",
    "DISCORD_BET_ALERTS_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
)
# Near-miss digest: edges below the alert threshold, posted when a scan finds
# nothing worth betting.
NEAR_MISS_WEBHOOK_URL = _first_env(
    "DISCORD_NEAR_MISS_WEBHOOK_URL",
    "DISCORD_STATUS_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
)
# MLB home run model output.
HOME_RUNS_WEBHOOK_URL = _first_env(
    "DISCORD_HOME_RUNS_WEBHOOK_URL",
    "DISCORD_MLB_BETS_WEBHOOK_URL",
    "DISCORD_BET_ALERTS_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
)
# Overnight opening-line ("opener") scan: pre-market edges to review before
# placing. Falls back to the bet-alerts channel when no dedicated hook is set.
OPENER_WEBHOOK_URL = _first_env("DISCORD_OPENER_WEBHOOK_URL", "DISCORD_BET_ALERTS_WEBHOOK_URL", "DISCORD_WEBHOOK_URL")
STATUS_WEBHOOK_URL = _first_env("DISCORD_STATUS_WEBHOOK_URL", "DISCORD_WEBHOOK_URL")
RESULTS_WEBHOOK_URL = _first_env(
    "DISCORD_RESULTS_WEBHOOK_URL",
    "DISCORD_DAILY_SLIPS_WEBHOOK_URL",
    "DISCORD_STATUS_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
)
DAILY_SLIPS_WEBHOOK_URL = _first_env("DISCORD_DAILY_SLIPS_WEBHOOK_URL", "DISCORD_RESULTS_WEBHOOK_URL", "DISCORD_STATUS_WEBHOOK_URL", "DISCORD_WEBHOOK_URL")
