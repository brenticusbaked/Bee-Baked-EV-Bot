"""Late-scratch and game-status exception handling for the scanning pipeline."""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from utils.thresholds import env_float
import os


# Game statuses that indicate cancellation or postponement
CANCELLED_STATUSES = {"cancelled", "postponed", "suspended", "canceled"}
STARTED_STATUSES = {"in_progress", "live", "in_play", "started"}
COMPLETED_STATUSES = {"completed", "final", "closed", "over", "ended"}
# The cache keeps games around for live handling for roughly a few hours after
# scheduled start, so we tolerate that window before calling a game started.
START_GRACE_MINUTES = env_float("SCRATCH_GUARD_START_GRACE_MINUTES", 180.0)
# Scheduled fixtures can drift a bit in the cache or across time zones, but
# anything more than this far past kickoff is considered stale and should not
# be scanned.
SCHEDULE_GRACE_MINUTES = env_float("SCRATCH_GUARD_SCHEDULE_GRACE_MINUTES", 360.0)
TRACE_SKIPS = os.getenv("SCRATCH_GUARD_TRACE", "").strip().lower() in {"1", "true", "yes", "on"}


def check_event_status(event: dict) -> Tuple[bool, str]:
    """Return (is_valid, reason) for whether an event should still be scanned."""
    status = str(event.get("status", "")).strip().lower()
    if status in CANCELLED_STATUSES:
        return False, f"event {status}"
    if status in COMPLETED_STATUSES:
        return False, f"event {status}"

    commence_time_str = event.get("commence_time") or ""
    if not commence_time_str:
        return False, "missing commence_time"

    try:
        # Explicitly enforce UTC awareness on the parsed API string
        commence_utc = datetime.fromisoformat(str(commence_time_str).replace("Z", "+00:00"))
        if commence_utc.tzinfo is None:
            commence_utc = commence_utc.replace(tzinfo=timezone.utc)
        else:
            commence_utc = commence_utc.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return False, "unparseable commence_time"

    now_utc = datetime.now(timezone.utc)
    minutes_past_start = (now_utc - commence_utc).total_seconds() / 60.0

    if status in STARTED_STATUSES:
        if minutes_past_start > START_GRACE_MINUTES:
            return False, f"event {status}"
        return True, "ok"

    # Strict fallback for un-updated API statuses.
    # If the game is scheduled strictly in the past by more than the grace period, skip it.
    if minutes_past_start > SCHEDULE_GRACE_MINUTES:
        return False, "event already started"

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
            if TRACE_SKIPS:
                status = str(event.get("status", "")).strip().lower() or "unknown"
                commence_time = str(event.get("commence_time", "")).strip() or "missing"
                now_utc = datetime.now(timezone.utc).isoformat()
                print(
                    f"scratch_guard: skipping {sport} {matchup} ({reason}) "
                    f"| status={status} | commence_time={commence_time} | now_utc={now_utc}"
                )
            else:
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
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError, AttributeError):
        return None