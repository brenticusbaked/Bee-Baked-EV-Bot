"""NFL player prop +EV model — passing yards, receiving yards, anytime TD.

Cache-only. Each player's two-way market (over/under, or yes/no for anytime TD)
is de-vigged against Pinnacle independently, so players sharing a line do not
pollute each other's baseline.
"""

import os

from models.market_ev import find_edges, publish_edges
from utils.config import env_flag
from utils.seasons import is_sport_in_season
from utils.thresholds import env_float, env_int

SPORT = "americanfootball_nfl"
ALERT_TYPE = "MODEL_NFL_PLAYER_PROP"
MODEL_LABEL = "nfl_player_prop_devig"

# Feeds differ on the receiving-yards key, so both spellings are accepted.
NFL_PROP_MARKETS = (
    "player_pass_yds",
    "player_receiving_yds",
    "player_reception_yds",
    "player_anytime_td",
)

NFL_PROP_EV_THRESHOLD = env_float("NFL_PROP_EV_THRESHOLD", 0.02)
NFL_PROP_MAX_UNITS = env_float("NFL_PROP_MAX_UNITS", 1.0)
NFL_PROP_MAX_ALERTS = env_int("NFL_PROP_MAX_ALERTS", 10)


def _webhook_url() -> str:
    return os.getenv("DISCORD_NFL_BETS_WEBHOOK_URL") or os.getenv("DISCORD_BET_ALERTS_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or ""


def run_nfl_player_prop_model(cache: dict | None = None) -> dict:
    if not env_flag("ENABLE_NFL_PLAYER_PROP_MODEL", True):
        return {"detail": "nfl player prop model disabled", "count": 0, "alerts": []}
    if not is_sport_in_season(SPORT):
        print("[seasons] Skipping nfl_player_props model (NFL off-season)")
        return {"detail": "nfl off-season", "count": 0, "alerts": []}

    edges = find_edges(
        sport=SPORT,
        market_keys=NFL_PROP_MARKETS,
        ev_threshold=NFL_PROP_EV_THRESHOLD,
        kelly_cap=NFL_PROP_MAX_UNITS,
        group_by_player=True,
        cache=cache,
    )
    alerts = publish_edges(
        edges,
        alert_type=ALERT_TYPE,
        model_label=MODEL_LABEL,
        source="model_nfl_player_props",
        webhook_url=_webhook_url(),
        max_alerts=NFL_PROP_MAX_ALERTS,
    )
    return {"detail": "nfl player prop model complete", "count": len(alerts), "alerts": alerts}


if __name__ == "__main__":
    print(run_nfl_player_prop_model())
