"""Late-scratch and game-status exception handling for the scanning pipeline."""

from datetime import datetime, timezone
from typing import List, Optional, Tuple


# Game statuses that indicate cancellation or postponement
CANCELLED_STATUSES = {"cancelled", "postponed", "suspended", "canceled"}
STARTED_STATUSES = {"in_progress", "live", "in_play", "started"}


def check_event_status(event: dict) -> Tuple[bool, str]:
    """Return (is_valid, reason) for whether an event should still be scanned."""
    status = str(event.get("status", "")).strip().lower()
    if status in CANCELLED_STATUSES:
        return False, f"event {status}"

    commence_time_str = event.get("commence_time", "")
    if commence_time_str:
        try:
            commence = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if now > commence:
                return False, "event already started"
        except (ValueError, TypeError):
            pass

    return True, "ok"


def filter_valid_events(events: List[dict], sport: str = "") -> List[dict]:
    """Filter out cancelled, postponed, or already-started events."""
    valid = []
    for event in events:
        is_valid, reason = check_event_status(event)
        if is_valid:
            valid.append(event)
        else:
            matchup = f"{event.get('away_team', '?')} @ {event.get('home_team', '?')}"
            print(f"scratch_guard: skipping {sport} {matchup} ({reason})")
    return valid


def validate_bookmaker_outcomes(
    bookmaker: dict,
    min_outcomes: int = 2,
) -> bool:
    """Verify a bookmaker entry has enough outcomes to be usable."""
    for market in bookmaker.get("markets", []):
        outcomes = market.get("outcomes", [])
        if len(outcomes) < min_outcomes:
            return False
        for outcome in outcomes:
            price = outcome.get("price")
            if price is None:
                return False
            try:
                if float(price) <= 1.0:
                    return False
            except (TypeError, ValueError):
                return False
    return True


def safe_parse_commence_time(raw: str) -> Optional[datetime]:
    """Parse commence_time with robust error handling for malformed timestamps."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
