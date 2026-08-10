"""MLB First 5 Innings (F5) +EV model — lines and totals, cache-only.

Complements ``model_mlb.py``, which prices the F5 moneyline from probable-pitcher
FIP/xERA. This model makes no projection of its own: it de-vigs Pinnacle's F5
markets and flags soft books priced away from that baseline, covering the run
line and total that the pitcher model does not.
"""

import os

from models.market_ev import find_edges, publish_edges
from utils.config import env_flag
from utils.seasons import is_sport_in_season
from utils.thresholds import env_float, env_int

SPORT = "baseball_mlb"
ALERT_TYPE = "MODEL_MLB_F5_MARKET"
MODEL_LABEL = "mlb_f5_market_devig"

F5_MARKETS = ("h2h_1st_5_innings", "spreads_1st_5_innings", "totals_1st_5_innings")

MLB_F5_MARKET_EV_THRESHOLD = env_float("MLB_F5_MARKET_EV_THRESHOLD", 0.015)
MLB_F5_MARKET_MAX_UNITS = env_float("MLB_F5_MARKET_MAX_UNITS", 1.0)
MLB_F5_MARKET_MAX_ALERTS = env_int("MLB_F5_MARKET_MAX_ALERTS", 8)


def _webhook_url() -> str:
    return os.getenv("DISCORD_MLB_BETS_WEBHOOK_URL") or os.getenv("DISCORD_BET_ALERTS_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL") or ""


def run_mlb_f5_model(cache: dict | None = None) -> dict:
    if not env_flag("ENABLE_MLB_F5_MARKET_MODEL", True):
        return {"detail": "mlb f5 market model disabled", "count": 0, "alerts": []}
    if not is_sport_in_season(SPORT):
        print("[seasons] Skipping mlb_f5 market model (MLB off-season)")
        return {"detail": "mlb off-season", "count": 0, "alerts": []}

    edges = find_edges(
        sport=SPORT,
        market_keys=F5_MARKETS,
        ev_threshold=MLB_F5_MARKET_EV_THRESHOLD,
        kelly_cap=MLB_F5_MARKET_MAX_UNITS,
        cache=cache,
    )
    alerts = publish_edges(
        edges,
        alert_type=ALERT_TYPE,
        model_label=MODEL_LABEL,
        source="model_mlb_f5_market",
        webhook_url=_webhook_url(),
        max_alerts=MLB_F5_MARKET_MAX_ALERTS,
    )
    return {"detail": "mlb f5 market model complete", "count": len(alerts), "alerts": alerts}


if __name__ == "__main__":
    print(run_mlb_f5_model())
