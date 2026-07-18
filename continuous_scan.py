"""Continuous +EV scan entry point.

Runs the full unified +EV scan against the Supabase-backed market cache
(populated every 10 minutes by the odds-cache-ingest Edge Function). This makes
no Odds API requests of its own — it only reads cached lines — so it can run on
a tight cadence (every 10 minutes, 24/7) without spending credits. Duplicate
alerts are suppressed by the existing per-bet dedupe in the alert pipeline.
"""

from db_manager import get_market_cache
from unified_bot import scan_markets


def run_continuous_scan() -> dict:
    cache = get_market_cache() or {}
    if not cache:
        return {"detail": "cache empty", "count": 0, "label": "alerts"}
    return scan_markets(
        cache_override=cache,
        source="continuous_scan",
        alert_type="bet_alert",
    )


if __name__ == "__main__":
    print(run_continuous_scan())
