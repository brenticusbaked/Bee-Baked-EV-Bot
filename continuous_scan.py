"""Continuous +EV scan entry point.

Runs the full unified +EV scan against the Supabase-backed market cache
(populated every 10 minutes by the odds-cache-ingest Edge Function). This makes
no Odds API requests of its own — it only reads cached lines — so it can run on
a tight cadence (every 10 minutes, 24/7) without spending credits. Duplicate
alerts are suppressed by the existing per-bet dedupe in the alert pipeline.
"""

import sys

from db_manager import get_market_cache
from services.discord_channels import OPENER_WEBHOOK_URL
from unified_bot import scan_markets

# Header on overnight opener alerts so they're never mistaken for live, place-now
# bets: opening lines are the stalest of the day but frequently move by morning.
OPENER_ALERT_PREFIX = "🌙 **[OPENER — review before placing, line may move]**\n\n"


def run_continuous_scan() -> dict:
    cache = get_market_cache() or {}
    if not cache:
        return {"detail": "cache empty", "count": 0, "label": "alerts"}
    return scan_markets(
        cache_override=cache,
        source="continuous_scan",
        alert_type="bet_alert",
    )


def run_opener_scan() -> dict:
    """Overnight opening-line scan.

    Same +EV math as the continuous scan (Pinnacle baseline, de-vig, Quarter-Kelly
    unchanged), but posts to the opener stream with a review-before-placing header
    and a distinct ``alert_type`` so overnight finds don't dedupe against daytime
    alerts (you'll still get the live alert if the edge survives to game time).
    """
    cache = get_market_cache() or {}
    if not cache:
        return {"detail": "cache empty", "count": 0, "label": "opener_alerts"}
    return scan_markets(
        cache_override=cache,
        source="opener_scan",
        alert_type="opener_alert",
        alert_prefix=OPENER_ALERT_PREFIX,
        webhook_override=OPENER_WEBHOOK_URL,
    )


if __name__ == "__main__":
    if "--opener" in sys.argv:
        print(run_opener_scan())
    else:
        print(run_continuous_scan())
