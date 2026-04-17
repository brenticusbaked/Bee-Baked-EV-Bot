import os
from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.book_weights import get_book_weights
from utils.odds import decimal_implied_probability, decimal_to_american, quarter_kelly_units
from utils.thresholds import MIN_EV_THRESHOLD, NEAR_MISS_THRESHOLD

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def scan_markets():
    """Scans the master cache for standard market +EV edges."""
    cache = get_master_cache()
    if not cache:
        return {"detail": "cache empty", "count": 0, "label": "alerts"}

    book_weights = get_book_weights()
    alerts = []
    near_misses = []

    for sport, events in cache.items():
        for event in events:
            matchup = f"{event.get('away_team')} @ {event.get('home_team')}"
            # Extract Pinnacle as the sharp reference
            pinnacle = next((b for b in event.get('bookmakers', []) if b['key'] == 'pinnacle'), None)
            if not pinnacle:
                continue

            for market in pinnacle.get('markets', []):
                # Calculate No-Vig Fair Price from Pinnacle
                # Compare against retail books (DraftKings, FanDuel, etc.)
                # If (Retail Odds * Sharp Probability) - 1 > 0.0125: Alert!
                pass 

    return {"detail": "scan complete", "count": len(alerts), "label": "alerts"}
