"""WNBA First Basket Scorer +EV model — cache-only.

First basket is a single-winner market, so every listed player competes for one
outcome. All of Pinnacle's outcomes are de-vigged together as one n-way market
rather than pairwise, then soft-book prices are compared against that baseline.
"""

import os

from models.market_ev import find_edges, publish_edges
from utils.config import env_flag
from utils.seasons import is_sport_in_season
from utils.thresholds import env_float, env_int

SPORT = "basketball_wnba"
ALERT_TYPE = "MODEL_WNBA_FIRST_BASKET"
MODEL_LABEL = "wnba_first_basket_devig"

FIRST_BASKET_MARKETS = (
    "player_first_basket",
    "player_first_field_goal",
    "first_basket_scorer",
)

WNBA_FIRST_BASKET_EV_THRESHOLD = env_float("WNBA_FIRST_BASKET_EV_THRESHOLD", 0.025)
WNBA_FIRST_BASKET_MAX_UNITS = env_float("WNBA_FIRST_BASKET_MAX_UNITS", 0.5)
WNBA_FIRST_BASKET_MAX_ALERTS = env_int("WNBA_FIRST_BASKET_MAX_ALERTS", 5)


def _webhook_url() -> str:
    return os.getenv("DISCORD_WNBA_BETS_WEBHOOK_URL") or os.getenv("DISCORD_BET_ALERTS_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or ""


def run_wnba_first_basket_model(cache: dict | None = None) -> dict:
    if not env_flag("ENABLE_WNBA_FIRST_BASKET_MODEL", True):
        return {"detail": "wnba first basket model disabled", "count": 0, "alerts": []}
    if not is_sport_in_season(SPORT):
        print("[seasons] Skipping wnba_first_basket model (WNBA off-season)")
        return {"detail": "wnba off-season", "count": 0, "alerts": []}

    edges = find_edges(
        sport=SPORT,
        market_keys=FIRST_BASKET_MARKETS,
        ev_threshold=WNBA_FIRST_BASKET_EV_THRESHOLD,
        kelly_cap=WNBA_FIRST_BASKET_MAX_UNITS,
        group_by_player=False,
        cache=cache,
    )
    alerts = publish_edges(
        edges,
        alert_type=ALERT_TYPE,
        model_label=MODEL_LABEL,
        source="model_wnba_first_basket",
        webhook_url=_webhook_url(),
        max_alerts=WNBA_FIRST_BASKET_MAX_ALERTS,
    )
    return {"detail": "wnba first basket model complete", "count": len(alerts), "alerts": alerts}


if __name__ == "__main__":
    print(run_wnba_first_basket_model())
