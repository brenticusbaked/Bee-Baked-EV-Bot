import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from db_manager import get_master_cache
from master_odds_fetcher import run_fetcher
from services.discord_channels import STATUS_WEBHOOK_URL
from services.http_client import post_discord
from unified_bot import scan_markets
from utils.config import env_flag


Cache = Dict[str, List[dict]]


def _parse_int_list(raw: str, default: Iterable[int]) -> List[int]:
    values = []
    for item in str(raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError:
            continue
    return values or list(default)


def _parse_commence_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _matching_window(minutes_to_start: float, windows: Iterable[int], tolerance: int) -> Optional[int]:
    for window in windows:
        if abs(minutes_to_start - window) <= tolerance:
            return window
    return None


def filter_pregame_cache(cache: Cache, windows: Iterable[int], tolerance: int) -> Tuple[Cache, List[dict]]:
    now = datetime.now(timezone.utc)
    filtered: Cache = {}
    matches = []

    for sport, events in (cache or {}).items():
        for event in events or []:
            start = _parse_commence_time(event.get("commence_time"))
            if not start:
                continue
            minutes_to_start = (start - now).total_seconds() / 60.0
            if minutes_to_start < 0:
                continue
            window = _matching_window(minutes_to_start, windows, tolerance)
            if window is None:
                continue
            filtered.setdefault(sport, []).append(event)
            matches.append(
                {
                    "sport": sport,
                    "event_id": event.get("id"),
                    "matchup": f"{event.get('away_team', 'Away')} @ {event.get('home_team', 'Home')}",
                    "window": window,
                    "minutes_to_start": round(minutes_to_start, 1),
                }
            )
    return filtered, matches


def _send_status(matches: List[dict], alert_count: int, refresh_detail: str) -> None:
    if not STATUS_WEBHOOK_URL or not env_flag("PREGAME_STATUS_NOTIFY", False):
        return
    if matches:
        lines = [
            f"`T-{item['window']}` {item['matchup']} ({item['minutes_to_start']:.1f}m)"
            for item in matches[:10]
        ]
        body = "\n".join(lines)
    else:
        body = "No events are inside the configured pregame windows."
    post_discord(
        {
            "embeds": [
                {
                    "description": (
                        "**PREGAME WINDOW SCAN**\n"
                        f"{body}\n\n"
                        f"Alerts sent: {alert_count}\n"
                        f"Refresh: {refresh_detail}"
                    ),
                    "color": 3447003,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ]
        },
        webhook_url=STATUS_WEBHOOK_URL,
    )


def run_pregame_scan() -> dict:
    windows = _parse_int_list(os.getenv("PREGAME_SCAN_WINDOWS", "90,45,15"), [90, 45, 15])
    tolerance = int(os.getenv("PREGAME_SCAN_TOLERANCE_MINUTES", "7"))
    refresh_detail = "cache only"

    if env_flag("PREGAME_REFRESH_ODDS", False):
        result = run_fetcher()
        refresh_detail = result.get("detail", "refresh complete") if isinstance(result, dict) else str(result)

    cache = get_master_cache() or {}
    filtered_cache, matches = filter_pregame_cache(cache, windows, tolerance)
    if not filtered_cache:
        _send_status(matches, 0, refresh_detail)
        return {"detail": "no events in pregame windows", "count": 0, "label": "alerts"}

    result = scan_markets(
        cache_override=filtered_cache,
        source="pregame_scan",
        alert_type="pregame_bet_alert",
    )
    alert_count = int(result.get("count", 0)) if isinstance(result, dict) else 0
    _send_status(matches, alert_count, refresh_detail)
    return {
        "detail": f"pregame scan complete | events={len(matches)} | windows={','.join(map(str, windows))}",
        "count": alert_count,
        "label": "alerts",
    }


if __name__ == "__main__":
    run_pregame_scan()
